"""What a malformed control-plane body is answered with, exactly.

The invariant under test is one sentence: *no control route can answer 500 for
a syntactically valid JSON body*. Every assertion below is either a kind, a
field path, or a round trip -- the three things a consumer can act on and the
three things an unvalidated ``readJson<T>()`` cannot give them.
"""

from __future__ import annotations

import pytest

from vendorfake.core.control.schemas import (
    CapabilitiesBody,
    ChaosResetBody,
    ChaosRulesBody,
    ClockAdvanceBody,
    JournalEntryDocument,
    MachineProbeBody,
    RetryPolicyPatchBody,
    SinkProgramBody,
    SnapshotDocument,
    StateRestoreBody,
    SubscriptionCreateBody,
    idempotency_as_json,
    journal_entry_as_json,
    parse_or_raise,
    require_finite,
    snapshot_as_json,
)
from vendorfake.core.kernel.types import JournalEntry, UnitError, UnitErrorKind
from vendorfake.core.state.store import IdempotencyRecord, Store
from vendorfake.core.time.clock import Clock

# ---------------------------------------------------------------------------
# parse_or_raise: the one adapter between Pydantic and the core's vocabulary
# ---------------------------------------------------------------------------


def test_a_missing_required_field_is_missing_field_and_names_the_field() -> None:
    """Not `invalid_value`, and not a 500. The reference hand-wrote
    `throw new UnitError('missing_field', {field: 'notificationUrl'})` for
    exactly this body; the mapping has to reproduce the kind AND the field, or
    a consumer is told "something was wrong" about a body they cannot fix."""
    with pytest.raises(UnitError) as caught:
        parse_or_raise(SubscriptionCreateBody, {"name": "mine"})
    assert caught.value.kind is UnitErrorKind.MISSING_FIELD
    assert caught.value.field == "notification_url"


def test_a_wrong_type_is_invalid_value_not_missing_field() -> None:
    """The split matters: `missing_field` tells a consumer to add something,
    `invalid_value` tells them to change something."""
    with pytest.raises(UnitError) as caught:
        parse_or_raise(SubscriptionCreateBody, {"notification_url": 17})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "notification_url"


def test_a_missing_field_wins_over_a_type_error_elsewhere_in_the_same_body() -> None:
    """Two faults, one report. The absent field is chosen because it is the one
    the caller must fix first: supplying it may well change what the other
    field is validated against."""
    with pytest.raises(UnitError) as caught:
        parse_or_raise(SubscriptionCreateBody, {"enabled": "yes"})
    assert caught.value.kind is UnitErrorKind.MISSING_FIELD
    assert caught.value.field == "notification_url"


@pytest.mark.parametrize("body", [[], "text", 3, None])
def test_a_body_that_is_not_an_object_names_the_body_and_not_an_empty_path(body: object) -> None:
    """Pydantic reports `loc: ()` for this, which would produce an error naming
    no field at all -- the least actionable 400 there is."""
    with pytest.raises(UnitError) as caught:
        parse_or_raise(CapabilitiesBody, body)
    assert (caught.value.kind, caught.value.field) == (UnitErrorKind.INVALID_VALUE, "body")


def test_a_misspelled_key_is_refused_rather_than_ignored() -> None:
    """`{"keepRules": true}` on a snake_case body. Silently ignoring it is how
    "I asked you to keep my rules and you wiped them" happens, and it is
    indistinguishable from a bug in the unit."""
    with pytest.raises(UnitError) as caught:
        parse_or_raise(ChaosResetBody, {"keepRules": True})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "keepRules"


def test_a_quoted_boolean_is_refused_because_javascript_would_call_it_true() -> None:
    """`Boolean("false")` is `true`. A consumer round-tripping a flag through a
    shell or a query string sends this, and honouring it means the unit does
    the opposite of what was asked, forever, silently."""
    with pytest.raises(UnitError) as caught:
        parse_or_raise(ChaosResetBody, {"keep_rules": "false"})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE


def test_a_quoted_float_is_accepted_because_every_shell_produces_one() -> None:
    """The other half of the same decision: numbers stay lax. The reference
    coerced with `Number(...)` and a query-string round trip is one keystroke
    away from every consumer."""
    assert parse_or_raise(RetryPolicyPatchBody, {"time_scale": "0.5"}).time_scale == 0.5


# ---------------------------------------------------------------------------
# The chaos body: three shapes in one, and none of them strict
# ---------------------------------------------------------------------------


def test_a_bare_toggle_carries_no_rule_at_all() -> None:
    """`{"enabled": false}` is the reference's own toggle test and must be a
    valid body with no `id` and no `fault`. A single strict model cannot
    express that, which is why this one is not strict."""
    body = parse_or_raise(ChaosRulesBody, {"enabled": False})
    assert body.enabled is False
    assert body.rule_document() is None
    assert body.rules is None


def test_a_bare_rule_lands_in_the_extras_untouched() -> None:
    """The rule grammar is stated once, in chaos/rules.py. This body forwards
    the document rather than restating its fields, so a misspelled `when` key
    is caught by the grammar's own `extra="forbid"` and not by a second,
    diverging copy here."""
    document = {"id": "r1", "scope": "request", "fault": "rate_limit", "when": {"nth": [2]}}
    body = parse_or_raise(ChaosRulesBody, document)
    assert body.rule_document() == document
    assert (body.rules, body.enabled) == (None, None)


def test_a_replacement_set_and_a_toggle_coexist_without_being_read_as_a_rule() -> None:
    body = parse_or_raise(ChaosRulesBody, {"rules": [], "enabled": True})
    assert body.rules == ()
    assert body.enabled is True
    assert body.rule_document() is None


def test_a_quoted_enabled_is_refused_even_on_the_permissive_chaos_body() -> None:
    """`extra="allow"` widens what keys are accepted, not what types are."""
    with pytest.raises(UnitError):
        parse_or_raise(ChaosRulesBody, {"enabled": "false"})


# ---------------------------------------------------------------------------
# The probe body: `from` is a Python keyword and the wire name is the contract
# ---------------------------------------------------------------------------


def test_the_probe_body_reads_from_and_not_from_underscore() -> None:
    body = parse_or_raise(MachineProbeBody, {"machine": "order", "from": "OPEN", "to": "COMPLETED"})
    assert (body.machine, body.from_, body.to) == ("order", "OPEN", "COMPLETED")


def test_the_probe_body_refuses_the_python_spelling_of_from() -> None:
    """`populate_by_name` is off deliberately: accepting `from_` would give one
    request two spellings, and no other language's client would ever produce
    the second."""
    with pytest.raises(UnitError) as caught:
        parse_or_raise(MachineProbeBody, {"machine": "order", "from_": "OPEN"})
    assert caught.value.kind is UnitErrorKind.MISSING_FIELD


def test_the_probe_bodys_to_is_optional_because_its_absence_selects_a_question() -> None:
    """Without `to` the probe asks `assert_mutable`; with it, `assert_transition`."""
    assert parse_or_raise(MachineProbeBody, {"machine": "order", "from": "COMPLETED"}).to is None


# ---------------------------------------------------------------------------
# Sparse patches, finite numbers
# ---------------------------------------------------------------------------


def test_a_retry_patch_carries_only_the_keys_the_body_actually_set() -> None:
    """`model_fields_set`, never `model_dump`. A dump would send every default
    as a value and a request to change one multiplier would reset the vendor's
    documented eleven-interval schedule to empty."""
    assert parse_or_raise(RetryPolicyPatchBody, {"time_scale": 2.0}).patch() == {"time_scale": 2.0}


def test_a_retry_patch_lists_its_schedule_so_the_policy_can_hold_it() -> None:
    assert parse_or_raise(RetryPolicyPatchBody, {"schedule_ms": [1, 2]}).patch() == {"schedule_ms": [1, 2]}


def test_an_empty_retry_patch_asks_for_nothing() -> None:
    assert parse_or_raise(RetryPolicyPatchBody, {}).patch() == {}


def test_an_infinite_millisecond_count_is_refused_by_name() -> None:
    """JSON has no `Infinity` literal, but `1e400` parses to one in both
    languages, and an infinite advance is an unbounded loop, not an error."""
    body = parse_or_raise(ClockAdvanceBody, {"ms": 1e400})
    with pytest.raises(UnitError) as caught:
        require_finite(body.ms, field="ms")
    assert (caught.value.kind, caught.value.field) == (UnitErrorKind.INVALID_VALUE, "ms")


def test_a_finite_millisecond_count_passes_through_unchanged() -> None:
    assert require_finite(2.5, field="ms") == 2.5


def test_a_sink_programme_needs_at_least_one_status() -> None:
    with pytest.raises(UnitError) as caught:
        parse_or_raise(SinkProgramBody, {"statuses": []})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE


# ---------------------------------------------------------------------------
# Absent is absent, in both directions
# ---------------------------------------------------------------------------


def test_an_insert_entry_carries_no_from_version_key_at_all() -> None:
    """Not `"from_version": null`. An insert has no prior version, and the
    reference emits no key -- which is also what makes the document round-trip
    through JournalEntryDocument, whose `extra="forbid"` would still accept a
    null but whose consumers would then see a value where there is none."""
    entry = JournalEntry(
        seq=1,
        at="2024-01-01T00:00:00.000Z",
        collection="orders",
        id="o1",
        op="insert",
        from_version=None,
        to_version=1,
        changed=("id",),
        meta=None,
    )
    body = journal_entry_as_json(entry)
    assert "from_version" not in body
    assert "meta" not in body
    assert body["to_version"] == 1


def test_a_journal_entry_round_trips_through_its_own_published_shape() -> None:
    """The published shape and the accepted shape are one model. If they ever
    diverge, a snapshot becomes readable but not restorable."""
    entry = JournalEntry(
        seq=4,
        at="2024-01-01T00:00:00.000Z",
        collection="orders",
        id="o1",
        op="update",
        from_version=1,
        to_version=2,
        changed=("state",),
        meta={"operation_id": "PayOrder"},
    )
    assert JournalEntryDocument.model_validate(journal_entry_as_json(entry)).to_entry() == entry


def test_an_idempotency_record_round_trips_through_its_published_shape() -> None:
    record = IdempotencyRecord(
        scope="CreateOrder",
        key="k-1",
        request_digest="d",
        status=200,
        headers={"content-type": "application/json"},
        body_b64="e30",
        stored_at="2024-01-01T00:00:00.000Z",
    )
    body = idempotency_as_json(record)
    restored = SnapshotDocument.model_validate(
        {"collections": {}, "journal": [], "idempotency": [body], "seq": 0}
    ).to_snapshot()
    assert restored.idempotency == [record]


def test_a_whole_snapshot_round_trips_and_preserves_the_digest() -> None:
    """The end-to-end claim: take a snapshot here, restore it into a second
    store, and the two hash the same. That is what makes "pin a scenario" a
    capability rather than a report."""
    source = Store(Clock("real"))
    orders = source.collection("orders")
    orders.insert({"id": "o1", "state": "OPEN"})
    orders.update("o1", lambda draft: draft.__setitem__("state", "COMPLETED"))
    source.put_idempotent(
        IdempotencyRecord(
            scope="CreateOrder",
            key="k-1",
            request_digest="d",
            status=200,
            headers={},
            body_b64="e30",
            stored_at="2024-01-01T00:00:00.000Z",
        )
    )

    document = snapshot_as_json(source.snapshot())
    target = Store(Clock("real"))
    target.restore(SnapshotDocument.model_validate(document).to_snapshot())

    assert target.entity_digest() == source.entity_digest()
    assert target.journal_seq == source.journal_seq
    assert target.stats() == source.stats()
    assert [e.seq for e in target.journal()] == [e.seq for e in source.journal()]
    assert target.get_idempotent("CreateOrder", "k-1") is not None


def test_a_restore_body_with_no_snapshot_parses_so_the_handler_can_name_it() -> None:
    """Optional in the model, required by the handler. A Pydantic-required
    field would report the same kind but the handler would lose its own
    wording, which is the reference's ('snapshot is required')."""
    assert parse_or_raise(StateRestoreBody, {}).snapshot is None
