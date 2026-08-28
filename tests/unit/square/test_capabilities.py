"""Capability toggles: the claim that a consumer subset is configuration.

Nothing in this file changes a line of the unit. Every subset comes from a
profile, an environment variable or a control-plane call, which is the whole
composition claim: a consumer who needs only the OAuth dance runs the same
build as a consumer who needs orders and webhooks, and neither has to fork.

The distinction this file exists to pin is the one a 404 would destroy. "This
vendor does not serve that path" and "this deployment has that surface switched
off" are different facts and a consumer must be able to act on each: the first
is a bug in their integration, the second is a line in a profile. So a disabled
capability answers 501 with the capability *named*, and only a genuinely absent
path answers 404.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import APPLICATION_ID, CONFIGURED_REDIRECT_URI, Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.capabilities import SQUARE_CAPABILITIES
from vendorfake.square.seed.constants import SEED_LOCATION_ID, SEED_OPEN_ORDER_ID, TEA_MUG_VARIATION_ID


@pytest.fixture
def full() -> Iterator[Harness]:
    yield from build_harness("full")


@pytest.fixture
def oauth_only() -> Iterator[Harness]:
    yield from build_harness("oauth-only")


def capability_view(h: Harness) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in h.api.get("/__unit/capabilities").json()["capabilities"]}


def order_body(key: str) -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "order": {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"catalog_object_id": TEA_MUG_VARIATION_ID, "quantity": "2"}],
        },
    }


# ---------------------------------------------------------------------------
# 501 versus 404: the distinction the whole design rests on.
# ---------------------------------------------------------------------------


def test_a_disabled_capability_answers_explicitly_and_never_with_a_404(oauth_only: Harness) -> None:
    response = oauth_only.api.post("/v2/orders", order_body("cap-1"), headers=oauth_only.auth)

    assert response.status == 501
    assert response.headers["x-unit-error"] == "capability_disabled"
    assert response.headers["x-unit-capability"] == "order-lifecycle"
    error = first_error(response)
    assert error["code"] == "NOT_IMPLEMENTED"
    assert error["category"] == "API_ERROR"
    assert "Capability 'order-lifecycle' is disabled in profile 'oauth-only'" in error["detail"]
    assert response.json()["unit_error"] == {
        **response.json()["unit_error"],
        "kind": "capability_disabled",
        "capability": "order-lifecycle",
        "profile": "oauth-only",
        "route": "POST /v2/orders",
    }


def test_a_path_this_vendor_does_not_serve_still_answers_404(oauth_only: Harness) -> None:
    """The contrast case, without which the assertion above proves nothing: an
    implementation that answered 501 for everything would pass it."""
    missing = oauth_only.api.get("/v2/subscriptions", headers=oauth_only.auth)
    assert missing.status == 404
    assert missing.headers["x-unit-error"] == "not_found"


def test_the_enabled_capability_is_served_in_a_narrow_profile(oauth_only: Harness) -> None:
    response = oauth_only.api.call(
        method="GET",
        path="/oauth2/authorize",
        query={"client_id": APPLICATION_ID, "redirect_uri": CONFIGURED_REDIRECT_URI},
    )
    assert response.status == 302


# ---------------------------------------------------------------------------
# Authentication is not part of the oauth capability.
# ---------------------------------------------------------------------------


def test_seeded_tokens_stay_usable_when_the_oauth_capability_is_off() -> None:
    """A consumer who does not test the OAuth dance is not forced to run it.

    Token *validity* is the auth adapter's job and the adapter is always
    present; the `oauth` capability owns the four `/oauth2/*` routes and
    nothing else. Losing that split would mean `orders-only` had no way to
    authenticate at all.
    """
    for h in build_harness("orders-only"):
        dance = h.api.post("/oauth2/token", {"client_id": "x", "grant_type": "refresh_token"})
        assert dance.status == 501
        assert dance.headers["x-unit-capability"] == "oauth"

        assert h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=h.auth).status == 200
        assert h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}").status == 401


# ---------------------------------------------------------------------------
# Where a subset can come from.
# ---------------------------------------------------------------------------


def test_a_capability_subset_can_come_from_the_environment() -> None:
    """The delta grammar, against the profile's list rather than replacing it.

    `env` is an explicit mapping here and everywhere: `create_unit` never reads
    `os.environ`, so this variable cannot leak into any other test in the run.
    """
    for h in build_harness("full", env={"VENDORFAKE_CAPABILITIES": "-webhooks,-webhooks.chaos"}):
        view = capability_view(h)
        assert view["oauth"]["enabled"] is True
        assert view["order-lifecycle"]["enabled"] is True
        assert view["webhooks"]["enabled"] is False
        assert view["webhooks.chaos"]["enabled"] is False
        assert h.api.get("/v2/webhooks/subscriptions", headers=h.auth).status == 501


def test_capabilities_toggle_at_runtime_without_a_restart(full: Harness) -> None:
    assert full.api.get("/v2/locations", headers=full.auth).status == 200

    assert full.api.post("/__unit/capabilities", {"disable": ["merchant-directory"]}).status == 200
    assert full.api.get("/v2/locations", headers=full.auth).status == 501

    assert full.api.post("/__unit/capabilities", {"enable": ["merchant-directory"]}).status == 200
    assert full.api.get("/v2/locations", headers=full.auth).status == 200


def test_a_child_capability_goes_away_with_its_parent(full: Harness) -> None:
    """`webhooks.chaos` requires `webhooks`, so disabling the parent must not
    leave a delivery-fault switch that reads as on and does nothing."""
    full.api.post("/__unit/capabilities", {"disable": ["webhooks"]})
    assert capability_view(full)["webhooks.chaos"]["enabled"] is False

    # Turning the child on by name does not overrule the prerequisite; the
    # answer says which capability is in the way rather than failing silently.
    full.api.post("/__unit/capabilities", {"enable": ["webhooks.chaos"]})
    child = capability_view(full)["webhooks.chaos"]
    assert child["enabled"] is False
    assert child["blocked_by"] == "webhooks"


def test_an_unknown_capability_name_is_refused_loudly(full: Harness) -> None:
    response = full.api.post("/__unit/capabilities", {"set": ["not-a-capability"]})
    assert response.status == 400
    assert "Unknown capability 'not-a-capability'" in first_error(response)["detail"]


# ---------------------------------------------------------------------------
# What the index publishes.
# ---------------------------------------------------------------------------


def test_the_index_reports_which_routes_each_capability_owns(full: Harness) -> None:
    """Brace templates, here as everywhere: this index is one of the five
    places a path template is written, and a colon in any one of them would
    make a chaos rule naming that route dead on arrival."""
    view = capability_view(full)
    assert "POST /v2/orders" in view["order-lifecycle"]["routes"]
    assert "POST /v2/orders/{order_id}/pay" in view["order-lifecycle"]["routes"]


def test_a_behavior_capability_owns_no_routes(full: Harness) -> None:
    """Both of them, by kind rather than by name, so a third behaviour
    capability inherits the assertion instead of escaping it."""
    view = capability_view(full)
    behaviors = {decl.name for decl in SQUARE_CAPABILITIES if decl.kind == "behavior"}
    assert behaviors == {"chaos", "webhooks.chaos"}
    for name in behaviors:
        assert view[name]["kind"] == "behavior"
        assert view[name]["routes"] == []


def test_the_declared_order_is_the_order_the_index_publishes(full: Harness) -> None:
    """A consumer reading `/__unit/capabilities` sees the vendor's declaration
    order, not a hash order that changes between runs."""
    published = [row["name"] for row in full.api.get("/__unit/capabilities").json()["capabilities"]]
    assert published == [decl.name for decl in SQUARE_CAPABILITIES]
