"""The payments surface: adding payments to a check, tipping, and reading them back.

======================  ===================================================
AddPayments             ``POST  /orders/v2/orders/{o}/checks/{c}/payments``
UpdateTip               ``PATCH /orders/v2/orders/{o}/checks/{c}/payments/{p}``
GetPayments             ``GET   /orders/v2/payments?paidBusinessDate|refundBusinessDate|voidBusinessDate``
GetPayment              ``GET   /orders/v2/payments/{guid}``
======================  ===================================================
(apiAddingPaymentsToACheck.html, apiCreatingAnOrderWithPaymentInformation.html,
authorizingCcPayments.html, toast-orders-api.yaml)

Documented behaviour reproduced here
------------------------------------
* "Orders created using the orders API are limited to the CREDIT and OTHER
  payment types"; an OTHER payment names an alternate payment type in
  ``otherPayment.guid`` (from ``/config/v2/alternatePaymentTypes``); "Credit
  card payments must be authorized before you add them" and the order then
  carries ``{"guid": "<paymentUuid>", "type": "CREDIT", ...}``;
* the body is an array of payments and the answer is the Order;
* ``amount`` "excluding tips" and ``tipAmount`` are required; an empty amount
  is the one documented error code, 10025 "Payment amount cannot be empty";
* ``PATCH .../payments/{guid}`` takes ``{"tipAmount"}`` only;
* ``GET /payments`` takes exactly one of the three business-date parameters
  and answers payment guid strings; ``GET /payments/{guid}`` answers the
  Payment; both need ``orders:read``.

JUDGMENT, each labelled
-----------------------
* **CREDIT authorisation** is a seeded record (the credit-cards API is not
  modelled): the request's ``guid`` must name a seeded authorisation not yet
  captured, for at most the authorised amount; the payment takes the
  authorisation's guid, card type, last four digits and
  ``cardEntryMode: PRE_AUTHED`` (a documented enum value);
* **``paymentStatus``** after a successful POST is ``CAPTURED`` for both
  types (audit gap 6: undocumented for OTHER); ``tipAmount`` defaults to 0
  and ``amountTendered`` to ``amount``;
* **a check is PAID** when its payments' amounts cover ``totalAmount``; a
  payment on a PAID check is refused (400), and so is a payment in the SAME
  request array whose earlier elements already cover the check -- validation
  runs against an accumulating view of the whole array (a
  :class:`PaymentBatch`), before any id is drawn or any write happens, so a
  refusal in the third element leaves nothing of the first two;
* the same batch is what makes a CREDIT authorisation single-use *within* one
  array: the second element naming the same authorisation guid is the same
  400 the store-level replay gets, never a write that could collide;
* **the tip PATCH** answers the Order (the specification's declared 200; the
  payment rides inside it), and refuses a voided one (400).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitContext, UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine
from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact
from vendorfake.toast.entities import COL, RestaurantEntity
from vendorfake.toast.errors import CODE_PAYMENT_AMOUNT_EMPTY, TOAST_CODE_INFO_KEY
from vendorfake.toast.machine import CHECK_MACHINE, CheckPaymentStatus
from vendorfake.toast.model.common import validate_body, validate_items
from vendorfake.toast.model.dates import business_date, parse_business_date, parse_rest_date
from vendorfake.toast.model.money import opt_cents, to_cents
from vendorfake.toast.model.order import PaymentRequest, TipRequest, project_payment
from vendorfake.toast.surface.common import RESTAURANT_AUTH, ToastDeps, is_guid, now_ms, require_restaurant

__all__ = [
    "CAPABILITY",
    "CREDIT_NOT_AUTHORIZED",
    "PAYMENT_AMOUNT_EMPTY",
    "UNSUPPORTED_PAYMENT_TYPE",
    "PaymentBatch",
    "ToastPaymentsSurface",
    "add_payment",
    "covered_cents",
    "payment_routes",
    "payments_for",
    "settle_order",
]

CAPABILITY = "payments"

PAYMENT_AMOUNT_EMPTY = "Payment amount cannot be empty"
UNSUPPORTED_PAYMENT_TYPE = "Only OTHER and CREDIT payment types are supported."
CREDIT_NOT_AUTHORIZED = "Credit card payments must be authorized before you add them."
"""Documented phrases."""

_CHECK_MACHINE = StateMachine(CHECK_MACHINE)


@dataclass(slots=True)
class PaymentBatch:
    """What one request's earlier payments have already claimed.

    ``add_payment`` validates one element; this is the memory between
    elements, so the whole array is judged as it will land rather than each
    element against a store none of them has touched yet. One batch per
    validation pass, always -- the id-drawing pass gets a fresh one, so both
    passes see the same world and agree.
    """

    #: CREDIT authorisation guids captured by earlier elements.
    captured: set[str] = dataclass_field(default_factory=set)
    #: Cents earlier elements put on each check, keyed by ``id(check)`` --
    #: the dict object identity, because a rehearsal check has no guid yet.
    covered: dict[int, int] = dataclass_field(default_factory=dict)


class ToastPaymentsSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="POST",
                path="/orders/v2/orders/{guid}/checks/{checkGuid}/payments",
                capability=CAPABILITY,
                handler=self.add_payments,
                auth=RESTAURANT_AUTH,
                scopes=("orders.payments:write",),
                operation_id="CheckPaymentsPost",
                summary="Add OTHER or pre-authorised CREDIT payments to a check; answers the Order.",
            ),
            Route(
                method="PATCH",
                path="/orders/v2/orders/{guid}/checks/{checkGuid}/payments/{paymentGuid}",
                capability=CAPABILITY,
                handler=self.update_tip,
                auth=RESTAURANT_AUTH,
                scopes=("orders.payments:write",),
                operation_id="PaymentTipPatch",
                summary="Set a payment's tipAmount; answers the Order.",
            ),
            Route(
                method="GET",
                path="/orders/v2/payments",
                capability=CAPABILITY,
                handler=self.list_payments,
                auth=RESTAURANT_AUTH,
                scopes=("orders:read",),
                operation_id="PaymentsGet",
                summary="Payment guids for exactly one of paidBusinessDate, refundBusinessDate, voidBusinessDate.",
            ),
            Route(
                method="GET",
                path="/orders/v2/payments/{guid}",
                capability=CAPABILITY,
                handler=self.get_payment,
                auth=RESTAURANT_AUTH,
                scopes=("orders:read",),
                operation_id="PaymentGet",
                summary="One payment by guid.",
            ),
        )

    def add_payments(self, args: HandlerArgs) -> ReplyInit:
        from vendorfake.toast.surface.orders import _assert_not_voided, _check_of, load_order, reply_order

        ctx = args.ctx
        restaurant = require_restaurant(args)
        requests = validate_items(PaymentRequest, args.json(), what="payments")
        order = load_order(args, restaurant, args.params["guid"])
        _assert_not_voided(order)
        check = _check_of(order, args.params["checkGuid"])
        # Every refusal, with no id drawn: a dry run of the WHOLE array
        # against one accumulating batch, then a second pass with a fresh
        # batch that draws the ids.
        rehearsal = PaymentBatch()
        for i, request in enumerate(requests):
            add_payment(ctx, restaurant, order, check, request, field=f"[{i}].", mint=None, batch=rehearsal)
        batch = PaymentBatch()
        docs = [
            add_payment(
                ctx, restaurant, order, check, request, field=f"[{i}].", mint=self._deps.ids.payment, batch=batch
            )
            for i, request in enumerate(requests)
        ]
        for doc in docs:
            ctx.store.collection(COL.payments).insert(doc, {"operation_id": "CheckPaymentsPost"})
        updated = ctx.store.collection(COL.orders).update(
            order["id"], lambda draft: settle_order(draft, ctx), meta={"operation_id": "CheckPaymentsPost"}
        )
        return reply_order(args, updated)

    def update_tip(self, args: HandlerArgs) -> ReplyInit:
        from vendorfake.toast.surface.orders import _assert_not_voided, _check_of, load_order

        ctx = args.ctx
        restaurant = require_restaurant(args)
        request = validate_body(TipRequest, args.body())
        if request.tipAmount is None:
            raise UnitError(UnitErrorKind.MISSING_FIELD, detail="tipAmount is required.", field="tipAmount")
        tip = to_cents(request.tipAmount, field="tipAmount")
        order = load_order(args, restaurant, args.params["guid"])
        _assert_not_voided(order)
        check = _check_of(order, args.params["checkGuid"])
        payment_guid = args.params["paymentGuid"]
        if payment_guid not in check.get("payments", []):
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Payment {payment_guid} was not found on this check.",
                field="paymentGuid",
            )
        stored = ctx.store.collection(COL.payments).require(payment_guid)
        if stored.get("paymentStatus") == "VOIDED":
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION, detail="A voided payment cannot be tipped.", field="paymentGuid"
            )
        now = now_ms(ctx)

        def set_tip(draft: Entity) -> None:
            draft["tipAmount"] = tip
            draft["tip_adjusted"] = True

        def touch(draft: Entity) -> None:
            draft["modifiedDate"] = now
            _check_of(draft, check["guid"])["modifiedDate"] = now
            # The tip is the adjustment PAID waits for; settling again moves
            # the check to CLOSED when nothing else is outstanding.
            settle_order(draft, ctx)

        updated = ctx.store.collection(COL.payments).update(
            payment_guid, set_tip, meta={"operation_id": "PaymentTipPatch"}
        )
        ctx.store.collection(COL.orders).update(order["id"], touch, meta={"operation_id": "PaymentTipPatch"})
        del updated  # the payment rides inside the order the specification answers
        from vendorfake.toast.surface.orders import _project

        # DOCUMENTED: the orders specification declares the tip PATCH's 200
        # as the Order, not the payment. The unit answered the payment until
        # the fidelity validator found it (konyklabs/roadmap#56).
        return json_(_project(args, load_order(args, restaurant, args.params["guid"])))

    def list_payments(self, args: HandlerArgs) -> ReplyInit:
        from vendorfake.toast.surface.orders import _client_id

        restaurant = require_restaurant(args)
        client = _client_id(args)
        given = {name: args.query(name) for name in ("paidBusinessDate", "refundBusinessDate", "voidBusinessDate")}
        present = [name for name, value in given.items() if value is not None]
        if len(present) != 1:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Exactly one of paidBusinessDate, refundBusinessDate or voidBusinessDate is required.",
                field="paidBusinessDate",
            )
        name = present[0]
        wanted = parse_business_date(str(given[name]), field=name)

        def matches(row: Mapping[str, Any]) -> bool:
            if name == "paidBusinessDate":
                return row.get("paidBusinessDate") == wanted
            if name == "voidBusinessDate":
                info = row.get("voidInfo")
                return isinstance(info, Mapping) and info.get("voidBusinessDate") == wanted
            return False  # refunds are not modelled; nothing was refunded on any date

        rows = args.ctx.store.collection(COL.payments).all()
        return json_(
            [
                str(row["id"])
                for row in rows
                if row.get("restaurant_guid") == restaurant.id and row.get("client_id") == client and matches(row)
            ]
        )

    def get_payment(self, args: HandlerArgs) -> ReplyInit:
        from vendorfake.toast.surface.orders import _client_id

        restaurant = require_restaurant(args)
        guid = args.params["guid"]
        if not is_guid(guid):
            raise UnitError(UnitErrorKind.BAD_REQUEST, detail="The GUID was malformed", field="guid")
        stored = args.ctx.store.collection(COL.payments).get(guid)
        if (
            stored is None
            or stored.get("restaurant_guid") != restaurant.id
            or stored.get("client_id") != _client_id(args)
        ):
            # Another client's payment is as absent as none: the same
            # visibility rule load_order enforces for the order itself.
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Payment {guid} was not found.", field="guid")
        return json_(project_payment(stored))


def payment_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastPaymentsSurface(deps).routes()


def covered_cents(ctx: UnitContext, check: Mapping[str, Any]) -> int:
    """What the store's non-voided payments already put on ``check``."""
    guid = check.get("guid")
    if not isinstance(guid, str):
        return 0
    return sum(
        int(row.get("amount", 0))
        for row in ctx.store.collection(COL.payments).all()
        if row.get("checkGuid") == guid and row.get("paymentStatus") != "VOIDED"
    )


def payments_for(ctx: UnitContext, order: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Every payment document the order's checks reference, by guid."""
    collection = ctx.store.collection(COL.payments)
    found: dict[str, Mapping[str, Any]] = {}
    for check in order.get("checks", []):
        for guid in check.get("payments", []):
            stored = collection.get(str(guid))
            if stored is not None:
                found[str(guid)] = stored
    return found


def add_payment(
    ctx: UnitContext,
    restaurant: RestaurantEntity,
    order: Mapping[str, Any],
    check: Mapping[str, Any],
    request: PaymentRequest,
    *,
    field: str,
    mint: Callable[[], str] | None,
    batch: PaymentBatch,
) -> dict[str, Any]:
    """Validate one payment against its check AND against the request's own
    earlier payments, then build its document.

    Nothing is written here: the caller inserts and then settles the order,
    so one request with three payments journals under one operation id. With
    ``mint=None`` nothing is drawn either -- the callers rehearse the whole
    array that way, over one ``batch``, before drawing a single id; a refusal
    anywhere therefore leaves the store, the journal and both id streams
    exactly as they were (konyklabs/roadmap#39 review, B1).
    """
    if check.get("paymentStatus") in (
        CheckPaymentStatus.PAID.value,
        CheckPaymentStatus.CLOSED.value,
        CheckPaymentStatus.VOIDED.value,
    ):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Check {check.get('guid')} is {check.get('paymentStatus')} and takes no further payment.",
            field=f"{field}amount",
        )
    already_covered = covered_cents(ctx, check) + batch.covered.get(id(check), 0)
    total = int(check.get("totalAmount", 0))
    if already_covered >= total and total > 0:
        # JUDGMENT: the earlier elements of this very array already cover the
        # check, so this one would over-pay a check that is about to be PAID --
        # the same refusal a second request after settling would get.
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Check {check.get('guid')} is already covered by the preceding payments and takes no further payment.",
            field=f"{field}amount",
        )
    kind = request.type.upper()
    if kind not in ("OTHER", "CREDIT"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=UNSUPPORTED_PAYMENT_TYPE,
            field=f"{field}type",
            info={"supplied": request.type},
        )
    if request.amount is None or (isinstance(request.amount, str) and not request.amount.strip()):
        raise UnitError(
            UnitErrorKind.MISSING_FIELD,
            detail=PAYMENT_AMOUNT_EMPTY,
            field=f"{field}amount",
            info={TOAST_CODE_INFO_KEY: CODE_PAYMENT_AMOUNT_EMPTY},
        )
    amount = to_cents(request.amount, field=f"{field}amount")
    tip = opt_cents(request.tipAmount, field=f"{field}tipAmount") or 0
    tendered = opt_cents(request.amountTendered, field=f"{field}amountTendered")
    now = now_ms(ctx)
    paid_at = now if request.paidDate is None else parse_rest_date(request.paidDate, field=f"{field}paidDate")
    document: dict[str, Any] = {
        "restaurant_guid": restaurant.id,
        # The documented order visibility rule -- an integration sees only
        # what it submitted -- holds for the order's payments too; stamped so
        # the payment reads can enforce it (vendorfake#30 gate, finding 3).
        "client_id": order.get("client_id"),
        "orderGuid": order.get("id"),
        "checkGuid": check.get("guid"),
        "externalId": request.externalId,
        "paidDate": paid_at,
        "paidBusinessDate": business_date(
            paid_at, time_zone=restaurant.time_zone, closeout_hour=restaurant.closeout_hour
        ),
        "type": kind,
        "amount": amount,
        "tipAmount": tip,
        # Internal (never projected): whether the tip has been set by the
        # caller, at creation or by PATCH. A CREDIT payment's check stays PAID
        # until it has (the documented meaning of PAID); zero is a tip too.
        "tip_adjusted": request.tipAmount is not None,
        "amountTendered": amount if tendered is None else tendered,
        "paymentStatus": "CAPTURED",
        "refundStatus": "NONE",
    }
    batch.covered[id(check)] = batch.covered.get(id(check), 0) + amount
    if kind == "OTHER":
        if request.otherPayment is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="An OTHER payment requires otherPayment.guid.",
                field=f"{field}otherPayment.guid",
            )
        if ctx.store.collection(COL.alternate_payment_types).get(request.otherPayment.guid) is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Alternate payment type {request.otherPayment.guid} was not found; see /config/v2/alternatePaymentTypes.",
                field=f"{field}otherPayment.guid",
            )
        document["otherPayment"] = {"guid": request.otherPayment.guid, "entityType": "AlternatePaymentType"}
        document["id"] = None if mint is None else mint()
    else:
        authorization = (
            None if request.guid is None else ctx.store.collection(COL.credit_authorizations).get(request.guid)
        )
        if authorization is None:
            raise UnitError(UnitErrorKind.INVALID_VALUE, detail=CREDIT_NOT_AUTHORIZED, field=f"{field}guid")
        auth_guid = str(authorization["id"])
        if auth_guid in batch.captured or ctx.store.collection(COL.payments).get(auth_guid) is not None:
            # Captured by the store OR by an earlier element of this array:
            # the same 400 either way, and never a write that could collide.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Authorization {request.guid} was already captured.",
                field=f"{field}guid",
            )
        batch.captured.add(auth_guid)
        authorized = int(authorization.get("amount", 0))
        if amount > authorized:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Payment amount exceeds the authorized amount for {request.guid}.",
                field=f"{field}amount",
                info={"authorized_cents": authorized},
            )
        document["id"] = auth_guid
        document["cardEntryMode"] = str(authorization.get("cardEntryMode", "PRE_AUTHED"))
        document["cardType"] = authorization.get("cardType")
        document["last4Digits"] = authorization.get("last4Digits")
    return compact(document)


def settle_order(draft: Entity, ctx: UnitContext) -> None:
    """Attach every stored payment of this order to its check and move the
    statuses; the order's ``paidDate`` is set when every check is settled.
    Idempotent, so the create path, the append path and the tip path share it.

    DOCUMENTED, from the Check schema's own per-value notes
    (toast-orders-api.yaml): a card charge that cleared while its gratuity
    still awaits adjustment leaves the check ``PAID``; a check with nothing
    left owing is ``CLOSED``. The payment walkthrough
    (https://doc.toasttab.com/doc/devguide/apiCreatingAnOrderWithPaymentInformation.html)
    shows an OTHER payment covering the total answering ``CLOSED``. So a
    covered check is ``PAID`` only while a CREDIT payment on it still awaits
    its tip, and ``CLOSED`` otherwise -- found by the fidelity corpus
    (konyklabs/roadmap#56); the unit answered ``PAID`` for both before."""
    now = now_ms(ctx)
    rows = [row for row in ctx.store.collection(COL.payments).all() if row.get("orderGuid") == draft.get("id")]
    all_paid = True
    for check in draft.get("checks", []):
        mine = [row for row in rows if row.get("checkGuid") == check.get("guid")]
        check["payments"] = [str(row["id"]) for row in mine]
        live = [row for row in mine if row.get("paymentStatus") != "VOIDED"]
        covered = sum(int(row.get("amount", 0)) for row in live)
        awaiting_tip = any(row.get("type") == "CREDIT" and not row.get("tip_adjusted") for row in live)
        current = str(check.get("paymentStatus", CheckPaymentStatus.OPEN.value))
        settled = CheckPaymentStatus.PAID.value if awaiting_tip else CheckPaymentStatus.CLOSED.value
        if current == CheckPaymentStatus.OPEN.value and mine and covered >= int(check.get("totalAmount", 0)):
            _CHECK_MACHINE.assert_transition(current, settled, f"Check {check.get('guid')}")
            check["paymentStatus"] = settled
            check["paidDate"] = now
        elif current == CheckPaymentStatus.PAID.value and not awaiting_tip:
            _CHECK_MACHINE.assert_transition(current, CheckPaymentStatus.CLOSED.value, f"Check {check.get('guid')}")
            check["paymentStatus"] = CheckPaymentStatus.CLOSED.value
        if check.get("paymentStatus") not in (CheckPaymentStatus.PAID.value, CheckPaymentStatus.CLOSED.value):
            all_paid = False
        if mine:
            check["modifiedDate"] = now
    if all_paid and draft.get("checks"):
        draft["paidDate"] = now
    draft["modifiedDate"] = now
