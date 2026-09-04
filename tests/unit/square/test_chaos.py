"""Fault injection through the Square surface, from both trigger mechanisms.

The reference's chaos suite is the one this file mirrors, and it exists to
prove two claims that only a *vendor* surface can prove:

* a fault is shaped by the vendor. A rate limit is not "429"; it is Square's
  ``RATE_LIMITED`` in Square's envelope with Square's ``retry-after``. The core
  raises a neutral ``UnitError`` and the shaper decides what a consumer reads,
  so an assertion on the code is an assertion about the seam.
* neither trigger mechanism consults a random number generator. ``nth``,
  ``every``, ``after`` and ``times`` are counters, and the magic values are
  textual, so "the third create fails" is a fact a test can assert rather than
  a flake to be tolerated.

Two divergences from the reference, both deliberate and both stated where they
occur: the delivery *log* here carries a body hash rather than the body, so the
out-of-order test reads versions from the sink's raw bytes; and an in-band
overlay fire is recorded in ``/__unit/chaos``'s ``events`` with
``rule_id="magic"``, where the reference records nothing at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.square.events import ORDER_CREATED, ORDER_UPDATED
from vendorfake.square.seed.constants import (
    SEED_KIOSK_LOCATION_ID,
    SEED_LOCATION_ID,
    SEED_OPEN_ORDER_ID,
    TEA_MUG_VARIATION_ID,
)

HOOKS = "https://subscriber.test/hooks"


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def h(sink: MemorySink) -> Iterator[Harness]:
    """The full profile: every capability on, and a real clock, which is the
    profile the reference's own timing assertion was written against."""
    yield from build_harness("full", sink=sink)


def add_rule(h: Harness, **rule: Any) -> dict[str, Any]:
    response = h.api.post("/__unit/chaos/rules", rule)
    assert response.status == 200, response.text
    return dict(response.json())


def order_body(key: str, **overrides: Any) -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "order": {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"catalog_object_id": TEA_MUG_VARIATION_ID, "quantity": "2"}],
            **overrides,
        },
    }


def create(h: Harness, key: str, **overrides: Any) -> Any:
    return h.api.post("/v2/orders", order_body(key, **overrides), headers=h.auth)


def subscribe(h: Harness, event_types: list[str]) -> str:
    response = h.api.post(
        "/__unit/webhooks/subscriptions",
        {"notification_url": HOOKS, "event_types": event_types, "signature_key": "test-signature-key"},
    )
    assert response.status == 201, response.text
    return str(response.json()["subscription"]["id"])


def deliveries(h: Harness) -> list[dict[str, Any]]:
    h.api.post("/__unit/webhooks/drain", {})
    return list(h.api.get("/__unit/webhooks/deliveries").json()["deliveries"])


# ---------------------------------------------------------------------------
# Request-scope faults, from a control-plane rule.
# ---------------------------------------------------------------------------


def test_a_rule_rate_limits_exactly_the_requests_it_names(h: Harness) -> None:
    """`nth: [2, 4]` is a claim about which calls fail, not how many.

    The 429 body is Square's: https://developer.squareup.com/reference/square/objects/Error
    `retry-after` is asserted as the string "3" because the parameter arrives
    as JSON here and as text on the magic path, and `str(3.0)` is "3.0".
    """
    add_rule(
        h,
        id="rl",
        scope="request",
        fault="rate_limit",
        match={"route": "POST /v2/orders"},
        when={"nth": [2, 4]},
        params={"retry_after_seconds": 3},
    )

    statuses: list[int] = []
    for index in range(5):
        response = create(h, f"rl-{index}")
        statuses.append(response.status)
        if response.status == 429:
            error = first_error(response)
            assert error["code"] == "RATE_LIMITED"
            assert error["category"] == "RATE_LIMIT_ERROR"
            assert response.headers["retry-after"] == "3"
    assert statuses == [200, 429, 200, 429, 200]

    # The rule named one route; every other route was untouched by it.
    assert h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=h.auth).status == 200


def test_a_rule_expires_the_token_mid_flow_without_changing_stored_state(h: Harness) -> None:
    """`token_expiry` fires *after* authentication succeeded, so it is
    distinguishable from an ordinary 401 -- the call either side of it works
    with the same credential."""
    add_rule(
        h,
        id="expire",
        scope="request",
        fault="token_expiry",
        match={"route": "GET /v2/orders/{order_id}"},
        when={"nth": [2]},
    )

    assert h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=h.auth).status == 200
    expired = h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=h.auth)
    assert expired.status == 401
    assert first_error(expired)["code"] == "ACCESS_TOKEN_EXPIRED"
    # The token was never revoked, so the next call succeeds.
    assert h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=h.auth).status == 200


def test_a_rule_injects_a_shaped_five_hundred(h: Harness) -> None:
    add_rule(h, id="boom", scope="request", fault="server_error", match={"route": "POST /v2/orders"}, when={"nth": [1]})
    response = create(h, "boom-1")
    assert response.status == 500
    error = first_error(response)
    assert error["code"] == "INTERNAL_SERVER_ERROR"
    assert error["category"] == "API_ERROR"


def test_a_timeout_fault_declares_its_delay_and_then_fails_the_request(h: Harness) -> None:
    """The delay is *declared*, not slept, and this binding does not carry it out.

    ``InProcessClient`` holds no caller: it is a function call, so there is
    nobody to make wait and nothing to time out. It hands the delay back on
    ``.raw.delay_ms`` and elapsed time here stays a measurement of the unit.
    The bindings that do hold a caller -- the ``httpx`` transport, the ASGI
    application, the file drop -- honour it, and are tested where they live
    (``tests/unit/test_async_seam.py``, ``tests/integration``).

    This used to assert ``elapsed_ms >= 20`` against a ``time.sleep`` in the
    kernel. See ``vendorfake.core.chaos.faults`` for why that went.
    """
    import time

    add_rule(
        h,
        id="slow",
        scope="request",
        fault="timeout",
        match={"route": "POST /v2/orders"},
        when={"nth": [1]},
        params={"delay_ms": 25},
    )
    started = time.monotonic()
    response = create(h, "slow-1")
    elapsed_ms = (time.monotonic() - started) * 1000

    assert response.status == 504
    assert first_error(response)["code"] == "GATEWAY_TIMEOUT"
    assert response.raw.delay_ms == 25
    assert elapsed_ms < 25, f"the unit waited {elapsed_ms:.1f}ms for a delay no binding asked it to take"


def test_a_rejected_request_creates_nothing(h: Harness) -> None:
    """The fault fires before the handler, so the store never sees the write.

    Two seeded orders, and neither of the two refused creates added a third.
    """
    add_rule(h, id="boom", scope="request", fault="server_error", match={"route": "POST /v2/orders"}, when={})
    assert create(h, "gone-1").status == 500
    assert create(h, "gone-2").status == 500
    found = h.api.post(
        "/v2/orders/search",
        # Required: "Your request must include one or more `location_ids`."
        # https://developer.squareup.com/docs/orders-api/manage-orders/search-orders
        {"location_ids": [SEED_LOCATION_ID, SEED_KIOSK_LOCATION_ID]},
        headers=h.auth,
    ).json()["orders"]
    assert len(found) == 2


# ---------------------------------------------------------------------------
# Request-scope faults, from a magic value in an ordinary field.
# ---------------------------------------------------------------------------


def test_a_magic_value_in_an_ordinary_field_triggers_a_fault(h: Harness) -> None:
    """The in-band path exists for a consumer driving the unit through an SDK
    that cannot reach the control plane. `reference_id` is a field Square
    documents as free text, so an SDK can always set it."""
    limited = create(h, "magic-1", reference_id="chaos:rate_limit")
    assert limited.status == 429
    assert first_error(limited)["code"] == "RATE_LIMITED"

    slow = create(h, "magic-2", reference_id="chaos:timeout:delay_ms=15")
    assert slow.status == 504

    # The magic value affects only the request that carries it.
    assert create(h, "magic-3").status == 200


def test_a_magic_value_does_not_advance_a_standing_rule(h: Harness) -> None:
    """The overlay short-circuits rather than merging into the matching loop.

    This is the leak the design forbids structurally: if the in-band path ran
    through the standing rules, the magic request would consume the `nth: [1]`
    budget and the next ordinary create would come back 200.
    """
    add_rule(
        h, id="standing", scope="request", fault="rate_limit", match={"route": "POST /v2/orders"}, when={"nth": [1]}
    )
    assert create(h, "overlay-1", reference_id="chaos:server_error").status == 500

    status = h.api.get("/__unit/chaos").json()
    assert status["rules"][0]["matches"] == 0
    assert status["rules"][0]["fires"] == 0

    # The standing rule still has its first occurrence to spend.
    assert create(h, "overlay-2").status == 429


def test_an_overlay_fire_is_audited_under_the_rule_id_magic(h: Harness) -> None:
    """A judgment call, and a divergence from the reference stated in place.

    The reference records nothing for an in-band fire, which leaves a consumer
    debugging a magic-driven run with no audit trail at all -- against the
    engine's own stated purpose. Here the fire is appended to `events` with
    `rule_id="magic"`, which is also what lets a conformance check tell an
    overlay fire from a standing one.
    """
    assert create(h, "audit-1", reference_id="chaos:rate_limit").status == 429
    events = h.api.get("/__unit/chaos").json()["events"]
    assert len(events) == 1
    assert events[0]["rule_id"] == "magic"
    assert events[0]["fault"] == "rate_limit"


# ---------------------------------------------------------------------------
# Delivery-scope faults.
# ---------------------------------------------------------------------------


def test_a_duplicated_delivery_carries_the_same_event_id(h: Harness) -> None:
    """At-least-once, made observable. A consumer that does not dedupe on
    `event_id` processes the order twice, and this is the rule that shows it."""
    subscribe(h, [ORDER_CREATED])
    add_rule(
        h,
        id="dup",
        scope="webhook",
        fault="webhook.duplicate",
        match={"event_type": ORDER_CREATED},
        when={"nth": [1]},
        params={"copies": 1},
    )
    assert create(h, "dup-1").status == 200

    log = deliveries(h)
    assert len(log) == 2
    assert log[0]["event_id"] == log[1]["event_id"]
    assert log[0]["body_hash"] == log[1]["body_hash"]
    assert [row["status"] for row in log] == ["delivered", "delivered"]


def test_events_are_delivered_out_of_order_when_a_rule_says_so(h: Harness, sink: MemorySink) -> None:
    """The held event is released on the *next* enqueue, so the second update
    reaches the subscriber before the first.

    The versions are read from the sink's raw bytes rather than from the
    delivery log, which carries a body hash and a preview but not the body --
    a deliberate difference from the reference, and the stronger reading: what
    is asserted is what the subscriber actually received.
    """
    subscribe(h, [ORDER_UPDATED])
    add_rule(
        h,
        id="reorder",
        scope="webhook",
        fault="webhook.out_of_order",
        match={"event_type": ORDER_UPDATED},
        when={"nth": [1]},
    )
    for key, version, ticket in (("ooo-1", 1, "first"), ("ooo-2", 2, "second")):
        response = h.api.put(
            f"/v2/orders/{SEED_OPEN_ORDER_ID}",
            {"idempotency_key": key, "order": {"version": version, "ticket_name": ticket}},
            headers=h.auth,
        )
        assert response.status == 200, response.text

    log = deliveries(h)
    assert any(row["status"] == "skipped" for row in log)
    assert [row["status"] for row in log if row["status"] != "skipped"] == ["delivered", "delivered"]

    versions = [json.loads(req.body)["data"]["object"]["order_updated"]["version"] for req in sink.received]
    assert versions == [3, 2]


def test_a_dropped_acknowledgement_is_retried_even_though_the_subscriber_answered(h: Harness) -> None:
    """`webhook.drop_ack` discards a 200 the subscriber really sent, which is
    the failure mode a consumer cannot reproduce any other way: the delivery is
    recorded as failed and `response_status` is still 200."""
    subscribe(h, [ORDER_CREATED])
    add_rule(
        h,
        id="drop",
        scope="webhook",
        fault="webhook.drop_ack",
        match={"event_type": ORDER_CREATED},
        when={"nth": [1]},
    )
    assert create(h, "drop-1").status == 200

    log = deliveries(h)
    assert [row["status"] for row in log] == ["failed", "delivered"]
    assert log[0]["response_status"] == 200
    assert "chaos" in str(log[0]["error"])
    assert log[0]["event_id"] == log[1]["event_id"]


# ---------------------------------------------------------------------------
# Reproducibility, the toggle, and the audit trail.
# ---------------------------------------------------------------------------


def test_the_same_rule_and_the_same_traffic_give_the_same_answers() -> None:
    """Both the literal sequence and the equality, because a pure A-equals-B
    comparison would pass two units that shared one bug."""

    def run() -> list[int]:
        for unit in build_harness("full", sink=MemorySink()):
            add_rule(
                unit,
                id="every-third",
                scope="request",
                fault="rate_limit",
                match={"route": "POST /v2/orders"},
                when={"every": 3},
            )
            return [create(unit, f"rep-{index}").status for index in range(7)]
        raise AssertionError("the harness yielded nothing")

    first = run()
    second = run()
    assert first == [200, 200, 429, 200, 200, 429, 200]
    assert first == second


def test_chaos_can_be_switched_off_and_back_on_at_runtime(h: Harness) -> None:
    add_rule(
        h, id="toggle", scope="request", fault="rate_limit", match={"route": "POST /v2/orders"}, when={"always": True}
    )
    assert create(h, "t-1").status == 429

    assert h.api.post("/__unit/chaos/rules", {"enabled": False}).status == 200
    assert create(h, "t-2").status == 200

    assert h.api.post("/__unit/chaos/rules", {"enabled": True}).status == 200
    assert create(h, "t-3").status == 429

    assert h.api.post("/__unit/chaos/reset", {}).status == 200
    assert create(h, "t-4").status == 200


def test_the_engine_records_what_fired_so_a_failing_run_can_be_explained(h: Harness) -> None:
    add_rule(
        h, id="audited", scope="request", fault="rate_limit", match={"route": "POST /v2/orders"}, when={"nth": [2]}
    )
    create(h, "aud-1")
    create(h, "aud-2")

    status = h.api.get("/__unit/chaos").json()
    rule = status["rules"][0]
    assert rule["id"] == "audited"
    assert rule["matches"] == 2
    assert rule["fires"] == 1

    event = status["events"][0]
    assert event["rule_id"] == "audited"
    assert event["fault"] == "rate_limit"
    assert event["occurrence"] == 2
    assert event["subject"] == "POST /v2/orders"


def test_a_rule_naming_no_registered_route_is_refused_by_the_shipped_profiles(h: Harness) -> None:
    """`strict_rules` is on in every shipped profile, so the colon form the
    reference's profiles carried is a 400 naming the field rather than a rule
    that matches nothing, forever, silently."""
    response = h.api.post(
        "/__unit/chaos/rules",
        {"id": "dead", "scope": "request", "fault": "rate_limit", "match": {"route": "GET /v2/orders/:order_id"}},
    )
    assert response.status == 400
    assert response.headers["x-unit-error"] == "invalid_value"
    assert "{order_id}" in first_error(response)["detail"]


# ---------------------------------------------------------------------------
# The two capability gates.
# ---------------------------------------------------------------------------


def test_a_webhook_scope_rule_is_refused_when_webhooks_chaos_is_off() -> None:
    """`no-chaos` keeps the `chaos` capability and drops `webhooks.chaos`,
    which is exactly the reference's split: request faults are unaffected."""
    for unit in build_harness("no-chaos", sink=MemorySink()):
        refused = unit.api.post(
            "/__unit/chaos/rules",
            {"id": "nope", "scope": "webhook", "fault": "webhook.duplicate"},
        )
        assert refused.status == 501
        assert first_error(refused)["code"] == "NOT_IMPLEMENTED"
        assert refused.json()["unit_error"]["capability"] == "webhooks.chaos"
        assert refused.json()["unit_error"]["profile"] == "no-chaos"

        accepted = unit.api.post("/__unit/chaos/rules", {"id": "ok", "scope": "request", "fault": "rate_limit"})
        assert accepted.status == 200
        assert create(unit, "nc-1").status == 429


def test_the_no_faults_profile_injects_nothing_from_either_mechanism() -> None:
    """The new profile the sixth capability exists for.

    A rule is still *accepted* -- the grammar is valid and the control plane is
    not the gate -- and it never fires, from either trigger, and nothing is
    recorded. That combination is what distinguishes "the capability is off"
    from "the rule was rejected".
    """
    for unit in build_harness("no-faults", sink=MemorySink()):
        assert (
            unit.api.post(
                "/__unit/chaos/rules",
                {"id": "inert", "scope": "request", "fault": "rate_limit", "match": {"route": "POST /v2/orders"}},
            ).status
            == 200
        )

        assert create(unit, "nf-1").status == 200
        assert create(unit, "nf-2", reference_id="chaos:server_error").status == 200

        status = unit.api.get("/__unit/chaos").json()
        assert status["events"] == []
        assert status["rules"][0]["fires"] == 0
