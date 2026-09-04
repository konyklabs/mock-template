"""The six shipped profiles, and what each name promises.

The profile-name contract is C35's; these are the vendor-side half, so that a
profile edited by hand fails here with a message about this vendor rather than
in a matrix run.
"""

from __future__ import annotations

import json

import pytest

from vendorfake.lightspeed.capabilities import LIGHTSPEED_CAPABILITIES
from vendorfake.lightspeed.vendor import LIGHTSPEED_ROLES, LightspeedVendor
from vendorfake.registry import available_profiles

REQUIRED = ("full", "oauth-only", "orders-only", "no-chaos", "no-faults", "chaos-demo")

AUTH = LIGHTSPEED_ROLES["auth"]
ORDERS = LIGHTSPEED_ROLES["orders"]
CHAOS = LIGHTSPEED_ROLES["chaos"]
WEBHOOKS = LIGHTSPEED_ROLES["webhooks"]


def _profile(name: str) -> dict[str, object]:
    path = LightspeedVendor().profile_dir / f"{name}.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _capabilities(name: str) -> set[str]:
    raw = _profile(name)["capabilities"]
    assert isinstance(raw, list)
    return {str(item) for item in raw}


def test_all_six_shared_names_ship() -> None:
    """C-discovery.md requires every vendor to ship all six, so that
    ``profile="oauth-only"`` means the same shape of thing on every vendor."""
    assert set(REQUIRED) <= {row.name for row in available_profiles("lightspeed")}


@pytest.mark.parametrize("name", REQUIRED)
def test_every_profile_names_only_declared_capabilities(name: str) -> None:
    declared = {row.name for row in LIGHTSPEED_CAPABILITIES}
    assert _capabilities(name) <= declared


@pytest.mark.parametrize("name", REQUIRED)
def test_every_profile_document_agrees_with_its_filename(name: str) -> None:
    assert _profile(name)["name"] == name


@pytest.mark.parametrize("name", REQUIRED)
def test_every_profile_loads_the_shipped_seed(name: str) -> None:
    assert _profile(name)["seed"] == "seed/default.seed.json"


def test_oauth_only_enables_auth_and_chaos_and_no_resource_surface() -> None:
    enabled = _capabilities("oauth-only")
    assert {AUTH, CHAOS} <= enabled
    assert "registers" not in enabled and "webhooks" not in enabled


def test_orders_only_has_no_token_endpoint() -> None:
    """Every shipped profile of this name promises "authenticate with a seeded
    token"; a live token endpoint would contradict it."""
    enabled = _capabilities("orders-only")
    assert ORDERS in enabled
    assert AUTH not in enabled


def test_no_chaos_keeps_request_faults_and_drops_delivery_faults() -> None:
    enabled = _capabilities("no-chaos")
    assert CHAOS in enabled
    assert "webhooks.chaos" not in enabled


def test_no_faults_drops_both() -> None:
    enabled = _capabilities("no-faults")
    assert CHAOS not in enabled
    assert "webhooks.chaos" not in enabled


def test_chaos_demo_arms_rules_on_a_virtual_clock() -> None:
    document = _profile("chaos-demo")
    assert document["clock"] == {"mode": "virtual", "start": "2026-09-04T12:00:00.000Z"}
    chaos = document["chaos"]
    assert isinstance(chaos, dict)
    rules = chaos["rules"]
    assert isinstance(rules, list) and len(rules) == 4


def test_the_rate_limiter_is_on_in_every_profile() -> None:
    """It is vendor behaviour, not chaos: the documented quota applies to every
    Lightspeed retailer and no profile here can switch it off. A profile that
    tried would have to name a capability, and there is none to name."""
    for name in REQUIRED:
        assert "rate_limit" not in json.dumps(_profile(name)) or name == "chaos-demo"
