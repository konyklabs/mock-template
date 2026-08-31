"""Two units driven identically an hour apart digest alike, over every write path.

The hole this closes (konyklabs/roadmap#39 review, B3): Toast nests every
check and selection stamp inside the order entity, and before the #35 chassis
the volatile scrub was top-level only -- so two units driven identically
milliseconds apart diverged on ``checks[0].createdDate`` while the seed-only
digest pin stayed green and hid it. The scrub now matches volatile names at
any depth and keeps their presence, and this suite is what keeps that from
regressing silently: one scenario per write path, two units, an hour of
virtual clock between them.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.unit.toast.harness import Harness, harness
from tests.unit.toast.test_surface_orders import order_body
from vendorfake.toast.seed import constants as c

HOUR_MS = 60 * 60 * 1000

OTHER = {"type": "OTHER", "amount": 9.55, "tipAmount": 1.0, "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID}}


def _created(h: Harness) -> tuple[str, str]:
    order = h.post("/orders/v2/orders", order_body()).json()
    return order["guid"], order["checks"][0]["guid"]


def drive_login(h: Harness) -> None:
    from tests.unit.toast.test_surface_auth import LOGIN, LOGIN_PATH

    assert h.api.post(LOGIN_PATH, LOGIN).status == 200


def drive_order_with_modifiers(h: Harness) -> None:
    burger = {
        "item": {"guid": c.ITEM_BURGER_GUID},
        "quantity": 2,
        "modifiers": [
            {
                "item": {"guid": c.MODIFIER_OPTION_SALAD_GUID},
                "quantity": 1,
                "preModifier": {"guid": c.PRE_MODIFIER_EXTRA_GUID},
            }
        ],
    }
    body = order_body(burger, table={"guid": c.TABLE_1_GUID}, numberOfGuests=2)
    body["checks"][0]["customer"] = {"firstName": "Ada", "lastName": "Lovelace"}
    assert h.post("/orders/v2/orders", body).status == 200


def drive_selections_append(h: Harness) -> None:
    guid, check = _created(h)
    appended = h.post(
        f"/orders/v2/orders/{guid}/checks/{check}/selections",
        [{"item": {"guid": c.ITEM_LEMONADE_GUID}, "quantity": 1}],
    )
    assert appended.status == 200


def drive_pay_other(h: Harness) -> None:
    guid, check = _created(h)
    assert h.post(f"/orders/v2/orders/{guid}/checks/{check}/payments", [OTHER]).status == 200


def drive_pay_credit_and_tip(h: Harness) -> None:
    guid, check = _created(h)
    paid = h.post(
        f"/orders/v2/orders/{guid}/checks/{check}/payments",
        [{"type": "CREDIT", "guid": c.CREDIT_AUTHORIZATION_GUID, "amount": 9.55}],
    )
    assert paid.status == 200
    tipped = h.patch(
        f"/orders/v2/orders/{guid}/checks/{check}/payments/{c.CREDIT_AUTHORIZATION_GUID}", {"tipAmount": 2.0}
    )
    assert tipped.status == 200


def drive_void(h: Harness) -> None:
    guid, check = _created(h)
    assert h.post(f"/orders/v2/orders/{guid}/checks/{check}/payments", [OTHER]).status == 200
    voided = h.post(f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": True}})
    assert voided.status == 200


def drive_check_discount(h: Harness) -> None:
    guid, check = _created(h)
    applied = h.post(
        f"/orders/v2/orders/{guid}/checks/{check}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_REGULARS_GUID}}],
    )
    assert applied.status == 200


def drive_selection_discount(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    selection = order["checks"][0]["selections"][0]["guid"]
    applied = h.post(
        f"/orders/v2/orders/{order['guid']}/checks/{order['checks'][0]['guid']}/selections/{selection}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_SOUP_GUID}, "appliedPromoCode": "SOUP"}],
    )
    assert applied.status == 200


def drive_delivery_info(h: Harness) -> None:
    body = order_body(deliveryInfo={"address1": "1 Main St"})
    body["diningOption"] = {"guid": c.DINING_OPTION_TAKE_OUT_GUID}
    order = h.post("/orders/v2/orders", body).json()
    assert h.patch(f"/orders/v2/orders/{order['guid']}/deliveryInfo", {"notes": "ring twice"}).status == 200


def drive_stock_update(h: Harness) -> None:
    updated = h.put(
        "/stock/v1/inventory/update",
        [
            {"guid": c.ITEM_SOUP_GUID, "status": "OUT_OF_STOCK"},
            {"guid": c.ITEM_BURGER_GUID, "status": "QUANTITY", "quantity": 4.0},
        ],
    )
    assert updated.status == 200


def drive_subscription(h: Harness) -> None:
    registered = h.api.post(
        "/__toast/webhooks/subscriptions", {"url": "https://example.test/hooks", "eventCategories": ["stock"]}
    )
    assert registered.status == 201


DRIVES: dict[str, Callable[[Harness], None]] = {
    "login": drive_login,
    "order-with-modifiers": drive_order_with_modifiers,
    "selections-append": drive_selections_append,
    "pay-other": drive_pay_other,
    "pay-credit-and-tip": drive_pay_credit_and_tip,
    "void": drive_void,
    "check-discount": drive_check_discount,
    "selection-discount": drive_selection_discount,
    "delivery-info": drive_delivery_info,
    "stock-update": drive_stock_update,
    "subscription": drive_subscription,
}


@pytest.mark.parametrize("name", sorted(DRIVES))
def test_two_units_driven_alike_an_hour_apart_digest_alike(name: str) -> None:
    digests = []
    for advance in (0, HOUR_MS):
        for h in harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
            if advance:
                assert h.api.post("/__unit/clock/advance", {"ms": advance}).status == 200
            DRIVES[name](h)
            digests.append(h.unit.context.store.entity_digest())
    assert digests[0] == digests[1], name


def test_a_void_moves_the_digest_by_its_marks_alone() -> None:
    """The other half of the volatile bargain: ``voidDate`` is scrubbed as an
    instant but its presence is kept, so a void that only stamps dates still
    changes the digest."""
    for h in harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        store = h.unit.context.store
        guid, _check = _created(h)
        before = store.entity_digest()
        voided = h.post(
            f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": True}}
        )
        assert voided.status == 200
        assert store.entity_digest() != before


def test_a_nested_stamp_alone_does_not_move_the_digest() -> None:
    """Directly at the hole B3 named: rewriting ``checks[0].modifiedDate`` in
    place changes no digest, because the name is volatile at any depth."""
    for h in harness("full", env={"VENDORFAKE_CLOCK": "virtual"}):
        store = h.unit.context.store
        guid, _ = _created(h)
        before = store.entity_digest()
        raw = store.raw("orders")[guid]
        raw["checks"][0]["modifiedDate"] = raw["checks"][0]["modifiedDate"] + 12345
        raw["checks"][0]["selections"][0]["createdDate"] = 1
        assert store.entity_digest() == before
        raw["checks"][0]["selections"][0]["quantity"] = 9.0  # real state does move it
        assert store.entity_digest() != before
