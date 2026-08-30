"""The dashboard stand-in, the verification handshake, and what a subscriber
actually receives.

Three rules, inherited from the Square webhook suite:

* **the header is checked against the documented scheme**, which for Clover
  is a string comparison against the auth code the stand-in handed out;
* **headers are asserted as a whole set**, because the claim is that the core
  contributes nothing and an extra key is as much a failure as a missing one;
* **the retry cascade runs on the virtual clock at the declared schedule** --
  compressed proves the shape, uncompressed proves the numbers.

Mutations are committed straight into the store under the operation ids PR C
will use, because that branch's routes are not here; the journal listener
does not know or care which thread wrote the entry.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.clover.harness import MERCHANT_ID, Harness
from tests.unit.clover.harness import harness as build_harness
from vendorfake.clover.retry import (
    CLOVER_RETRY_SCHEDULE_MS,
    INITIAL_DELIVERY_HEADER,
    RETRY_NUMBER_HEADER,
    RETRY_REASON_HEADER,
)
from vendorfake.clover.signer import AUTH_HEADER, verify_clover_auth
from vendorfake.core.webhooks.sink import MemorySink, SinkRequest

HOOKS = "https://api-created.test/hooks"
OTHER_HOOKS = "https://api-other.test/hooks"
UUID_SHAPE_LEN = 36


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def h(sink: MemorySink) -> Iterator[Harness]:
    """The full profile: a real clock, the schedule scaled so the first retry
    is five milliseconds rather than thirty seconds."""
    yield from build_harness("full", sink=sink)


@pytest.fixture
def virtual(sink: MemorySink) -> Iterator[Harness]:
    yield from build_harness("full", sink=sink, env={"VENDORFAKE_CLOCK": "virtual"})


def register(h: Harness, url: str = HOOKS, **spec: Any) -> dict[str, Any]:
    response = h.api.post("/__clover/webhooks/subscriptions", {"url": url, **spec})
    assert response.status == 201, response.text
    return dict(response.json())


def verification_code(h: Harness, sink: MemorySink, url: str = HOOKS) -> str:
    """What the consumer's endpoint received, read off the sink."""
    h.api.post("/__unit/webhooks/drain", {})
    for request in sink.received:
        if request.url == url and AUTH_HEADER not in request.headers:
            return str(json.loads(bytes(request.body).decode("utf-8"))["verificationCode"])
    raise AssertionError(f"no verification POST reached {url}: {[r.url for r in sink.received]}")


def verify(h: Harness, code: str) -> dict[str, Any]:
    response = h.api.post("/__clover/webhooks/verify", {"verificationCode": code})
    assert response.status == 200, response.text
    return dict(response.json())


def register_and_verify(h: Harness, sink: MemorySink, url: str = HOOKS, **spec: Any) -> dict[str, Any]:
    register(h, url, **spec)
    verified = verify(h, verification_code(h, sink, url))
    sink.clear()
    return verified


def create_order(h: Harness, order_id: str = "ORD0000000001") -> None:
    h.unit.context.store.collection("orders").insert({"id": order_id}, {"operation_id": "CreateOrder"})


def create_item(h: Harness, item_id: str = "ITM0000000001") -> None:
    h.unit.context.store.collection("items").insert(
        {"id": item_id, "name": "Craft Beer"}, {"operation_id": "CreateItem"}
    )


def all_deliveries(h: Harness) -> list[dict[str, Any]]:
    h.api.post("/__unit/webhooks/drain", {})
    return list(h.api.get("/__unit/webhooks/deliveries").json()["deliveries"])


def deliveries(h: Harness) -> list[dict[str, Any]]:
    """Event deliveries only: the verification POST is in the same log, and
    the retry assertions are about what happens after it."""
    return [record for record in all_deliveries(h) if record["event_type"] != "verification"]


def first_error(response: Any) -> dict[str, Any]:
    return dict(response.json()["unit_error"])


# ---------------------------------------------------------------------------
# The unit starts with webhooks on.
# ---------------------------------------------------------------------------


def test_the_full_profile_starts_with_webhooks_enabled_and_the_judgment_schedule(h: Harness) -> None:
    """The core refuses a webhooks-enabled vendor with an empty schedule, so
    a started unit is the proof that `retry_defaults` merged under the
    profile. The profile sets only the time scale and the timeout."""
    info = h.api.get("/__unit/info").json()
    assert set(h.unit.context.config.capabilities) >= {"webhooks", "webhooks.chaos"}
    assert info["webhooks"]["enabled"] is True
    assert info["webhooks"]["retry"]["schedule_ms"] == list(CLOVER_RETRY_SCHEDULE_MS)
    assert info["webhooks"]["retry"]["time_scale"] == 0.000167
    assert info["webhooks"]["retry"]["timeout_ms"] == 2000
    assert info["signer"]["header"] == AUTH_HEADER
    assert info["signer"]["bindings"] == {
        "url_bound": False,
        "body_bound": False,
        "secret_bound": True,
        "signature_headers": ["x-clover-auth"],
    }


# ---------------------------------------------------------------------------
# The handshake.
# ---------------------------------------------------------------------------


def test_registering_a_callback_answers_pending_and_posts_the_verification_code_to_it(
    h: Harness, sink: MemorySink
) -> None:
    """Step 1 and 2 of the documented flow: the callback URL goes in, and the
    unit POSTs `{"verificationCode": "<uuid>"}` to it -- with no auth header,
    because the code is documented as sent only after validation."""
    registered = register(h)
    assert registered["verified"] is False
    assert registered["eventKeys"] == ["O", "I", "C", "P"]
    assert registered["url"] == HOOKS
    assert "authCode" not in registered, registered
    assert "verificationCode" not in registered, registered

    h.api.post("/__unit/webhooks/drain", {})
    (request,) = sink.received
    assert isinstance(request, SinkRequest)
    assert request.url == HOOKS
    body = json.loads(bytes(request.body).decode("utf-8"))
    assert list(body) == ["verificationCode"]
    assert len(body["verificationCode"]) == UUID_SHAPE_LEN
    assert set(request.headers) == {"content-type", INITIAL_DELIVERY_HEADER}


def test_the_verification_post_is_a_real_delivery_visible_in_the_log(h: Harness, sink: MemorySink) -> None:
    """A consumer without a live endpoint reads the code here; that is the
    fake's stand-in for their inbox, and it only works because the handshake
    goes through the dispatcher rather than around it."""
    registered = register(h)
    (record,) = all_deliveries(h)
    assert record["event_type"] == "verification"
    assert record["subscription_id"] == registered["id"]
    assert record["status"] == "delivered"
    assert record["body"]["verificationCode"] == verification_code(h, sink)


def test_an_unverified_callback_receives_no_events(h: Harness, sink: MemorySink) -> None:
    register(h)
    h.api.post("/__unit/webhooks/drain", {})
    sink.clear()
    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})
    assert sink.received == []


def test_pasting_the_code_verifies_the_callback_and_reveals_the_auth_code(h: Harness, sink: MemorySink) -> None:
    """Step 3: the code goes back in, the auth code comes out, and the record
    now says so through both the stand-in and the core's own list."""
    registered = register(h, eventKeys=["O", "I"])
    verified = verify(h, verification_code(h, sink))
    assert verified["id"] == registered["id"]
    assert verified["verified"] is True
    assert verified["eventKeys"] == ["O", "I"]
    assert len(verified["authCode"]) == UUID_SHAPE_LEN

    (core_view,) = h.api.get("/__unit/webhooks/subscriptions").json()["subscriptions"]
    assert core_view["id"] == registered["id"]
    assert core_view["event_types"] == ["O:*", "I:*"]
    assert core_view["signature_key"] == verified["authCode"]
    assert core_view["enabled"] is True


def test_a_verified_callback_receives_events_with_the_auth_code_in_x_clover_auth(h: Harness, sink: MemorySink) -> None:
    """The whole point: after the handshake, every delivery carries
    `X-Clover-Auth: <auth code>` and nothing else the core invented."""
    verified = register_and_verify(h, sink)
    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})

    (request,) = sink.received
    assert request.url == HOOKS
    assert request.headers[AUTH_HEADER] == verified["authCode"]
    assert verify_clover_auth(request.headers, verified["authCode"])
    assert not verify_clover_auth(request.headers, "some-other-code")
    assert set(request.headers) == {"content-type", INITIAL_DELIVERY_HEADER, AUTH_HEADER}
    assert request.headers["content-type"] == "application/json"
    body = json.loads(bytes(request.body).decode("utf-8"))
    assert body["merchants"][MERCHANT_ID][0]["objectId"] == "O:ORD0000000001"


def test_a_wrong_code_is_refused_and_changes_nothing(h: Harness, sink: MemorySink) -> None:
    register(h)
    response = h.api.post("/__clover/webhooks/verify", {"verificationCode": "not-the-code"})
    assert response.status == 400
    assert response.headers["x-unit-error"] == "invalid_value"
    assert first_error(response)["field"] == "verificationCode"
    (row,) = h.api.get("/__clover/webhooks/subscriptions").json()["subscriptions"]
    assert row["verified"] is False


def test_verifying_twice_is_idempotent(h: Harness, sink: MemorySink) -> None:
    register(h)
    code = verification_code(h, sink)
    assert verify(h, code) == verify(h, code)


def test_a_missing_url_is_a_missing_field(h: Harness) -> None:
    response = h.api.post("/__clover/webhooks/subscriptions", {})
    assert response.status == 400
    assert response.headers["x-unit-error"] == "missing_field"
    assert first_error(response)["field"] == "url"


def test_an_unknown_or_empty_event_key_is_refused(h: Harness) -> None:
    """`E` and `M` are documented keys nothing here mutates; accepting them
    would register a subscription that never fires."""
    for keys in (["E"], ["O", "M"], []):
        response = h.api.post("/__clover/webhooks/subscriptions", {"url": HOOKS, "eventKeys": keys})
        assert response.status == 400, keys
        assert response.headers["x-unit-error"] == "invalid_value"
        assert first_error(response)["field"] == "eventKeys"


# ---------------------------------------------------------------------------
# HTTPS only. DOCUMENTED: "Clover supports only HTTPS-enabled callbacks".
# ---------------------------------------------------------------------------

INSECURE_HOOKS = "http://localhost:9999/hooks"


def test_an_http_callback_is_refused_by_default_naming_the_documented_rule(h: Harness, sink: MemorySink) -> None:
    response = h.api.post("/__clover/webhooks/subscriptions", {"url": INSECURE_HOOKS})
    assert response.status == 400, response.text
    assert response.headers["x-unit-error"] == "invalid_value"
    assert first_error(response)["field"] == "url"
    assert "HTTPS-enabled callbacks" in response.json()["message"]
    assert h.api.get("/__clover/webhooks/subscriptions").json()["subscriptions"] == []
    h.api.post("/__unit/webhooks/drain", {})
    assert sink.received == []


@pytest.mark.parametrize("url", ["ftp://receiver.test/hooks", "receiver.test/hooks", "HTTP://receiver.test/hooks"])
def test_every_non_https_scheme_is_refused_not_only_http(h: Harness, url: str) -> None:
    response = h.api.post("/__clover/webhooks/subscriptions", {"url": url})
    assert response.status == 400, (url, response.text)
    assert first_error(response)["field"] == "url"


def test_https_is_always_accepted_whatever_the_switch_says(h: Harness) -> None:
    assert h.api.post("/__clover/webhooks/subscriptions", {"url": "HTTPS://receiver.test/hooks"}).status == 201


def test_the_allow_insecure_callbacks_switch_lifts_the_check_for_a_local_receiver(sink: MemorySink) -> None:
    """JUDGMENT, fake-only: a receiver on http://localhost has no certificate
    to offer. Reached through the vendor block's environment layer, and read
    live, so the profile's value is the one in force."""
    env = {"VENDORFAKE_VENDOR_ALLOW_INSECURE_CALLBACKS": "true"}
    for h in build_harness("full", sink=sink, env=env):
        assert h.unit.context.vendor.config.allow_insecure_callbacks is True  # type: ignore[attr-defined]
        verified = register_and_verify(h, sink, INSECURE_HOOKS)
        assert verified["url"] == INSECURE_HOOKS
        create_order(h)
        h.api.post("/__unit/webhooks/drain", {})
        (request,) = sink.received
        assert request.url == INSECURE_HOOKS
        assert request.headers[AUTH_HEADER] == verified["authCode"]


def test_a_profile_declared_http_subscriber_is_exempt_and_still_delivers(sink: MemorySink) -> None:
    """The dashboard's pre-verified entries are not a request to this route;
    a scenario may point them at whatever its author's receiver listens on."""
    env = {"VENDORFAKE_WEBHOOK_URL": INSECURE_HOOKS, "VENDORFAKE_WEBHOOK_SIGNATURE_KEY": "seeded-auth-code"}
    for h in build_harness("full", sink=sink, env=env):
        assert h.unit.context.vendor.config.allow_insecure_callbacks is False  # type: ignore[attr-defined]
        (row,) = h.api.get("/__clover/webhooks/subscriptions").json()["subscriptions"]
        assert row["url"] == INSECURE_HOOKS
        assert row["verified"] is True
        create_order(h)
        h.api.post("/__unit/webhooks/drain", {})
        (request,) = sink.received
        assert request.url == INSECURE_HOOKS


def test_every_stand_in_summary_says_it_is_not_a_clover_endpoint() -> None:
    """Route has no flag for 'vendor route that is not the vendor's API'
    (konyklabs/roadmap#38), so the prose is the mark a generated client
    carries: it must open the summary, not be buried in it."""
    for h in build_harness("full"):
        routes = [r for r in h.api.get("/__unit/routes").json()["routes"] if r["path"].startswith("/__clover/")]
        assert len(routes) == 3
        assert all(r["summary"].startswith("Stand-in (not a Clover endpoint)") for r in routes), routes


def test_registering_while_delivery_is_disabled_is_refused_not_left_hanging(h: Harness, sink: MemorySink) -> None:
    """JUDGMENT: with delivery off, `enqueue_to` no-ops, no record carries the
    code, and verify could never succeed. A 201 would be a registration that
    can only hang; a 503 says so and names the alternative."""
    h.unit.context.webhooks.set_enabled(False)
    response = h.api.post("/__clover/webhooks/subscriptions", {"url": HOOKS})
    assert response.status == 503, response.text
    assert response.headers["x-unit-error"] == "unavailable"
    assert "disable_delivery" in response.json()["message"]
    assert "/__unit/webhooks/subscriptions" in response.json()["message"]
    assert h.api.get("/__clover/webhooks/subscriptions").json()["subscriptions"] == []
    assert sink.received == []


def test_an_emitted_event_of_the_verification_type_still_carries_the_auth_header(h: Harness, sink: MemorySink) -> None:
    """POST /__unit/webhooks/emit accepts any type string. The signer must
    recognise the unit's *own* verification POST by its id as well, or the
    emitter is a way to push an unauthenticated delivery to every verified
    callback by naming the type."""
    verified = register_and_verify(h, sink)
    emitted = h.api.post(
        "/__unit/webhooks/emit",
        {"type": "verification", "entity_id": verified["id"], "body": {"verificationCode": "forged"}},
    )
    assert emitted.status == 202, emitted.text
    h.api.post("/__unit/webhooks/drain", {})
    assert sink.received == []

    # Even a subscriber that asked for everything gets the header on it.
    h.api.post(
        "/__unit/webhooks/subscriptions",
        {"id": "wbhk_star", "notification_url": OTHER_HOOKS, "event_types": ["*"], "signature_key": "star-code"},
    )
    h.api.post("/__unit/webhooks/emit", {"type": "verification", "entity_id": "wbhk_star", "body": {}})
    h.api.post("/__unit/webhooks/drain", {})
    (request,) = sink.received
    assert request.url == OTHER_HOOKS
    assert request.headers[AUTH_HEADER] == "star-code"


def test_the_unverified_verification_reading_is_published_as_judgment(h: Harness) -> None:
    """The doc says the code is sent 'after the callback URL is validated'
    and nothing about the verification POST; /__unit/info must present the
    unauthenticated verification POST as a reading, not a fact."""
    described = h.api.get("/__unit/info").json()["signer"]
    assert described["verification"].startswith("JUDGMENT:")
    assert "after the webhook callback URL is validated" in described["verification"]


def test_ids_and_codes_are_deterministic_per_seed() -> None:
    """Two units on the same profile mint the same subscription id, so a
    transcript of the handshake diffs between runs."""
    seen: list[dict[str, Any]] = []
    for _ in range(2):
        for h in build_harness("full"):
            seen.append(register(h))
    assert seen[0] == seen[1]
    assert seen[0]["id"].startswith("wbhk_")


# ---------------------------------------------------------------------------
# Per-key filtering. JUDGMENT: models the dashboard's per-key subscription.
# ---------------------------------------------------------------------------


def test_a_callback_subscribed_to_one_key_receives_only_that_key(h: Harness, sink: MemorySink) -> None:
    register_and_verify(h, sink, HOOKS, eventKeys=["I"])
    register_and_verify(h, sink, OTHER_HOOKS, eventKeys=["O"])
    create_order(h)
    create_item(h)
    h.api.post("/__unit/webhooks/drain", {})
    received = sorted(
        (r.url, json.loads(bytes(r.body).decode())["merchants"][MERCHANT_ID][0]["objectId"]) for r in sink.received
    )
    assert received == [(HOOKS, "I:ITM0000000001"), (OTHER_HOOKS, "O:ORD0000000001")]


def test_the_default_is_every_key(h: Harness, sink: MemorySink) -> None:
    register_and_verify(h, sink)
    create_order(h)
    create_item(h)
    h.api.post("/__unit/webhooks/drain", {})
    assert len(sink.received) == 2


# ---------------------------------------------------------------------------
# Pre-verified subscribers: the control plane and a profile's subscribers block.
# ---------------------------------------------------------------------------


def test_a_control_plane_subscriber_is_pre_verified_with_its_key_as_the_auth_code(h: Harness, sink: MemorySink) -> None:
    """The documented dashboard flow has no API, so a subscriber that arrives
    already carrying a code is treated as one the dashboard verified earlier.
    The stand-in's list reports it as verified with that code."""
    created = h.api.post(
        "/__unit/webhooks/subscriptions",
        {"id": "wbhk_ctl", "notification_url": HOOKS, "event_types": ["O:*"], "signature_key": "pre-shared-code"},
    )
    assert created.status == 201
    (row,) = h.api.get("/__clover/webhooks/subscriptions").json()["subscriptions"]
    assert row == {"id": "wbhk_ctl", "url": HOOKS, "eventKeys": ["O"], "verified": True, "authCode": "pre-shared-code"}

    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})
    (request,) = sink.received
    assert request.headers[AUTH_HEADER] == "pre-shared-code"


def test_a_profile_declared_subscriber_is_delivered_to_with_its_auth_code(sink: MemorySink) -> None:
    """The `webhooks.subscribers` block the core already supports, reached
    through the environment layer so no second profile file is needed."""
    env = {
        "VENDORFAKE_WEBHOOK_URL": HOOKS,
        "VENDORFAKE_WEBHOOK_EVENTS": "*",
        "VENDORFAKE_WEBHOOK_SIGNATURE_KEY": "seeded-auth-code",
    }
    for h in build_harness("full", sink=sink, env=env):
        (row,) = h.api.get("/__clover/webhooks/subscriptions").json()["subscriptions"]
        assert row["verified"] is True
        assert row["eventKeys"] == ["O", "I", "C", "P"]
        assert row["authCode"] == "seeded-auth-code"
        create_order(h)
        h.api.post("/__unit/webhooks/drain", {})
        (request,) = sink.received
        assert request.headers[AUTH_HEADER] == "seeded-auth-code"


def test_registering_does_not_notify_anybody_that_somebody_registered(h: Harness, sink: MemorySink) -> None:
    """Registering is a committed mutation of the subscriptions collection,
    which the dispatcher ignores; the only delivery is the verification POST
    to the new callback itself."""
    register_and_verify(h, sink, OTHER_HOOKS)
    register(h)
    h.api.post("/__unit/webhooks/drain", {})
    assert [r.url for r in sink.received] == [HOOKS]


# ---------------------------------------------------------------------------
# Retries. JUDGMENT throughout: Clover documents no retry policy.
# ---------------------------------------------------------------------------


def test_a_non_2xx_answer_is_retried_on_the_declared_backoff_shape(h: Harness, sink: MemorySink) -> None:
    """ "The response ... needs to be a 200 OK code." A 500 is retried; the
    `full` profile scales 30s and 2m to 5ms and 20ms, and the *ratio* is the
    declared one."""
    register_and_verify(h, sink)
    sink.respond_with = lambda _req, index: 500 if index < 2 else 200
    create_order(h)

    log = deliveries(h)
    assert [record["status"] for record in log] == ["failed", "failed", "delivered"]
    assert [record["retry_number"] for record in log] == [0, 1, 2]
    assert log[0]["next_attempt_in_ms"] == 5
    assert log[1]["next_attempt_in_ms"] == 20
    assert len({record["event_id"] for record in log}) == 1
    assert RETRY_NUMBER_HEADER not in log[0]["headers"]
    assert log[1]["headers"][RETRY_NUMBER_HEADER] == "1"
    assert log[1]["headers"][RETRY_REASON_HEADER] == "http_error"
    # The auth header is the same on every attempt: nothing about the attempt
    # enters into it.
    assert len({record["headers"][AUTH_HEADER] for record in log}) == 1


def test_a_timed_out_subscriber_is_retried_with_the_timeout_reason(h: Harness, sink: MemorySink) -> None:
    register_and_verify(h, sink)
    sink.respond_with = lambda _req, index: 0 if index == 0 else 200
    create_order(h)
    log = deliveries(h)
    assert [record["status"] for record in log] == ["failed", "delivered"]
    assert log[1]["headers"][RETRY_REASON_HEADER] == "timeout"


def test_the_whole_declared_schedule_runs_uncompressed_on_the_virtual_clock(virtual: Harness, sink: MemorySink) -> None:
    """Five retries after the initial send, at the JUDGMENT intervals, over
    2h42m30s of unit time -- then `exhausted`, and the subscriber is left
    alone. `time_scale` is put back to 1.0 so the assertion is about the
    declared numbers, which only a virtual clock makes affordable."""
    register_and_verify(virtual, sink)
    sink.respond_with = 500
    assert virtual.api.post("/__unit/webhooks/retry-policy", {"time_scale": 1.0}).status == 200

    started_at = virtual.unit.context.clock.now()
    create_order(virtual)
    log = deliveries(virtual)

    assert len(log) == len(CLOVER_RETRY_SCHEDULE_MS) + 1 == 6
    assert [record["status"] for record in log] == ["failed"] * 5 + ["exhausted"]
    assert [record["next_attempt_in_ms"] for record in log[:-1]] == list(CLOVER_RETRY_SCHEDULE_MS)
    assert "next_attempt_in_ms" not in log[-1]
    assert virtual.unit.context.clock.now() - started_at == sum(CLOVER_RETRY_SCHEDULE_MS)
    assert len({record["headers"][INITIAL_DELIVERY_HEADER] for record in log}) == 1
    assert [record["headers"][RETRY_NUMBER_HEADER] for record in log[1:]] == ["1", "2", "3", "4", "5"]


# ---------------------------------------------------------------------------
# Capability gating.
# ---------------------------------------------------------------------------


def test_with_webhooks_off_the_stand_in_is_a_501_and_a_mutation_delivers_nothing(sink: MemorySink) -> None:
    """Not a 404: a consumer must be able to tell "this unit does not do
    webhooks" from "you typed the path wrong". And the gate is at the point
    of delivery, so a mutation with a pre-verified subscriber in place still
    reaches nobody."""
    env = {"VENDORFAKE_CAPABILITIES": "-webhooks"}
    for h in build_harness("full", sink=sink, env=env):
        response = h.api.post("/__clover/webhooks/subscriptions", {"url": HOOKS})
        assert response.status == 501
        assert response.headers["x-unit-error"] == "capability_disabled"
        assert response.headers["x-unit-capability"] == "webhooks"

        h.unit.context.store.collection("subscriptions").insert(
            {"id": "wbhk_off", "notification_url": HOOKS, "event_types": ["*"], "signature_key": "k"},
            {"source": "test"},
        )
        create_order(h)
        assert deliveries(h) == []
        assert sink.received == []


def test_switching_webhooks_back_on_at_runtime_delivers_again(h: Harness, sink: MemorySink) -> None:
    """The gate is evaluated per event, not once at construction."""
    register_and_verify(h, sink)
    original = [row["name"] for row in h.api.get("/__unit/capabilities").json()["capabilities"] if row["enabled"]]
    off = h.api.post("/__unit/capabilities", {"set": [n for n in original if not n.startswith("webhooks")]})
    assert off.status == 200, off.text
    create_order(h, "ORD0000000001")
    assert deliveries(h) == []
    back = h.api.post("/__unit/capabilities", {"set": original})
    assert back.status == 200, back.text
    create_order(h, "ORD0000000002")
    (record,) = deliveries(h)
    assert record["entity_id"] == "ORD0000000002"


def test_no_stand_in_route_authenticates_and_none_is_internal() -> None:
    """The dashboard is the developer's console: open, like the control
    plane beside it, and a vendor route rather than an internal one so the
    capability gate and chaos still apply."""
    for h in build_harness("full"):
        routes = [r for r in h.api.get("/__unit/routes").json()["routes"] if r["path"].startswith("/__clover/")]
        assert len(routes) == 3
        assert all(r.get("auth") is None for r in routes)
        assert all(r["capability"] == "webhooks" for r in routes)
        assert not any(r["internal"] for r in routes)
