"""The shipped profiles: every one loads, and every one means what it says."""

from __future__ import annotations

import json

import pytest

from tests.unit.toast.harness import Harness, harness
from tests.unit.toast.test_surface_auth import LOGIN, LOGIN_PATH
from tests.unit.toast.test_surface_orders import order_body
from vendorfake.toast.capabilities import TOAST_CAPABILITIES
from vendorfake.toast.seed import constants as c
from vendorfake.toast.vendor import create_toast_vendor

PROFILE_DIR = create_toast_vendor().profile_dir
SHIPPED = sorted(path.stem for path in PROFILE_DIR.glob("*.json"))


def _document(name: str) -> dict:  # type: ignore[type-arg]
    return dict(json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8")))


def test_the_expected_profiles_are_shipped() -> None:
    """Six, named as a literal: the same six names every vendor ships, so one
    conformance matrix shape covers all of them."""
    assert SHIPPED == ["chaos-demo", "full", "no-chaos", "no-faults", "oauth-only", "orders-only"]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_starts_and_seeds_the_shipped_scenario(name: str) -> None:
    assert _document(name)["seed"] == "seed/default.seed.json"
    for h in harness(name):
        health = h.api.get("/__unit/health").json()
        assert health["status"] == "ok" and health["profile"] == name
        assert h.unit.context.store.collection("restaurants").size == 1


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_names_only_declared_capabilities_and_refuses_dead_rules(name: str) -> None:
    declared = {decl.name for decl in TOAST_CAPABILITIES}
    assert set(_document(name)["capabilities"]) <= declared
    assert _document(name)["chaos"]["strict_rules"] is True


def test_the_default_profile_is_full() -> None:
    for h in harness():
        assert h.api.get("/__unit/health").json()["profile"] == "full"


def test_oauth_only_serves_the_login_and_refuses_the_rest_explicitly() -> None:
    for h in harness("oauth-only"):
        assert h.api.post(LOGIN_PATH, LOGIN).status == 200
        orders = h.post("/orders/v2/prices", order_body())
        assert orders.status == 501
        assert orders.headers["x-unit-capability"] == "orders"


def test_orders_only_has_no_login_and_authenticates_with_the_seeded_token() -> None:
    for h in harness("orders-only"):
        assert h.api.post(LOGIN_PATH, LOGIN).status == 501
        assert h.post("/orders/v2/prices", order_body()).status == 200
        assert h.get("/menus/v3/menus").status == 200
        assert h.get("/stock/v1/inventory").status == 200
        assert h.api.get("/__toast/webhooks/subscriptions").status == 501


def test_no_faults_switches_chaos_off_and_the_others_keep_it() -> None:
    for name in SHIPPED:
        capabilities = set(_document(name)["capabilities"])
        assert ("chaos" in capabilities) == (name != "no-faults"), name
    for h in harness("no-faults"):
        assert h.post("/orders/v2/orders", order_body(externalId="chaos:rate_limit")).status == 200  # inert


def test_chaos_demo_ships_four_rules_on_a_virtual_clock_and_the_request_rules_fire() -> None:
    document = _document("chaos-demo")
    assert document["clock"]["mode"] == "virtual"
    assert [rule["id"] for rule in document["chaos"]["rules"]] == [
        "rate-limit-every-third-create",
        "token-expires-on-fourth-read",
        "duplicate-order-updated",
        "reorder-out-of-stock",
    ]
    for h in harness("chaos-demo"):
        statuses = [h.post("/orders/v2/orders", order_body()).status for _ in range(3)]
        assert statuses == [200, 200, 429]
        limited = h.post("/orders/v2/orders", order_body())  # the 4th: fine again
        assert limited.status == 200
        guid = limited.json()["guid"]
        reads = [h.get(f"/orders/v2/orders/{guid}").status for _ in range(4)]
        assert reads == [200, 200, 200, 401]
        assert h.get(f"/orders/v2/orders/{guid}").status == 200  # the stored token never changed


SEED_DIGEST = "47c16e1359143cbca7680578258049416efc9b6f4205961ede68fb63af006af6"
"""The entity digest of the shipped scenario, pinned as a literal.

Identical on every profile because seeded ids come from the document, never
from the id stream, and every hydrate-time instant is a volatile field the
digest ignores. A change to the scenario changes this line on purpose; a
change to anything else that moves it is the regression this test exists to
catch (the same claim conformance C06/C22 make across units and processes).
The value moved when the branch rebased onto the #35 chassis: the digest now
scrubs volatile names at any depth and keeps their presence markers.

Re-pinned for konyklabs/roadmap#56: the seeded discount's promo codes became
the ``PromoCode`` objects the configuration specification describes, and a
seeded selection stores no applied-tax guid (it is derived on the wire from
the selection's own guid, the same way the builder derives it).
"""


@pytest.mark.parametrize("name", SHIPPED)
def test_the_seeded_digest_is_pinned_and_identical_on_every_profile(name: str) -> None:
    for h in harness(name):
        assert h.unit.context.store.entity_digest() == SEED_DIGEST


def test_every_profile_seeds_the_read_only_token_so_a_403_is_always_askable() -> None:
    for name in SHIPPED:
        for h in harness(name):
            tokens = {t["access_token"] for t in h.unit.context.store.collection("tokens").all()}
            assert {c.SEED_ACCESS_TOKEN, c.SEED_READ_ONLY_ACCESS_TOKEN} <= tokens


def _unused(h: Harness) -> None:  # pragma: no cover - keeps the Harness import honest for type checkers
    del h
