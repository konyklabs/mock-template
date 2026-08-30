"""The Loyalty surface: the program, search-or-enrol by phone, and points.

https://developer.squareup.com/reference/square/loyalty-api
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import (
    SEED_KIOSK_LOCATION_ID,
    SEED_LOCATION_ID,
    SEED_LOYALTY_ACCOUNT_ID,
    SEED_LOYALTY_ACCOUNT_PHONE,
    SEED_LOYALTY_CUSTOMER_ID,
    SEED_LOYALTY_PROGRAM_ID,
    SEED_LOYALTY_REWARD_TIER_ID,
    SEED_OPEN_ORDER_ID,
)


@pytest.fixture
def h() -> Iterator[Harness]:
    """On a virtual clock: `enrolled_at` is stamped in the handler and
    `created_at` by `Collection.insert`, two reads that a real clock can
    separate by a millisecond."""
    yield from build_harness("full", env={"VENDORFAKE_CLOCK": "virtual"})


def search(h: Harness, **body: Any) -> Any:
    return h.api.post("/v2/loyalty/accounts/search", body, headers=h.auth)


def enrol(h: Harness, phone: str, key: str = "enrol-1", **spec: Any) -> Any:
    return h.api.post(
        "/v2/loyalty/accounts",
        {
            "idempotency_key": key,
            "loyalty_account": {"program_id": SEED_LOYALTY_PROGRAM_ID, "mapping": {"phone_number": phone}, **spec},
        },
        headers=h.auth,
    )


def accumulate(
    h: Harness, account_id: str, key: str = "acc-1", location_id: str = SEED_LOCATION_ID, **points: Any
) -> Any:
    return h.api.post(
        f"/v2/loyalty/accounts/{account_id}/accumulate",
        {"idempotency_key": key, "location_id": location_id, "accumulate_points": points},
        headers=h.auth,
    )


def create_order(h: Harness, amount: int, key: str = "loy-order") -> str:
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": key,
            "order": {
                "location_id": SEED_LOCATION_ID,
                "line_items": [{"quantity": "1", "base_price_money": {"amount": amount}}],
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return str(response.json()["order"]["id"])


def account(h: Harness, account_id: str) -> dict[str, Any]:
    """Read one account back through the search, which is the only read."""
    (found,) = [row for row in search(h).json()["loyalty_accounts"] if row["id"] == account_id]
    return dict(found)


# ---------------------------------------------------------------------------
# RetrieveLoyaltyProgram
# ---------------------------------------------------------------------------


def test_main_returns_the_sellers_program_in_the_documented_shape(h: Harness) -> None:
    """ "The ID of the loyalty program or the keyword `main`." -- and the
    object's fields, in its documented order."""
    response = h.api.get("/v2/loyalty/programs/main", headers=h.auth)
    assert response.status == 200, response.text
    program = response.json()["program"]
    assert list(program) == [
        "id",
        "status",
        "reward_tiers",
        "terminology",
        "location_ids",
        "created_at",
        "updated_at",
        "accrual_rules",
    ]
    assert program["id"] == SEED_LOYALTY_PROGRAM_ID
    assert program["status"] == "ACTIVE"
    assert program["terminology"] == {"one": "Point", "other": "Points"}
    assert program["location_ids"] == [SEED_LOCATION_ID, SEED_KIOSK_LOCATION_ID]
    assert program["reward_tiers"][0]["id"] == SEED_LOYALTY_REWARD_TIER_ID
    assert program["accrual_rules"] == [
        {
            "accrual_type": "SPEND",
            "points": 1,
            "spend_data": {
                "amount_money": {"amount": 100, "currency": "USD"},
                "excluded_category_ids": [],
                "excluded_item_variation_ids": [],
                "tax_mode": "BEFORE_TAX",
            },
        }
    ]
    by_id = h.api.get(f"/v2/loyalty/programs/{SEED_LOYALTY_PROGRAM_ID}", headers=h.auth).json()["program"]
    assert by_id == program


def test_an_unknown_program_is_404(h: Harness) -> None:
    response = h.api.get("/v2/loyalty/programs/nope", headers=h.auth)
    assert response.status == 404
    assert first_error(response)["field"] == "program_id"


# ---------------------------------------------------------------------------
# SearchLoyaltyAccounts
# ---------------------------------------------------------------------------


def test_search_by_phone_mapping_finds_the_seeded_buyer(h: Harness) -> None:
    body = search(h, query={"mappings": [{"phone_number": SEED_LOYALTY_ACCOUNT_PHONE}]}).json()
    (found,) = body["loyalty_accounts"]
    assert found["id"] == SEED_LOYALTY_ACCOUNT_ID
    assert found["program_id"] == SEED_LOYALTY_PROGRAM_ID
    assert found["customer_id"] == SEED_LOYALTY_CUSTOMER_ID
    assert found["balance"] == 0
    assert found["lifetime_points"] == 0
    assert found["mapping"]["phone_number"] == SEED_LOYALTY_ACCOUNT_PHONE
    assert found["enrolled_at"] == "2026-08-01T10:00:00.000Z"
    assert "cursor" not in body


def test_search_for_an_unknown_phone_is_an_empty_list(h: Harness) -> None:
    """The "search, then create" flow's first half: an empty array, not an
    error, is what says "not enrolled"."""
    assert search(h, query={"mappings": [{"phone_number": "+19999999999"}]}).json() == {"loyalty_accounts": []}


def test_search_by_customer_id_and_the_two_filters_do_not_combine(h: Harness) -> None:
    """ "This cannot be combined with `customer_ids`." """
    by_customer = search(h, query={"customer_ids": [SEED_LOYALTY_CUSTOMER_ID]}).json()["loyalty_accounts"]
    assert [row["id"] for row in by_customer] == [SEED_LOYALTY_ACCOUNT_ID]
    both = search(
        h,
        query={"customer_ids": [SEED_LOYALTY_CUSTOMER_ID], "mappings": [{"phone_number": SEED_LOYALTY_ACCOUNT_PHONE}]},
    )
    assert both.status == 400
    assert first_error(both)["field"] == "query.mappings"


def test_search_limit_is_bounded_and_pages(h: Harness) -> None:
    """ "Min 1, Max 200", default 30."""
    assert enrol(h, "+14155550001", key="e1").status == 200
    page = search(h, limit=1).json()
    assert len(page["loyalty_accounts"]) == 1
    assert page["cursor"]
    rest = search(h, limit=1, cursor=page["cursor"]).json()
    assert len(rest["loyalty_accounts"]) == 1
    assert "cursor" not in rest
    assert first_error(search(h, limit=0))["field"] == "limit"
    assert first_error(search(h, limit=201))["field"] == "limit"


# ---------------------------------------------------------------------------
# CreateLoyaltyAccount
# ---------------------------------------------------------------------------


def test_enrol_creates_an_account_with_uuid_shaped_ids(h: Harness) -> None:
    response = enrol(h, "+14155550001")
    assert response.status == 200, response.text
    created = response.json()["loyalty_account"]
    assert len(created["id"].split("-")) == 5
    assert len(created["mapping"]["id"].split("-")) == 5
    assert created["program_id"] == SEED_LOYALTY_PROGRAM_ID
    assert created["balance"] == created["lifetime_points"] == 0
    assert created["mapping"]["phone_number"] == "+14155550001"
    assert created["enrolled_at"] == created["created_at"]
    # A customer was minted, since none was named (JUDGMENT, stated).
    assert len(created["customer_id"]) == 26
    # And the search now finds them.
    found = search(h, query={"mappings": [{"phone_number": "+14155550001"}]}).json()["loyalty_accounts"]
    assert [row["id"] for row in found] == [created["id"]]


def test_a_stated_customer_id_is_kept(h: Harness) -> None:
    created = enrol(h, "+14155550002", customer_id="CUST123").json()["loyalty_account"]
    assert created["customer_id"] == "CUST123"


@pytest.mark.parametrize("phone", ["4155550001", "+1 415 555 0001", "+0415", "+14155550001x", "+1234567890123456"])
def test_a_phone_number_that_is_not_e164_is_refused(h: Harness, phone: str) -> None:
    """ "The phone number of the buyer, in E.164 format." """
    response = enrol(h, phone)
    assert response.status == 400
    assert first_error(response)["field"] == "loyalty_account.mapping.phone_number"


def test_enrolling_a_phone_twice_is_a_conflict(h: Harness) -> None:
    """JUDGMENT, stated on the surface: 409, naming the existing account."""
    response = enrol(h, SEED_LOYALTY_ACCOUNT_PHONE)
    assert response.status == 409
    assert first_error(response)["code"] == "CONFLICT"
    assert response.json()["unit_error"]["loyalty_account_id"] == SEED_LOYALTY_ACCOUNT_ID


def test_enrol_needs_the_program_a_mapping_and_an_idempotency_key(h: Harness) -> None:
    wrong_program = h.api.post(
        "/v2/loyalty/accounts",
        {
            "idempotency_key": "e",
            "loyalty_account": {"program_id": "nope", "mapping": {"phone_number": "+14155550003"}},
        },
        headers=h.auth,
    )
    assert wrong_program.status == 404
    no_mapping = h.api.post(
        "/v2/loyalty/accounts",
        {"idempotency_key": "e2", "loyalty_account": {"program_id": SEED_LOYALTY_PROGRAM_ID}},
        headers=h.auth,
    )
    assert first_error(no_mapping)["field"] == "loyalty_account.mapping.phone_number"
    no_key = h.api.post(
        "/v2/loyalty/accounts",
        {"loyalty_account": {"program_id": SEED_LOYALTY_PROGRAM_ID, "mapping": {"phone_number": "+14155550003"}}},
        headers=h.auth,
    )
    assert first_error(no_key)["field"] == "idempotency_key"


# ---------------------------------------------------------------------------
# AccumulateLoyaltyPoints
# ---------------------------------------------------------------------------


def test_accumulating_for_an_order_computes_points_from_the_accrual_rule(h: Harness) -> None:
    """One point per 100 minor units, integer division: a 550 order earns 5.
    The event is the documented `ACCUMULATE_POINTS` shape and the balance
    moves by exactly its `points`."""
    order_id = create_order(h, 550)
    response = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, order_id=order_id)
    assert response.status == 200, response.text
    (event,) = response.json()["events"]
    assert list(event) == [
        "id",
        "type",
        "created_at",
        "accumulate_points",
        "loyalty_account_id",
        "location_id",
        "source",
    ]
    assert event["type"] == "ACCUMULATE_POINTS"
    assert event["accumulate_points"] == {
        "loyalty_program_id": SEED_LOYALTY_PROGRAM_ID,
        "points": 5,
        "order_id": order_id,
    }
    assert event["loyalty_account_id"] == SEED_LOYALTY_ACCOUNT_ID
    assert event["location_id"] == SEED_LOCATION_ID
    assert event["source"] == "LOYALTY_API"
    found = account(h, SEED_LOYALTY_ACCOUNT_ID)
    assert found["balance"] == 5
    assert found["lifetime_points"] == 5


def test_the_seeded_open_order_earns_its_points_once(h: Harness) -> None:
    """The seeded OPEN order totals 825 (2 x 150 + 525): eight points, and a
    second accumulation for the same order is refused (JUDGMENT)."""
    first = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, order_id=SEED_OPEN_ORDER_ID)
    assert first.status == 200, first.text
    assert first.json()["events"][0]["accumulate_points"]["points"] == 8
    again = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, key="acc-2", order_id=SEED_OPEN_ORDER_ID)
    assert again.status == 400
    assert first_error(again)["field"] == "accumulate_points.order_id"
    assert account(h, SEED_LOYALTY_ACCOUNT_ID)["balance"] == 8


def test_a_sub_threshold_order_earns_nothing_and_says_so(h: Harness) -> None:
    """JUDGMENT: refused rather than an empty `events`."""
    order_id = create_order(h, 99)
    response = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, order_id=order_id)
    assert response.status == 400
    assert first_error(response)["code"] == "BAD_REQUEST"
    assert response.json()["unit_error"]["order_total"] == 99
    assert account(h, SEED_LOYALTY_ACCOUNT_ID)["balance"] == 0


def test_a_stated_number_of_points_is_added_directly(h: Harness) -> None:
    """ "points: The number of points to add to the account. Specify this
    field only when the points are not calculated by Square from an order." """
    response = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, points=3)
    assert response.status == 200, response.text
    (event,) = response.json()["events"]
    assert event["accumulate_points"] == {"loyalty_program_id": SEED_LOYALTY_PROGRAM_ID, "points": 3}
    assert account(h, SEED_LOYALTY_ACCOUNT_ID)["balance"] == 3


def test_exactly_one_of_order_id_and_points_is_required(h: Harness) -> None:
    neither = accumulate(h, SEED_LOYALTY_ACCOUNT_ID)
    assert first_error(neither)["field"] == "accumulate_points"
    both = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, key="b", order_id=SEED_OPEN_ORDER_ID, points=1)
    assert first_error(both)["field"] == "accumulate_points"
    zero = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, key="z", points=0)
    assert first_error(zero)["field"] == "accumulate_points.points"


def test_accumulate_needs_a_real_account_location_and_order(h: Harness) -> None:
    assert accumulate(h, "nope", order_id=SEED_OPEN_ORDER_ID).status == 404
    bad_location = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, location_id="NOSUCH", order_id=SEED_OPEN_ORDER_ID)
    assert first_error(bad_location)["field"] == "location_id"
    bad_order = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, order_id="CAISnope")
    assert first_error(bad_order)["field"] == "accumulate_points.order_id"
    no_key = h.api.post(
        f"/v2/loyalty/accounts/{SEED_LOYALTY_ACCOUNT_ID}/accumulate",
        {"location_id": SEED_LOCATION_ID, "accumulate_points": {"points": 1}},
        headers=h.auth,
    )
    assert first_error(no_key)["field"] == "idempotency_key"


def test_a_rejected_accumulation_writes_no_event_and_moves_no_balance(h: Harness) -> None:
    """The ledger invariant: events sum to the balance, so a refusal must
    leave both untouched."""
    seq = int(h.api.get("/__unit/journal").json()["seq"])
    assert accumulate(h, SEED_LOYALTY_ACCOUNT_ID, order_id="CAISnope").status == 400
    assert int(h.api.get("/__unit/journal").json()["seq"]) == seq
    assert h.api.get("/__unit/state").json()["entities"].get("loyalty_events", 0) == 0


def test_accumulate_replays_under_its_idempotency_key(h: Harness) -> None:
    first = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, points=4)
    again = accumulate(h, SEED_LOYALTY_ACCOUNT_ID, points=4)
    assert first.json() == again.json()
    assert account(h, SEED_LOYALTY_ACCOUNT_ID)["balance"] == 4


# ---------------------------------------------------------------------------
# Scopes and capability
# ---------------------------------------------------------------------------


def test_scopes_are_loyalty_read_and_loyalty_write(h: Harness) -> None:
    """https://developer.squareup.com/docs/oauth-api/square-permissions"""
    assert h.api.get("/v2/loyalty/programs/main", headers=h.read_auth).status == 200
    assert search(h).status == 200
    refused = h.api.post(
        "/v2/loyalty/accounts",
        {
            "idempotency_key": "ro",
            "loyalty_account": {"program_id": SEED_LOYALTY_PROGRAM_ID, "mapping": {"phone_number": "+14155550009"}},
        },
        headers=h.read_auth,
    )
    assert refused.status == 403
    assert first_error(refused)["code"] == "INSUFFICIENT_SCOPES"


def test_the_surface_is_its_own_capability() -> None:
    for scoped in build_harness("orders-only"):
        response = scoped.api.get("/v2/loyalty/programs/main", headers=scoped.auth)
        assert response.status == 501
        assert response.headers["x-unit-capability"] == "loyalty"


def test_a_scenario_with_accounts_and_no_program_is_refused(h: Harness) -> None:
    from vendorfake.square.config import SquareConfig
    from vendorfake.square.seed.hydrate import hydrate_square

    document = {
        "merchant": {"id": "M1", "business_name": "X"},
        "loyalty_accounts": [{"id": "a1", "phone_number": "+14155550001", "customer_id": "c1"}],
    }
    h.unit.context.store.reset()
    with pytest.raises(Exception, match="loyalty_program"):
        hydrate_square(h.unit.context, document, SquareConfig())
