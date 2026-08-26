"""The shipped profiles: every one loads, and every one means what it says.

A profile is package data a consumer selects by name, so a profile that does
not load is a broken product rather than a broken test. This file boots each
one and asserts the surface it serves.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.square.harness import APPLICATION_ID, APPLICATION_SECRET, CONFIGURED_REDIRECT_URI
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.capabilities import SQUARE_CAPABILITIES
from vendorfake.square.vendor import create_square_vendor

PROFILE_DIR = create_square_vendor().profile_dir
SHIPPED = sorted(path.stem for path in PROFILE_DIR.glob("*.json"))


def test_the_expected_profiles_are_shipped() -> None:
    """`chaos-demo` is deliberately absent until the orders surface lands: its
    rules name `POST /v2/orders` and `GET /v2/orders/{order_id}`, and a rule
    matching no registered route can never fire. The shipped profiles all set
    `strict_rules`, so shipping it early would be a startup failure rather than
    a silent no-op -- which is the right way round, and the reason to wait."""
    assert SHIPPED == ["full", "no-chaos", "oauth-only", "orders-only"]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_starts(name: str) -> None:
    for h in build_harness(name):
        health = h.api.get("/__unit/health").json()
        assert health["status"] == "ok"
        assert health["profile"] == name


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_names_only_declared_capabilities(name: str) -> None:
    declared = {decl.name for decl in SQUARE_CAPABILITIES}
    document = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert set(document["capabilities"]) <= declared


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_seeds_the_shipped_scenario(name: str) -> None:
    document = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert document["seed"] == "seed/default.seed.json"


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_refuses_a_chaos_rule_that_can_never_fire(name: str) -> None:
    """The reference validated a rule's id, fault and scope and never its
    route, so a typo was a rule that matched nothing, forever, silently."""
    document = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert document["chaos"]["strict_rules"] is True


def test_the_vendor_block_is_snake_case_and_typed() -> None:
    """The reference's profiles carry camelCase keys, because its whole entity
    model is camelCase. `SquareConfig` is a Pydantic model with snake_case
    fields and `extra="forbid"`, so a camelCase key here would be a startup
    failure naming it -- which is the check, not the inconvenience."""
    document = json.loads((PROFILE_DIR / "full.json").read_text(encoding="utf-8"))
    assert document["vendor"] == {
        "application_id": APPLICATION_ID,
        "application_secret": APPLICATION_SECRET,
        "redirect_uri": CONFIGURED_REDIRECT_URI,
        "environment": "Sandbox",
        "api_version": "2026-08-19",
    }


def test_oauth_only_serves_the_oauth_surface_and_nothing_else() -> None:
    for h in build_harness("oauth-only"):
        routes = h.api.get("/__unit/routes").json()["routes"]
        vendor_paths = {route["path"] for route in routes if not route["internal"]}
        assert vendor_paths == {
            "/oauth2/authorize",
            "/oauth2/token",
            "/oauth2/revoke",
            "/oauth2/token/status",
        }


def test_orders_only_serves_no_oauth_surface() -> None:
    """The OAuth routes are still *registered* -- the surface is the vendor's,
    not the profile's -- and the capability gate is what answers 501. That is
    the distinction `GET /__unit/capabilities` exists to make visible."""
    for h in build_harness("orders-only"):
        capabilities = {row["name"]: row for row in h.api.get("/__unit/capabilities").json()["capabilities"]}
        assert capabilities["oauth"]["enabled"] is False
        assert h.api.post("/oauth2/token", {"client_id": APPLICATION_ID}).status == 501


def test_the_default_profile_is_full() -> None:
    """`create_unit` with no profile resolves `full`, which is what a consumer
    running the container with no configuration gets."""
    for h in build_harness(profile=None):
        assert h.api.get("/__unit/info").json()["profile"] == "full"
