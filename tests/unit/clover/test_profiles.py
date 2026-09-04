"""The shipped profiles: every one loads, and every one means what it says."""

from __future__ import annotations

import json

import pytest

from tests.unit.clover.harness import CLIENT_ID, harness
from vendorfake.clover.capabilities import CLOVER_CAPABILITIES
from vendorfake.clover.vendor import create_clover_vendor

PROFILE_DIR = create_clover_vendor().profile_dir
SHIPPED = sorted(path.stem for path in PROFILE_DIR.glob("*.json"))


def _document(name: str) -> dict:  # type: ignore[type-arg]
    return dict(json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8")))


def test_the_expected_profiles_are_shipped() -> None:
    """Six, named as a literal: a consumer selects a profile by name and a
    rename is a breaking change."""
    assert SHIPPED == ["chaos-demo", "full", "no-chaos", "no-faults", "oauth-only", "orders-only"]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_starts_and_seeds_the_shipped_scenario(name: str) -> None:
    assert _document(name)["seed"] == "seed/default.seed.json"
    for h in harness(name):
        health = h.api.get("/__unit/health").json()
        assert health["status"] == "ok"
        assert health["profile"] == name
        assert h.unit.context.store.collection("merchants").size == 1


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_names_only_declared_capabilities(name: str) -> None:
    declared = {decl.name for decl in CLOVER_CAPABILITIES}
    assert set(_document(name)["capabilities"]) <= declared


def test_the_default_profile_is_full() -> None:
    for h in harness():
        assert h.api.get("/__unit/health").json()["profile"] == "full"


def test_oauth_only_serves_the_dance_and_refuses_the_rest_explicitly() -> None:
    for h in harness("oauth-only"):
        assert h.authorize().status == 302
        orders = h.get("/orders")
        assert orders.status == 501
        assert orders.headers["x-unit-capability"] == "orders"


def test_orders_only_has_no_dance_and_authenticates_with_the_seeded_token() -> None:
    for h in harness("orders-only"):
        assert h.api.call(method="GET", path="/oauth/v2/authorize", query={"client_id": CLIENT_ID}).status == 501
        assert h.get("/orders").status == 200
        assert h.get("/items").status == 200


def test_no_faults_switches_chaos_off_and_the_others_keep_it() -> None:
    for name in SHIPPED:
        capabilities = set(_document(name)["capabilities"])
        assert ("chaos" in capabilities) == (name != "no-faults"), name
    for h in harness("no-faults"):
        assert h.post("/orders", {"total": 1, "note": "chaos:rate_limit"}).status == 200  # the trigger is inert


def test_chaos_demo_ships_four_rules_on_a_virtual_clock_and_the_request_rules_fire() -> None:
    document = _document("chaos-demo")
    assert document["clock"]["mode"] == "virtual"
    assert [rule["id"] for rule in document["chaos"]["rules"]] == [
        "rate-limit-every-third-create",
        "token-expires-on-fourth-read",
        "duplicate-order-created",
        "reorder-order-updated",
    ]
    assert {rule["scope"] for rule in document["chaos"]["rules"]} == {"request", "webhook"}
    for h in harness("chaos-demo"):
        statuses = [h.post("/orders", {"total": 1}).status for _ in range(3)]
        assert statuses == [200, 200, 429]
        order = h.create_order()
        reads = [h.get(f"/orders/{order['id']}").status for _ in range(4)]
        assert reads == [200, 200, 200, 401]
        assert h.get(f"/orders/{order['id']}").status == 200  # the stored token never changed


# Re-pinned for konyklabs/roadmap#15: the scenario gained a second customer
# and a second order so every list survives the conformance page walk.
SEED_DIGEST = "6366ec024e4c1e0c5c5f94fa4f0a30cedf67426002374a2e6ca7ab1af6c15f5e"
"""The entity digest of the shipped scenario, pinned as a literal.

Identical on every profile because seeded ids come from the document, never
from the id stream, and every hydrate-time instant is a volatile field whose
value the digest ignores. A change to the scenario changes this line on
purpose; a change to anything else that moves it is the regression this test
exists to catch (the same claim the conformance C06/C22 contracts make across
units and across processes). Re-pinned for konyklabs/roadmap#35, when the
digest began hashing a volatile field's presence rather than dropping it."""


@pytest.mark.parametrize("name", SHIPPED)
def test_the_seeded_digest_is_pinned_and_identical_on_every_profile(name: str) -> None:
    for h in harness(name):
        assert h.unit.context.store.entity_digest() == SEED_DIGEST


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_refuses_a_chaos_rule_that_can_never_fire(name: str) -> None:
    assert _document(name)["chaos"]["strict_rules"] is True
