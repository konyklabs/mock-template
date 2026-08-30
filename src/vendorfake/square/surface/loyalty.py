"""The Loyalty surface: find or enrol a buyer by phone, and give them points
for an order.

FOR: the four calls an ordering integration makes around a purchase --
learn the program, look the buyer up by the phone number they typed, enrol
them if they are new, and accumulate points for the order they just paid.

========================  ======================================================
RetrieveLoyaltyProgram    ``GET  /v2/loyalty/programs/{program_id}``
                          https://developer.squareup.com/reference/square/loyalty-api/retrieve-loyalty-program
SearchLoyaltyAccounts     ``POST /v2/loyalty/accounts/search``
                          https://developer.squareup.com/reference/square/loyalty-api/search-loyalty-accounts
CreateLoyaltyAccount      ``POST /v2/loyalty/accounts``
                          https://developer.squareup.com/reference/square/loyalty-api/create-loyalty-account
AccumulateLoyaltyPoints   ``POST /v2/loyalty/accounts/{account_id}/accumulate``
                          https://developer.squareup.com/reference/square/loyalty-api/accumulate-loyalty-points
========================  ======================================================

INVARIANT: **points are a ledger.** An accumulation inserts a ``LoyaltyEvent``
and then updates the account's ``balance`` and ``lifetime_points`` by the
event's ``points``, both journalled; the balance is never written except by
an event, so the events sum to the balance on every account. That is the
property a consumer reconciling points against orders depends on.

Points from an order
--------------------
The seeded program has one ``SPEND`` accrual rule -- "buyers earn ``points``
for every ``amount_money`` spent" -- so an order earns
``(eligible total // spend amount) * points``, integer division, nothing for
the remainder. ``tax_mode`` is ``BEFORE_TAX`` and this unit models no tax, so
the eligible total is the order total. JUDGMENT, three times:

* the numbers in the rule are the scenario's, not Square's;
* an order that earns **no points** is refused with ``bad_request`` rather
  than answered with an empty ``events`` array, so a consumer's loyalty step
  fails loudly on a sub-threshold order. Square documents the response for
  a qualifying purchase and not this case; NOT VERIFIED;
* an order accrues **once**. A second accumulation naming the same order is
  refused, because paying a buyer twice for one purchase is the mistake a
  retry without an idempotency key would make. Square's page says points are
  calculated from the order; that it refuses a repeat is NOT VERIFIED.

The order must exist and be neither DRAFT nor CANCELED -- a purchase that was
never paid for earns nothing -- and OPEN is accepted alongside COMPLETED,
because the consumer flow accumulates in the same breath as the payment and
this unit does not insist on an ordering between two calls Square does not
order either.

SHRINK (prototype): one program, one SPEND rule, no rewards, no promotions,
no point expiry, no ``ListLoyaltyPrograms`` (deprecated in favour of ``main``),
``RetrieveLoyaltyAccount``, ``AdjustLoyaltyPoints`` or ``SearchLoyaltyEvents``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    IdempotencySpec,
    PaginationSpec,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.state.store import Collection, Entity
from vendorfake.core.util.json import compact
from vendorfake.square.entities import (
    COL,
    LoyaltyAccountEntity,
    LoyaltyEventEntity,
    LoyaltyProgramEntity,
    OrderEntity,
)
from vendorfake.square.machine import OrderState
from vendorfake.square.model.common import validate_body
from vendorfake.square.model.loyalty import (
    ACCUMULATE_POINTS,
    E164,
    MAIN_PROGRAM,
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
    AccumulateLoyaltyPointsRequest,
    CreateLoyaltyAccountRequest,
    SearchLoyaltyAccountsRequest,
    project_loyalty_account,
    project_loyalty_event,
    project_loyalty_program,
)
from vendorfake.square.model.order import order_total
from vendorfake.square.surface.common import SquareDeps

__all__ = ["CAPABILITY", "LoyaltySurface", "loyalty_routes", "points_for_order"]

CAPABILITY = "loyalty"
"""The capability every route below belongs to."""


class LoyaltySurface:
    """The four Loyalty routes, bound to one vendor's id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        """``/v2/loyalty/accounts/search`` (four segments) before
        ``/v2/loyalty/accounts/{account_id}/accumulate`` (five): different
        lengths, so neither can shadow the other, and listed in the order a
        consumer calls them."""
        return (
            Route(
                method="GET",
                path="/v2/loyalty/programs/{program_id}",
                capability=CAPABILITY,
                handler=self.retrieve_program,
                auth="bearer",
                scopes=("LOYALTY_READ",),
                operation_id="RetrieveLoyaltyProgram",
                summary="The seller's loyalty program, by id or as `main`.",
            ),
            Route(
                method="POST",
                path="/v2/loyalty/accounts/search",
                capability=CAPABILITY,
                handler=self.search_accounts,
                auth="bearer",
                scopes=("LOYALTY_READ",),
                operation_id="SearchLoyaltyAccounts",
                summary="Accounts by phone-number mapping or by customer id.",
                pagination=PaginationSpec(style="cursor", where="body", items_path="loyalty_accounts"),
            ),
            Route(
                method="POST",
                path="/v2/loyalty/accounts",
                capability=CAPABILITY,
                handler=self.create_account,
                auth="bearer",
                scopes=("LOYALTY_WRITE",),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="loyalty.accounts.create", required=True),
                operation_id="CreateLoyaltyAccount",
                summary="Enrol a buyer by E.164 phone number.",
            ),
            Route(
                method="POST",
                path="/v2/loyalty/accounts/{account_id}/accumulate",
                capability=CAPABILITY,
                handler=self.accumulate_points,
                auth="bearer",
                scopes=("LOYALTY_WRITE",),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="loyalty.accumulate", required=True),
                operation_id="AccumulateLoyaltyPoints",
                summary="Add points for an order (computed from the accrual rule) or a stated amount.",
            ),
        )

    # -- GET /v2/loyalty/programs/{program_id} ------------------------------

    def retrieve_program(self, args: HandlerArgs) -> ReplyInit:
        program = _require_program(args.ctx, args.params["program_id"])
        return json_({"program": project_loyalty_program(program)})

    # -- POST /v2/loyalty/accounts/search -----------------------------------

    def search_accounts(self, args: HandlerArgs) -> ReplyInit:
        """By phone-number mapping or by customer id, never both.

        "This cannot be combined with `customer_ids`" is Square's sentence on
        ``mappings`` and its mirror is on ``customer_ids``, so a query naming
        both is ``invalid_value`` rather than an intersection or a union
        nobody documented. A query naming neither returns every account,
        which is what an empty filter means on every other search here.
        """
        body = args.body()
        request = validate_body(SearchLoyaltyAccountsRequest, body)
        query = request.query
        phones: set[str] = set()
        customers: set[str] = set()
        if query is not None:
            if query.mappings and query.customer_ids:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail="query.mappings cannot be combined with query.customer_ids.",
                    field="query.mappings",
                )
            for index, mapping in enumerate(query.mappings or []):
                if not mapping.phone_number:
                    raise UnitError(
                        UnitErrorKind.MISSING_FIELD,
                        detail="Each mapping must carry a phone_number.",
                        field=f"query.mappings[{index}].phone_number",
                    )
                phones.add(mapping.phone_number)
            customers = set(query.customer_ids or [])
        if request.limit is not None and not (1 <= request.limit <= SEARCH_MAX_LIMIT):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"limit must be between 1 and {SEARCH_MAX_LIMIT}.",
                field="limit",
            )

        collection = args.ctx.store.collection(COL.loyalty_accounts)
        accounts = [
            entity
            for entity in collection.all()
            if (not phones or entity.get("phone_number") in phones)
            and (not customers or entity.get("customer_id") in customers)
        ]
        fingerprint = {name: value for name, value in body.items() if name not in ("cursor", "limit")}
        page = collection.paginate(
            accounts,
            limit=request.limit,
            cursor=request.cursor,
            fingerprint=fingerprint,
            default_limit=SEARCH_DEFAULT_LIMIT,
            max_limit=SEARCH_MAX_LIMIT,
        )
        return json_(
            compact(
                {
                    "loyalty_accounts": [project_loyalty_account(entity) for entity in page.items],
                    "cursor": page.cursor,
                }
            )
        )

    # -- POST /v2/loyalty/accounts ------------------------------------------

    def create_account(self, args: HandlerArgs) -> ReplyInit:
        """Enrol a buyer.

        The program must be the seller's; the phone number must be E.164; and
        the number must not already be enrolled. That last refusal is
        ``conflict`` -- a 409 -- JUDGMENT and NOT VERIFIED: Square's page
        documents the success shape and not the duplicate, and a 409 is the
        answer that tells the consumer's "search, then create" flow exactly
        which half it skipped.

        A customer is minted for the account when none is named, because
        Square's account carries a ``customer_id`` and "creates a customer
        profile if one does not exist" -- this unit has no Customers surface,
        so the id is minted and stored and nothing else knows it. JUDGMENT.
        """
        request = validate_body(CreateLoyaltyAccountRequest, args.body())
        spec = request.loyalty_account
        program = LoyaltyProgramEntity.from_entity(_require_program(args.ctx, spec.program_id))
        if spec.mapping is None or not spec.mapping.phone_number:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="loyalty_account.mapping.phone_number is required.",
                field="loyalty_account.mapping.phone_number",
            )
        phone = spec.mapping.phone_number
        _require_e164(phone, "loyalty_account.mapping.phone_number")
        accounts = args.ctx.store.collection(COL.loyalty_accounts)
        existing = accounts.find(lambda entity: entity.get("phone_number") == phone)
        if existing is not None:
            raise UnitError(
                UnitErrorKind.CONFLICT,
                detail=f"A loyalty account already exists for {phone}.",
                field="loyalty_account.mapping.phone_number",
                info={"loyalty_account_id": str(existing["id"])},
            )
        now = args.ctx.clock.iso_ms()
        entity = LoyaltyAccountEntity(
            id=self._deps.ids.uuid(),
            program_id=program.id,
            customer_id=spec.customer_id or self._deps.ids.customer(),
            phone_number=phone,
            mapping_id=self._deps.ids.uuid(),
            balance=0,
            lifetime_points=0,
            enrolled_at=now,
            mapping_created_at=now,
        ).to_entity()
        stored = accounts.insert(entity, {"operation_id": "CreateLoyaltyAccount"})
        return json_({"loyalty_account": project_loyalty_account(stored)})

    # -- POST /v2/loyalty/accounts/{account_id}/accumulate ------------------

    def accumulate_points(self, args: HandlerArgs) -> ReplyInit:
        """Add points, from an order through the accrual rule or as stated.

        "Either `order_id` or `points` is required" -- exactly one. The
        response is ``events``: "The resulting loyalty events. If the purchase
        qualifies for points, the ACCUMULATE_POINTS event is always included."
        The deprecated singular ``event`` is not emitted.
        """
        ctx = args.ctx
        request = validate_body(AccumulateLoyaltyPointsRequest, args.body())
        accounts = ctx.store.collection(COL.loyalty_accounts)
        stored = accounts.get(args.params["account_id"])
        if stored is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Loyalty account {args.params['account_id']} was not found.",
                field="account_id",
            )
        account = LoyaltyAccountEntity.from_entity(stored)
        program = LoyaltyProgramEntity.from_entity(_require_program(ctx, account.program_id))
        if ctx.store.collection(COL.locations).get(request.location_id) is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Location {request.location_id} does not exist for this merchant.",
                field="location_id",
            )

        spec = request.accumulate_points
        if (spec.order_id is None) == (spec.points is None):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Either accumulate_points.order_id or accumulate_points.points is required, and not both.",
                field="accumulate_points",
            )
        events = ctx.store.collection(COL.loyalty_events)
        if spec.order_id is not None:
            order = _accruable_order(ctx, spec.order_id)
            if events.find(lambda entity: entity.get("order_id") == order.id) is not None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"Order {order.id} has already accumulated points.",
                    field="accumulate_points.order_id",
                    info={"order_id": order.id},
                )
            points = points_for_order(program, order)
            if points <= 0:
                raise UnitError(
                    UnitErrorKind.BAD_REQUEST,
                    detail=(
                        f"Order {order.id} does not qualify for points: its total {order_total(order)} is below "
                        f"the {program.spend_amount.amount} the accrual rule requires."
                    ),
                    field="accumulate_points.order_id",
                    info={"order_total": order_total(order), "spend_amount": program.spend_amount.amount},
                )
        else:
            points = spec.points or 0
            if points <= 0:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail="accumulate_points.points must be positive.",
                    field="accumulate_points.points",
                )

        event = events.insert(
            LoyaltyEventEntity(
                id=self._deps.ids.uuid(),
                type=ACCUMULATE_POINTS,
                account_id=account.id,
                program_id=program.id,
                location_id=request.location_id,
                points=points,
                order_id=spec.order_id,
            ).to_entity(),
            {"operation_id": "AccumulateLoyaltyPoints"},
        )

        def credit(draft: Entity) -> None:
            draft["balance"] = int(draft.get("balance", 0)) + points
            draft["lifetime_points"] = int(draft.get("lifetime_points", 0)) + points

        accounts.update(account.id, credit, meta={"operation_id": "AccumulateLoyaltyPoints"})
        return json_({"events": [project_loyalty_event(event)]})


def loyalty_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The Loyalty routes for one vendor."""
    return LoyaltySurface(deps).routes()


def points_for_order(program: LoyaltyProgramEntity, order: OrderEntity) -> int:
    """The SPEND rule: whole multiples of the spend amount, times the points.

    Integer division, so a remainder earns nothing -- "for every $1 spent"
    means the dollar has to be spent in full. See the module docstring for
    what is JUDGMENT here.
    """
    spend = program.spend_amount.amount
    if spend <= 0:
        return 0
    return (order_total(order) // spend) * program.accrual_points


def _require_program(ctx: UnitContext, program_id: str) -> Mapping[str, Any]:
    """The program, resolving ``main`` to the seller's one program."""
    programs: Collection = ctx.store.collection(COL.loyalty_programs)
    if program_id == MAIN_PROGRAM:
        everything = programs.all()
        if not everything:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail="This seller has no loyalty program.",
                field="program_id",
            )
        return everything[0]
    stored = programs.get(program_id)
    if stored is None:
        raise UnitError(
            UnitErrorKind.NOT_FOUND,
            detail=f"Loyalty program {program_id} was not found.",
            field="program_id",
        )
    return stored


def _require_e164(phone: str, field: str) -> None:
    if E164.match(phone) is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be in E.164 format, for example +14155551111.",
            field=field,
            info={"supplied": phone},
        )


def _accruable_order(ctx: UnitContext, order_id: str) -> OrderEntity:
    stored = ctx.store.collection(COL.orders).get(order_id)
    if stored is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Order {order_id} does not exist.",
            field="accumulate_points.order_id",
        )
    order = OrderEntity.from_entity(stored)
    if order.state in (OrderState.DRAFT.value, OrderState.CANCELED.value):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Order {order_id} is {order.state} and cannot accumulate points.",
            field="accumulate_points.order_id",
            info={"state": order.state},
        )
    return order
