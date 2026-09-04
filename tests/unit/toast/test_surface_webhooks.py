"""The portal stand-in: register, list, remove; HTTPS only; one list with the core."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.toast.harness import LEDGER, SURFACE, Harness, Silent, harness
from vendorfake.fidelity.validate import ValidatingClient
from vendorfake.toast.seed import constants as c
from vendorfake.toast.surface.webhooks import STAND_IN


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_register_mints_a_secret_and_lands_in_the_core_s_list(h: Harness) -> None:
    response = h.api.post(
        "/__toast/webhooks/subscriptions",
        {"url": "https://example.test/hooks", "eventCategories": ["stock", "order_updated"]},
    )
    assert response.status == 201, response.text
    body = response.json()
    assert body["guid"].startswith("sub_") and body["url"] == "https://example.test/hooks"
    assert body["eventCategories"] == ["order_updated", "stock"]  # documented order, deduplicated
    assert len(body["secret"]) == 36 and body["enabled"] is True
    core = {row["id"]: row for row in h.api.get("/__unit/webhooks/subscriptions").json()["subscriptions"]}
    assert core[body["guid"]]["signature_key"] == body["secret"]
    assert set(core[body["guid"]]["event_types"]) == {"order_updated", "in_stock", "out_of_stock", "low_quantity"}
    listed = {row["guid"]: row for row in h.api.get("/__toast/webhooks/subscriptions").json()["subscriptions"]}
    assert listed[body["guid"]] == body
    assert listed[c.SEED_WEBHOOK_SUBSCRIPTION_ID]["eventCategories"] == ["order_updated", "stock", "menus"]
    assert listed[c.SEED_WEBHOOK_SUBSCRIPTION_ID]["enabled"] is False


def test_a_supplied_secret_is_kept_and_categories_default_to_all(h: Harness) -> None:
    body = h.api.post("/__toast/webhooks/subscriptions", {"url": "https://example.test/hooks", "secret": "mine"}).json()
    assert body["secret"] == "mine" and body["eventCategories"] == ["order_updated", "stock", "menus"]


def test_https_is_required_unless_the_switch_lifts_it() -> None:
    for h in harness():
        refused = h.api.post("/__toast/webhooks/subscriptions", {"url": "http://localhost:19999/hooks"})
        assert refused.status == 400 and refused.json()["unit_error"]["field"] == "url"
        assert "apiEndpointRequirements" in refused.json()["message"]
    from vendorfake import create_unit
    from vendorfake.toast.vendor import create_toast_vendor

    unit = create_unit(
        vendor=create_toast_vendor(vendor_config={"allow_insecure_callbacks": True}), profile="full", logger=Silent()
    )
    try:
        assert (
            ValidatingClient(unit, SURFACE, LEDGER)
            .post("/__toast/webhooks/subscriptions", {"url": "http://localhost:19999/hooks"})
            .status
            == 201
        )
    finally:
        unit.stop()


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "url"),
        ({"url": ""}, "url"),
        ({"url": "https://x.test", "eventCategories": []}, "eventCategories"),
        ({"url": "https://x.test", "eventCategories": ["partners"]}, "eventCategories"),
    ],
)
def test_a_malformed_registration_names_the_field(h: Harness, body: dict[str, object], field: str) -> None:
    response = h.api.post("/__toast/webhooks/subscriptions", body)
    assert response.status == 400 and response.json()["unit_error"]["field"] == field


def test_a_link_local_url_is_refused(h: Harness) -> None:
    """A cloud instance's metadata service lives at a link-local address; this stand-in refuses it
    the same way the control plane's own subscription route already does."""
    response = h.api.post("/__toast/webhooks/subscriptions", {"url": "https://169.254.169.254/latest"})
    assert response.status == 400 and response.json()["unit_error"]["field"] == "url"


def test_a_loopback_url_is_accepted(h: Harness) -> None:
    """Loopback stays allowed -- that is where a test's own receiver lives."""
    response = h.api.post("/__toast/webhooks/subscriptions", {"url": "https://127.0.0.1:9/hook"})
    assert response.status == 201, response.text


def test_remove_deletes_from_the_one_list_and_a_second_remove_is_404(h: Harness) -> None:
    guid = h.api.post("/__toast/webhooks/subscriptions", {"url": "https://example.test/hooks"}).json()["guid"]
    assert h.api.delete(f"/__toast/webhooks/subscriptions/{guid}").status == 204
    assert guid not in {row["id"] for row in h.api.get("/__unit/webhooks/subscriptions").json()["subscriptions"]}
    assert h.api.delete(f"/__toast/webhooks/subscriptions/{guid}").status == 404


def test_the_stand_in_routes_are_last_and_labelled(h: Harness) -> None:
    routes = [r for r in h.api.get("/__unit/routes").json()["routes"] if not r["internal"]]
    assert [r["path"] for r in routes[-3:]] == [
        "/__toast/webhooks/subscriptions",
        "/__toast/webhooks/subscriptions",
        "/__toast/webhooks/subscriptions/{guid}",
    ]
    assert all(r["summary"].startswith(STAND_IN) for r in routes[-3:])
    assert all(r["capability"] == "webhooks" for r in routes[-3:])
