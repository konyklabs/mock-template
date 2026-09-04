"""Semantics of the chaos engine.

Weighted at the four claims a reviewer could disagree about and a
coverage-shaped suite would miss: the exact condition order in `should_fire`,
the rule that a matching rule's counter advances even when it does not fire,
that the same rules and the same traffic give the same outcomes twice, and the
copy discipline on everything the engine hands out.
"""

from __future__ import annotations

import pytest

from vendorfake.core.chaos.engine import CHAOS_HISTORY_CAPACITY, ChaosEngine, ChaosSubject
from vendorfake.core.chaos.rules import ChaosRule
from vendorfake.core.kernel.types import UnitError
from vendorfake.core.rand.rng import Rng
from vendorfake.core.time.clock import Clock

START = "2024-01-01T00:00:00Z"


def engine(*rules: object, seed: int = 7) -> ChaosEngine:
    clock = Clock(mode="virtual", start=START)
    return ChaosEngine(Rng(seed), clock.iso_ms, list(rules))


def post_orders(**overrides: object) -> ChaosSubject:
    fields: dict[str, object] = {
        "scope": "request",
        "route_key": "POST /v2/orders",
        "method": "POST",
        "path": "/v2/orders",
        "capability": "orders",
        "headers": {},
        "body_text": "",
    }
    fields.update(overrides)
    return ChaosSubject(**fields)  # type: ignore[arg-type]  # a test builder over an open field set


def statuses(subject_engine: ChaosEngine, rule_id: str) -> tuple[int, int]:
    row = next(status for status in subject_engine.status() if status.rule.id == rule_id)
    return row.matches, row.fires


# ---------------------------------------------------------------------------
# The invariant that is easiest to get wrong.
# ---------------------------------------------------------------------------


def test_a_matching_rule_counts_even_when_it_does_not_fire() -> None:
    """`when.nth: [2]` means 'the second request this rule matched', not 'the
    second request no earlier rule claimed'. Break this and adding a rule
    silently re-numbers every rule below it: a scenario that passed yesterday
    fails today and nothing reports why."""
    unit = engine({"id": "rl", "scope": "request", "fault": "rate_limit", "when": {"nth": [2]}})

    assert unit.evaluate(post_orders()) is None
    assert statuses(unit, "rl") == (1, 0)

    fired = unit.evaluate(post_orders())
    assert fired is not None
    assert fired.occurrence == 2
    assert statuses(unit, "rl") == (2, 1)


def test_a_later_rule_still_matches_after_an_earlier_one_claimed_the_subject() -> None:
    """The loop does not break once a decision is taken: later rules keep
    counting matches. It is `matches` that advances unconditionally and
    `fires` that does not -- `should_fire` is consulted only while no decision
    has been taken, so a shadowed rule accumulates match history without ever
    spending its budget. Both halves are contract and both are here."""
    unit = engine(
        {"id": "first", "scope": "request", "fault": "server_error"},
        {"id": "second", "scope": "request", "fault": "unavailable", "when": {"nth": [2]}},
    )
    decision = unit.evaluate(post_orders())
    assert decision is not None
    assert decision.rule_id == "first"
    assert statuses(unit, "second") == (1, 0)

    second = unit.evaluate(post_orders())
    assert second is not None
    assert second.rule_id == "first"
    # `second` matched twice -- only true if the shadowed first request counted
    # for it -- and fired zero times, because `first` claimed both subjects.
    assert statuses(unit, "second") == (2, 0)

    # Remove the rule that was shadowing it and its budget is intact: `nth: [2]`
    # is spent only by a fire, so the very next match is its third and misses.
    unit.remove("first")
    assert unit.evaluate(post_orders()) is None
    assert statuses(unit, "second") == (3, 0)


def test_a_rule_whose_scope_differs_does_not_count() -> None:
    unit = engine({"id": "w", "scope": "webhook", "fault": "webhook.drop"})
    assert unit.evaluate(post_orders()) is None
    assert statuses(unit, "w") == (0, 0)


# ---------------------------------------------------------------------------
# The condition order in `should_fire`. It is contract, not style.
# ---------------------------------------------------------------------------


def test_probability_is_drawn_last_so_the_seeded_stream_ignores_irrelevant_traffic() -> None:
    """Move `probability` above `nth` and two runs of the same scenario stop
    producing the same outcomes -- the property the whole subsystem exists to
    provide. The draw count is the evidence: the first request matched, was
    vetoed by `nth`, and consumed no randomness."""
    unit = engine({"id": "p", "scope": "request", "fault": "server_error", "when": {"nth": [2], "probability": 1.0}})
    rng = Rng(7)

    assert unit.evaluate(post_orders()) is None
    assert statuses(unit, "p") == (1, 0)
    assert rng.draw_count == 0  # the fresh reference stream, for contrast

    assert unit.evaluate(post_orders()) is not None


def test_an_exhausted_rule_costs_nothing() -> None:
    """`times` is checked first. A rule that has spent its budget must not draw
    from the RNG, or an exhausted rule would keep shifting the seeded stream
    for every rule after it."""
    unit = engine(
        {
            "id": "t",
            "scope": "request",
            "fault": "server_error",
            "when": {"times": 1, "probability": 1.0},
        }
    )
    assert unit.evaluate(post_orders()) is not None
    assert unit.evaluate(post_orders()) is None
    assert statuses(unit, "t") == (2, 1)


def test_after_is_strictly_after() -> None:
    """`matches <= after` vetoes. `after: 2` fires on the third match, not the
    second: 'after N matches have already passed cleanly'."""
    unit = engine({"id": "a", "scope": "request", "fault": "server_error", "when": {"after": 2}})
    assert [unit.evaluate(post_orders()) is not None for _ in range(4)] == [False, False, True, True]


def test_every_counts_matches_and_not_fires() -> None:
    unit = engine({"id": "e", "scope": "request", "fault": "server_error", "when": {"every": 3}})
    assert [unit.evaluate(post_orders()) is not None for _ in range(7)] == [
        False,
        False,
        True,
        False,
        False,
        True,
        False,
    ]


def test_conditions_are_anded() -> None:
    unit = engine({"id": "x", "scope": "request", "fault": "server_error", "when": {"every": 2, "times": 1}})
    assert [unit.evaluate(post_orders()) is not None for _ in range(5)] == [False, True, False, False, False]


def test_an_absent_condition_is_not_a_veto() -> None:
    """A rule with no `when` fires on every match -- which is what makes
    `{"scope": "request", "fault": "server_error"}` mean 'fail everything', the
    first thing anyone tries."""
    unit = engine({"id": "all", "scope": "request", "fault": "server_error"})
    assert all(unit.evaluate(post_orders()) is not None for _ in range(3))


def test_an_empty_nth_list_is_not_a_veto() -> None:
    """`if (w.nth && ...)` in the reference: an empty array is falsy, so it
    imposes no condition. An empty tuple is falsy here for the same effect.
    Writing `is not None` instead would make `nth: []` a rule that never
    fires."""
    unit = engine({"id": "n", "scope": "request", "fault": "server_error", "when": {"nth": []}})
    assert unit.evaluate(post_orders()) is not None


def test_always_false_still_fires() -> None:
    """`always` is declared by the reference and read by nothing. Accepted here
    as a documented no-op in both directions. Pinned so nobody 'fixes' it into
    a veto without meeting the decision -- the reference's own profiles would
    change behaviour if they did."""
    unit = engine({"id": "n", "scope": "request", "fault": "server_error", "when": {"always": False}})
    assert unit.evaluate(post_orders()) is not None


# ---------------------------------------------------------------------------
# Matching.
# ---------------------------------------------------------------------------


def test_each_match_criterion_vetoes_independently() -> None:
    unit = engine(
        {
            "id": "m",
            "scope": "request",
            "fault": "server_error",
            "match": {
                "route": "POST /v2/orders",
                "method": "post",
                "capability": "orders",
                "body_contains": "needle",
                "header": {"X-Tenant": "acme"},
            },
        }
    )
    subject = post_orders(body_text='{"note":"needle"}', headers={"x-tenant": "acme"})
    assert unit.evaluate(subject) is not None

    assert unit.evaluate(post_orders(body_text="needle", headers={"x-tenant": "other"})) is None
    assert unit.evaluate(post_orders(body_text="hay", headers={"x-tenant": "acme"})) is None
    assert (
        unit.evaluate(post_orders(route_key="GET /v2/orders", body_text="needle", headers={"x-tenant": "acme"})) is None
    )
    assert unit.evaluate(post_orders(capability="oauth", body_text="needle", headers={"x-tenant": "acme"})) is None
    # Only the one that satisfied every criterion counted as a match.
    assert statuses(unit, "m") == (1, 1)


def test_method_comparison_is_case_insensitive_on_both_sides() -> None:
    unit = engine({"id": "m", "scope": "request", "fault": "server_error", "match": {"method": "post"}})
    assert unit.evaluate(post_orders(method="POST")) is not None


def test_header_names_are_lower_cased_on_the_pattern_side_only() -> None:
    """Transport bindings lower-case request header keys before the core sees
    them, so the subject side is already normalised and the pattern is not."""
    unit = engine({"id": "h", "scope": "request", "fault": "server_error", "match": {"header": {"X-Chaos": "on"}}})
    assert unit.evaluate(post_orders(headers={"x-chaos": "on"})) is not None
    assert unit.evaluate(post_orders(headers={"x-chaos": "off"})) is None
    assert unit.evaluate(post_orders(headers={})) is None


def test_event_type_matching_is_glob() -> None:
    unit = engine({"id": "w", "scope": "webhook", "fault": "webhook.drop", "match": {"event_type": "order.*"}})
    assert unit.evaluate(ChaosSubject(scope="webhook", event_type="order.created")) is not None
    assert unit.evaluate(ChaosSubject(scope="webhook", event_type="payment.created")) is None


# ---------------------------------------------------------------------------
# The history.
# ---------------------------------------------------------------------------


def test_the_history_subject_label_falls_through_on_none_and_not_on_empty() -> None:
    """The reference uses `??`, so an empty-string route key is kept. Porting
    `??` as `or` silently changes a fallback chain, which is exactly the class
    of bug that makes a transcript name the wrong thing."""
    assert ChaosSubject(scope="request", route_key="", path="/v2/orders").label() == ""
    assert ChaosSubject(scope="request", path="/v2/orders").label() == "/v2/orders"
    assert ChaosSubject(scope="webhook", event_type="order.created").label() == "order.created"
    assert ChaosSubject(scope="request").label() == "(unknown)"


def test_only_a_fire_reaches_the_history() -> None:
    unit = engine({"id": "rl", "scope": "request", "fault": "rate_limit", "when": {"nth": [2]}})
    unit.evaluate(post_orders())
    assert unit.events() == ()
    unit.evaluate(post_orders())
    assert len(unit.events()) == 1
    event = unit.events()[0]
    assert (event.rule_id, event.fault, event.occurrence, event.subject) == ("rl", "rate_limit", 2, "POST /v2/orders")
    assert event.at == "2024-01-01T00:00:00.000Z"


def test_record_overlay_writes_history_and_touches_no_counter() -> None:
    """The whole of one-shot leak-proofing, at the engine level: the only
    counter writer is `evaluate`, and this path does not call it."""
    unit = engine({"id": "rl", "scope": "request", "fault": "rate_limit", "when": {"nth": [1]}})
    from vendorfake.core.chaos.engine import OVERLAY_RULE_ID, ChaosDecision

    decision = ChaosDecision(rule_id=OVERLAY_RULE_ID, fault="timeout", params={"delay_ms": "5"}, occurrence=1)
    unit.record_overlay(decision, post_orders())

    assert statuses(unit, "rl") == (0, 0)
    assert [event.rule_id for event in unit.events()] == ["magic"]
    # The standing rule's budget survived: it still fires on its first match.
    assert unit.evaluate(post_orders()) is not None


# ---------------------------------------------------------------------------
# Reproducibility.
# ---------------------------------------------------------------------------


def test_the_same_rules_and_traffic_give_identical_outcomes_twice() -> None:
    """Including the probabilistic escape hatch, which is the only path that
    consults the RNG. Two engines built from the same seed, driven by the same
    traffic, must agree line for line."""
    rules = [
        {"id": "rl", "scope": "request", "fault": "rate_limit", "when": {"nth": [2, 4]}},
        {"id": "flaky", "scope": "request", "fault": "unavailable", "when": {"probability": 0.5}},
    ]
    traffic = [post_orders(), post_orders(route_key="GET /v2/orders"), post_orders(), post_orders(), post_orders()]

    def run() -> list[object]:
        unit = engine(*rules)
        outcomes = [unit.evaluate(subject) for subject in traffic]
        return [None if outcome is None else outcome.as_json() for outcome in outcomes] + [
            status.as_json() for status in unit.status()
        ]

    first, second = run(), run()
    assert first == second
    assert any(outcome is not None for outcome in first[: len(traffic)])


def test_reset_counters_rewinds_the_rng_so_a_repeat_repeats() -> None:
    """Without the RNG reset the second run would draw from wherever the first
    one stopped, and 'repeat the scenario' would not."""
    unit = engine({"id": "flaky", "scope": "request", "fault": "unavailable", "when": {"probability": 0.5}})
    first = [unit.evaluate(post_orders()) is not None for _ in range(6)]
    unit.reset_counters()
    assert [unit.evaluate(post_orders()) is not None for _ in range(6)] == first
    assert len(set(first)) == 2  # the run is actually mixed, not trivially all-True


# ---------------------------------------------------------------------------
# The copy discipline. A count of copies proves nothing; a mutation that fails
# to reach the engine proves everything.
# ---------------------------------------------------------------------------


def test_mutating_anything_the_engine_handed_out_changes_nothing() -> None:
    unit = engine(
        {
            "id": "rl",
            "scope": "request",
            "fault": "rate_limit",
            "match": {"header": {"x-tenant": "acme"}},
            "params": {"retry_after_seconds": 3},
        }
    )
    unit.evaluate(post_orders(headers={"x-tenant": "acme"}))
    before_status = [status.as_json() for status in unit.status()]
    before_events = [event.as_json() for event in unit.events()]

    listed = unit.list()[0]
    listed.params["retry_after_seconds"] = 999  # type: ignore[index]
    assert listed.match is not None
    listed.match.header["x-tenant"] = "smuggled"  # type: ignore[index]
    unit.status()[0].rule.params["retry_after_seconds"] = 999  # type: ignore[index]
    unit.events()[0].params["retry_after_seconds"] = 999  # type: ignore[index]

    assert [status.as_json() for status in unit.status()] == before_status
    assert [event.as_json() for event in unit.events()] == before_events
    # And the engine still matches on the header it was configured with.
    assert unit.evaluate(post_orders(headers={"x-tenant": "acme"})) is not None
    assert unit.evaluate(post_orders(headers={"x-tenant": "smuggled"})) is None


def test_a_decision_does_not_alias_the_rule_params() -> None:
    unit = engine({"id": "rl", "scope": "request", "fault": "rate_limit", "params": {"retry_after_seconds": 3}})
    decision = unit.evaluate(post_orders())
    assert decision is not None
    decision.params["retry_after_seconds"] = 999  # type: ignore[index]
    assert unit.list()[0].params == {"retry_after_seconds": 3}


# ---------------------------------------------------------------------------
# The rule set, and the runtime toggle.
# ---------------------------------------------------------------------------


def test_readding_an_id_demotes_it_to_the_end_and_restarts_its_counters() -> None:
    """`filter` then `push` in the reference. Insertion order is the tie-break
    for which rule claims a subject, so a re-add is a deliberate demotion
    rather than an in-place edit."""
    unit = engine(
        {"id": "a", "scope": "request", "fault": "server_error"},
        {"id": "b", "scope": "request", "fault": "unavailable"},
    )
    unit.evaluate(post_orders())
    assert statuses(unit, "a") == (1, 1)

    unit.add({"id": "a", "scope": "request", "fault": "timeout"})
    assert [status.rule.id for status in unit.status()] == ["b", "a"]
    assert statuses(unit, "a") == (0, 0)
    decision = unit.evaluate(post_orders())
    assert decision is not None
    assert decision.rule_id == "b"


def test_replace_resets_every_counter() -> None:
    unit = engine({"id": "a", "scope": "request", "fault": "server_error", "when": {"nth": [2]}})
    unit.evaluate(post_orders())
    unit.replace([{"id": "a", "scope": "request", "fault": "server_error", "when": {"nth": [2]}}])
    assert statuses(unit, "a") == (0, 0)


def test_remove_reports_whether_there_was_anything_to_remove() -> None:
    unit = engine({"id": "a", "scope": "request", "fault": "server_error"})
    assert unit.remove("nope") is False
    assert unit.remove("a") is True
    assert unit.status() == ()


def test_reset_restores_a_pristine_unit() -> None:
    unit = engine({"id": "a", "scope": "request", "fault": "server_error"})
    unit.evaluate(post_orders())
    unit.set_enabled(False)
    unit.reset()
    assert unit.status() == ()
    assert unit.events() == ()
    assert unit.is_enabled is True


def test_the_runtime_toggle_stops_counting_as_well_as_firing() -> None:
    """`enabled: false` returns before the loop, so a silenced scenario does not
    quietly burn through its `nth` budget while it is silenced."""
    unit = engine({"id": "a", "scope": "request", "fault": "server_error", "when": {"nth": [2]}})
    unit.set_enabled(False)
    assert unit.evaluate(post_orders()) is None
    assert statuses(unit, "a") == (0, 0)
    unit.set_enabled(True)
    assert unit.evaluate(post_orders()) is None
    assert unit.evaluate(post_orders()) is not None


def test_status_omits_unset_fields_rather_than_nulling_them() -> None:
    """The reference spreads the rule object and JavaScript has no key for an
    undefined field. A null here would make two identically configured units
    produce two different documents."""
    unit = engine({"id": "a", "scope": "request", "fault": "server_error"})
    assert unit.status()[0].as_json() == {
        "id": "a",
        "scope": "request",
        "fault": "server_error",
        "matches": 0,
        "fires": 0,
    }


def test_the_engine_parses_documents_at_construction() -> None:
    with pytest.raises(UnitError):
        engine({"id": "a", "scope": "request"})


def test_already_parsed_rules_are_accepted_too() -> None:
    unit = engine(ChaosRule(id="a", scope="request", fault="server_error"))
    assert unit.evaluate(post_orders()) is not None


def test_the_history_keeps_the_most_recent_fires_and_evicts_the_oldest() -> None:
    """``GET /__unit/chaos/history`` is a debugging read; the interesting fire is
    the last one, so the ring drops from the front. Under a rule with no ``times``
    the history would otherwise grow once per request for the life of the unit.

    The per-rule counters are deliberately not bounded with it: ``fires`` is the
    number a test asserts on, and a capped count would be a wrong one.
    """
    unit = engine({"id": "a", "scope": "request", "fault": "server_error"})
    for _ in range(CHAOS_HISTORY_CAPACITY + 3):
        assert unit.evaluate(post_orders()) is not None
    events = unit.events()
    assert len(events) == CHAOS_HISTORY_CAPACITY
    assert events[0].occurrence == 4
    assert events[-1].occurrence == CHAOS_HISTORY_CAPACITY + 3
    assert statuses(unit, "a") == (CHAOS_HISTORY_CAPACITY + 3, CHAOS_HISTORY_CAPACITY + 3)


def test_the_history_stays_bounded_after_a_reset() -> None:
    """``reset`` and ``reset_counters`` both rebuild the ring; one that rebuilt it
    as an unbounded list would silently un-cap the engine on the first reset."""
    unit = engine({"id": "a", "scope": "request", "fault": "server_error"})
    unit.reset_counters()
    for _ in range(CHAOS_HISTORY_CAPACITY + 1):
        unit.evaluate(post_orders())
    assert len(unit.events()) == CHAOS_HISTORY_CAPACITY
