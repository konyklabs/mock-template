"""The delivery vocabulary: matching, outcome classification, and the record.

Everything here is pure. What is being pinned is the set of decisions a
reviewer could reasonably have made differently -- the order of the outcome
tests, whether a glob escapes its dots, and whether an absent optional is
omitted or nulled -- because each of those is observable at
``/__unit/webhooks/deliveries`` or in what a subscriber receives.
"""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.types import PreparedEvent
from vendorfake.core.webhooks.models import (
    DELIVERY_STATUSES,
    DeliveryMetadata,
    DeliveryOutcome,
    DeliveryRecord,
    Subscription,
    matches_event_type,
)

EVENT = PreparedEvent(
    type="order.created", event_id="evt_1", entity_id="ord_1", created_at="2024-01-01T00:00:00.000Z", body={"a": 1}
)


# ---------------------------------------------------------------------------
# Event-type matching.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("patterns", "event_type", "expected"),
    [
        (("order.created",), "order.created", True),
        (("order.created",), "order.updated", False),
        (("*",), "anything.at.all", True),
        (("order.*",), "order.updated", True),
        (("order.*",), "payment.created", False),
        (("*.created",), "payment.created", True),
        (("payment.created", "order.created"), "order.created", True),
        ((), "order.created", False),
    ],
)
def test_matching_covers_the_three_documented_forms(patterns: tuple[str, ...], event_type: str, expected: bool) -> None:
    assert matches_event_type(patterns, event_type) is expected


def test_a_dot_in_a_pattern_is_a_dot_and_not_a_wildcard() -> None:
    """The reason the escape set exists at all.

    ``order.created`` reaches the glob branch only when it contains a ``*``, so
    this uses one -- and if ``.`` were left as a regex metacharacter,
    ``order.*`` would match ``orderXcreated``, and a subscriber asking for order
    events would receive somebody else's.
    """
    assert matches_event_type(("order.*",), "orderXcreated") is False
    assert matches_event_type(("order.*",), "order.created") is True


def test_other_metacharacters_are_escaped_too() -> None:
    """A pattern is a glob, not a regex: ``+`` means a plus sign."""
    assert matches_event_type(("order+*",), "order+created") is True
    assert matches_event_type(("order+*",), "orderrrr.created") is False


def test_a_star_inside_a_word_still_matches_a_run() -> None:
    assert matches_event_type(("or*ted",), "order.created") is True


# ---------------------------------------------------------------------------
# Outcome classification -- the neutral replacement for the vendor's strings.
# ---------------------------------------------------------------------------


def test_timed_out_is_consulted_before_the_status() -> None:
    """The order is contract, not taste.

    A sink reports a timeout as ``status == 0`` *and* ``timed_out``, so testing
    the status first would classify every timeout as a transport error and the
    two would be indistinguishable to the vendor mapping them onto wire
    strings. The second case is the one that proves the order rather than the
    outcome: a status that came back but was flagged as timed out is still a
    timeout.
    """
    assert DeliveryOutcome.of(0, timed_out=True) is DeliveryOutcome.TIMEOUT
    assert DeliveryOutcome.of(500, timed_out=True) is DeliveryOutcome.TIMEOUT


def test_no_status_without_a_timeout_is_a_transport_error() -> None:
    assert DeliveryOutcome.of(0, timed_out=False) is DeliveryOutcome.TRANSPORT_ERROR


def test_an_unsuccessful_status_is_an_http_error() -> None:
    assert DeliveryOutcome.of(500, timed_out=False) is DeliveryOutcome.HTTP_ERROR
    assert DeliveryOutcome.of(404, timed_out=False) is DeliveryOutcome.HTTP_ERROR


def test_there_are_exactly_three_outcomes_and_they_are_neutral() -> None:
    """A fourth would be a vendor's distinction leaking back in.

    The values are asserted literally because they are the core's half of the
    vendor contract: a vendor's outcome-to-wire-string map is keyed on them,
    and renaming one here would silently drop a vendor's retry-reason header.
    """
    assert [m.value for m in DeliveryOutcome] == ["timeout", "transport_error", "http_error"]


# ---------------------------------------------------------------------------
# The record.
# ---------------------------------------------------------------------------


def _record(**overrides: object) -> DeliveryRecord:
    base: dict[str, object] = {
        "id": "dlv_00001",
        "event_id": "evt_1",
        "event_type": "order.created",
        "entity_id": "ord_1",
        "subscription_id": "sub_1",
        "url": "https://sub.test/hooks",
        "attempt": 1,
        "retry_number": 0,
        "at": "2024-01-01T00:00:00.000Z",
        "status": "delivered",
        "response_status": 200,
        "body_hash": "abc",
        "body_preview": '{"a":1}',
    }
    base.update(overrides)
    return DeliveryRecord(**base)  # type: ignore[arg-type]


def test_absent_optionals_are_omitted_and_never_nulled() -> None:
    """Absence is absence, here as everywhere in this port.

    The reference leaves the keys ``undefined`` and ``JSON.stringify`` drops
    them. Python has no ``undefined``, so emitting them as ``null`` is the easy
    mistake -- and a consumer reading ``next_attempt_in_ms: null`` off a
    delivered record has to decide whether that means "no retry" or "retry in
    zero milliseconds".
    """
    payload = _record().as_json()
    assert "chaos" not in payload
    assert "error" not in payload
    assert "next_attempt_in_ms" not in payload
    assert "body" not in payload


def test_the_fourteen_always_present_keys_are_exactly_these() -> None:
    """The eighteen fields minus the four that are omitted when absent. Pinned
    as a set so a rename is a red test rather than a consumer's missing field."""
    assert set(_record().as_json()) == {
        "id",
        "event_id",
        "event_type",
        "entity_id",
        "subscription_id",
        "url",
        "attempt",
        "retry_number",
        "at",
        "status",
        "response_status",
        "body_hash",
        "body_preview",
        "headers",
    }


def test_present_optionals_are_published() -> None:
    payload = _record(
        body={"a": 1},
        body_is_json=True,
        chaos=("dup:webhook.duplicate",),
        error="boom",
        next_attempt_in_ms=10,
    ).as_json()
    assert payload["body"] == {"a": 1}
    assert payload["chaos"] == ["dup:webhook.duplicate"]
    assert payload["error"] == "boom"
    assert payload["next_attempt_in_ms"] == 10


def test_a_json_null_body_is_distinguishable_from_a_non_json_body() -> None:
    """The one thing Python can express that the reference cannot.

    ``body_is_json`` exists so that a vendor whose payload is protobuf keeps
    ``body_preview`` only, while a vendor that genuinely delivered the JSON
    document ``null`` gets a ``body`` key holding ``None``. Collapsing them
    would put ``"body": null`` on a record whose payload was never JSON.
    """
    assert "body" not in _record(body=None, body_is_json=False).as_json()
    assert _record(body=None, body_is_json=True).as_json()["body"] is None


def test_a_copy_does_not_share_its_mutable_parts() -> None:
    """``deliveries()`` hands out copies, and this is why it can.

    A caller that mutated a record's headers would rewrite the evidence a later
    assertion reads -- the delivery log is the transcript, and a transcript
    that can be edited by its reader is not evidence.
    """
    original = _record(headers={"a": "1"}, body={"nested": {"x": 1}}, body_is_json=True)
    duplicate = original.copy()
    duplicate.headers["a"] = "2"
    duplicate.body["nested"]["x"] = 99
    assert original.headers == {"a": "1"}
    assert original.body == {"nested": {"x": 1}}


def test_the_status_vocabulary_is_the_reference_five() -> None:
    assert DELIVERY_STATUSES == ("delivered", "failed", "exhausted", "skipped", "dropped")


# ---------------------------------------------------------------------------
# Subscription and metadata.
# ---------------------------------------------------------------------------


def test_a_subscription_reads_snake_case_entity_keys() -> None:
    sub = Subscription.from_entity(
        {
            "id": "sub_1",
            "name": "Example",
            "notification_url": "https://sub.test/hooks",
            "event_types": ["order.created"],
            "signature_key": "k",
            "enabled": True,
            "api_version": "2026-08-19",
        }
    )
    assert sub == Subscription(
        id="sub_1",
        notification_url="https://sub.test/hooks",
        event_types=("order.created",),
        signature_key="k",
        enabled=True,
        name="Example",
        api_version="2026-08-19",
    )


def test_a_subscription_with_no_enabled_key_is_enabled() -> None:
    """Absent means enabled, and only literal ``False`` disables.

    Coercing truthiness here would make ``"enabled": 0`` -- which is a defect in
    whatever wrote the entity -- silently disable a subscriber, and a silently
    disabled subscriber presents as "the webhook never arrived".
    """
    assert Subscription.from_entity(
        {"id": "s", "notification_url": "u", "event_types": [], "signature_key": "k"}
    ).enabled
    assert Subscription.from_entity({"id": "s", "enabled": False}).enabled is False
    assert Subscription.from_entity({"id": "s", "enabled": 0}).enabled is True


def test_metadata_states_the_retry_number_rather_than_deriving_it() -> None:
    """``attempt`` counts from one and ``retry_number`` from zero.

    Both are carried because a vendor's retry header uses the second and a
    human reads the first, and a vendor asked to subtract one gets it wrong
    once -- reporting the first send as retry number one, which tells a
    consumer their subscriber failed when it never had a chance to.
    """
    meta = DeliveryMetadata(
        event=EVENT,
        subscription_id="sub_1",
        notification_url="https://sub.test/hooks",
        attempt=1,
        retry_number=0,
        retry_reason=None,
        initial_delivery_at="2024-01-01T00:00:00.000Z",
    )
    assert meta.is_retry is False
    assert meta.attempt == meta.retry_number + 1

    retried = DeliveryMetadata(
        event=EVENT,
        subscription_id="sub_1",
        notification_url="https://sub.test/hooks",
        attempt=2,
        retry_number=1,
        retry_reason=DeliveryOutcome.HTTP_ERROR,
        initial_delivery_at="2024-01-01T00:00:00.000Z",
    )
    assert retried.is_retry is True
