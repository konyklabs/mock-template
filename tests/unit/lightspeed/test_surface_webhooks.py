"""Webhooks: the five documented operations, and the two shapes they answer with.

Unlike Toast's and Clover's, this is a real vendor surface -- Lightspeed
publishes webhook CRUD in the specification itself -- so every status and every
body below is the one the document declares.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.lightspeed.harness import Harness, harness
from vendorfake.lightspeed.events import LIGHTSPEED_EVENT_TYPES
from vendorfake.lightspeed.seed import constants as c

WEBHOOKS = "/webhooks"
NEW = {"active": True, "type": "sale.update", "url": "https://consumer.example/hooks/sales"}


def _create(h: Harness, **overrides: object) -> object:
    return h.post(h.path(WEBHOOKS), json.dumps({**NEW, **overrides}))


# -- the list ----------------------------------------------------------------


def test_the_list_answers_data_with_no_version_envelope(h: Harness) -> None:
    """The inline schema on ``get-webhooks`` declares a ``data`` array and no
    ``version`` member -- this is the one list in the package that does not
    carry the version envelope and does not paginate."""
    body = h.get(h.path(WEBHOOKS)).json()
    assert set(body) == {"data"}
    assert [row["id"] for row in body["data"]] == [c.SEED_WEBHOOK_ID]


def test_a_webhook_carries_the_five_documented_members(h: Harness) -> None:
    row = h.get(h.path(WEBHOOKS)).json()["data"][0]
    assert set(row) == {"id", "retailer_id", "type", "url", "active"}
    assert row["retailer_id"] == c.SEED_RETAILER_ID
    assert row["type"] == c.SEED_WEBHOOK_TYPE
    assert row["url"] == c.SEED_WEBHOOK_URL
    assert row["active"] is True


def test_no_list_route_declares_pagination(h: Harness) -> None:
    rows = h.api.get("/__unit/routes").json()["routes"]
    webhook_rows = [row for row in rows if row["path"].endswith("/webhooks") and row["method"] == "GET"]
    assert webhook_rows and all(row.get("pagination") is None for row in webhook_rows)


# -- create ------------------------------------------------------------------


def test_create_answers_201_with_the_single_record_wrapper(h: Harness) -> None:
    answered = _create(h)
    assert answered.status == 201
    assert answered.json()["data"]["type"] == "sale.update"
    assert answered.json()["data"]["id"]


def test_a_created_webhook_appears_in_the_list(h: Harness) -> None:
    created = _create(h).json()["data"]
    assert created["id"] in {row["id"] for row in h.get(h.path(WEBHOOKS)).json()["data"]}


def test_a_duplicate_type_and_url_pair_is_the_documented_409(h: Harness) -> None:
    """The response's own description is the message, and its inline schema is
    ``{"error": <string>}`` -- ONE member, not the two the rest of this
    package's errors carry."""
    answered = h.post(
        h.path(WEBHOOKS),
        json.dumps({"active": True, "type": c.SEED_WEBHOOK_TYPE, "url": c.SEED_WEBHOOK_URL}),
    )
    assert answered.status == 409
    body = answered.json()
    assert body["error"] == "A webhook with this type and URL already exists."
    assert "message" not in body


def test_the_same_url_on_a_different_type_is_allowed(h: Harness) -> None:
    """It is the PAIR that is unique -- the 409's own description says so."""
    answered = _create(h, url=c.SEED_WEBHOOK_URL)
    assert answered.status == 201


def test_the_same_type_at_a_different_url_is_allowed(h: Harness) -> None:
    answered = _create(h, type=c.SEED_WEBHOOK_TYPE, url="https://elsewhere.example/hooks")
    assert answered.status == 201


@pytest.mark.parametrize("event_type", LIGHTSPEED_EVENT_TYPES)
def test_every_documented_event_type_is_subscribable(h: Harness, event_type: str) -> None:
    """All seven, the two consignment values included: a subscription to one of
    those is accepted and nothing here ever fires it."""
    answered = _create(h, type=event_type, url=f"https://consumer.example/hooks/{event_type}")
    assert answered.status == 201


def test_an_undocumented_event_type_is_refused_naming_the_seven(h: Harness) -> None:
    answered = _create(h, type="sale.deleted")
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "type"


def test_a_missing_required_member_is_refused_by_name(h: Harness) -> None:
    answered = h.post(h.path(WEBHOOKS), json.dumps({"type": "sale.update", "url": "https://x.example/h"}))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "active"


def test_a_url_shorter_than_the_documented_minimum_is_refused(h: Harness) -> None:
    """``WebhookRequest.url`` carries ``minLength: 3``."""
    answered = _create(h, url="ab")
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "url"


def test_an_http_url_is_accepted_by_default(h: Harness) -> None:
    """JUDGMENT: ``WebhookRequest`` types ``url`` as a plain string and names
    no scheme, so refusing would be this project inventing a rule the vendor
    does not publish."""
    assert _create(h, url="http://localhost:9999/hooks").status == 201


def test_the_switch_can_require_https() -> None:
    gen = harness(env={"VENDORFAKE_VENDOR_ALLOW_INSECURE_CALLBACKS": "false"})
    started = next(gen)
    try:
        answered = started.post(
            started.path(WEBHOOKS),
            json.dumps({"active": True, "type": "sale.update", "url": "http://localhost:9999/hooks"}),
        )
        assert answered.status == 422
        assert answered.json()["unit_error"]["field"] == "url"
    finally:
        gen.close()


# -- get, update, delete -----------------------------------------------------


def test_get_answers_the_single_record_wrapper(h: Harness) -> None:
    answered = h.get(h.path(f"{WEBHOOKS}/{c.SEED_WEBHOOK_ID}"))
    assert answered.status == 200
    assert answered.json()["data"]["id"] == c.SEED_WEBHOOK_ID


def test_an_unknown_id_is_the_documented_one_member_404(h: Harness) -> None:
    """The three ``/webhooks/{webhookId}`` operations declare the same
    ``{"error": <string>}`` shape their sibling 409 does."""
    answered = h.get(h.path(f"{WEBHOOKS}/nope"))
    assert answered.status == 404
    body = answered.json()
    assert body["error"] == "Webhook nope was not found."
    # `message` is the second member of the generalised body every other
    # refusal in this package sends; the Webhooks tag declares a one-member
    # shape and this route keeps it. (`unit_error` is this fake's own sidecar,
    # which the harness switches into the body for these suites.)
    assert "message" not in body


def test_update_replaces_all_three_members(h: Harness) -> None:
    answered = h.put(
        h.path(f"{WEBHOOKS}/{c.SEED_WEBHOOK_ID}"),
        json.dumps({"active": False, "type": "customer.update", "url": "https://consumer.example/hooks/two"}),
    )
    assert answered.status == 200
    updated = answered.json()["data"]
    assert updated["active"] is False
    assert updated["type"] == "customer.update"
    assert updated["url"] == "https://consumer.example/hooks/two"


def test_update_onto_another_hooks_pair_is_a_409(h: Harness) -> None:
    created = _create(h).json()["data"]
    answered = h.put(
        h.path(f"{WEBHOOKS}/{created['id']}"),
        json.dumps({"active": True, "type": c.SEED_WEBHOOK_TYPE, "url": c.SEED_WEBHOOK_URL}),
    )
    assert answered.status == 409


def test_update_onto_its_own_pair_is_allowed(h: Harness) -> None:
    answered = h.put(
        h.path(f"{WEBHOOKS}/{c.SEED_WEBHOOK_ID}"),
        json.dumps({"active": False, "type": c.SEED_WEBHOOK_TYPE, "url": c.SEED_WEBHOOK_URL}),
    )
    assert answered.status == 200


def test_update_on_an_unknown_id_is_a_404(h: Harness) -> None:
    assert h.put(h.path(f"{WEBHOOKS}/nope"), json.dumps(NEW)).status == 404


def test_delete_answers_an_empty_200(h: Harness) -> None:
    """The operation declares ``"200": {"description": "OK"}`` with no
    content -- a 200 and an empty body, not the 204 a reader might assume."""
    answered = h.delete(h.path(f"{WEBHOOKS}/{c.SEED_WEBHOOK_ID}"))
    assert answered.status == 200
    assert answered.body == b""
    assert h.get(h.path(WEBHOOKS)).json()["data"] == []


def test_delete_on_an_unknown_id_is_a_404(h: Harness) -> None:
    assert h.delete(h.path(f"{WEBHOOKS}/nope")).status == 404


# -- scope -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", WEBHOOKS, None),
        ("POST", WEBHOOKS, json.dumps(NEW)),
        ("GET", f"{WEBHOOKS}/{c.SEED_WEBHOOK_ID}", None),
        ("PUT", f"{WEBHOOKS}/{c.SEED_WEBHOOK_ID}", json.dumps(NEW)),
        ("DELETE", f"{WEBHOOKS}/{c.SEED_WEBHOOK_ID}", None),
    ],
)
def test_every_operation_needs_the_single_unqualified_webhooks_scope(
    h: Harness, method: str, path: str, body: str | None
) -> None:
    """Not a read/write pair: the scopes reference page has one ``webhooks``
    entry and all five operations name it."""
    answered = h.api.call(method=method, path=h.path(path), body=body, headers=h.read_auth)
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["webhooks"]
