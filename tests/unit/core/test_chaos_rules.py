"""Semantics of the chaos rule grammar.

A rule document is written by a human, in a profile or in a control-plane
body, and the failure mode this file exists to prevent is the silent one: a
rule that parses, never fires, and reports nothing.
"""

from __future__ import annotations

import pytest

from vendorfake.core.chaos.rules import (
    BUILTIN_FAULTS,
    ChaosRule,
    glob_match,
    matched_routes,
    parse_rule,
)
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

MINIMAL = {"id": "r1", "scope": "request", "fault": "server_error"}


# ---------------------------------------------------------------------------
# The three error kinds are contract: they are what the control plane returns.
# ---------------------------------------------------------------------------


def test_a_missing_or_empty_id_is_missing_field() -> None:
    """The reference writes `if (!r.id)`, so absent and empty are the same
    answer. A bare `min_length` constraint would call the second one
    invalid_value, which is a different 400 for the same mistake."""
    for document in ({"scope": "request", "fault": "x"}, {"id": "", "scope": "request", "fault": "x"}):
        with pytest.raises(UnitError) as caught:
            parse_rule(document)
        assert caught.value.kind is UnitErrorKind.MISSING_FIELD
        assert caught.value.field == "id"


def test_a_missing_fault_is_missing_field() -> None:
    with pytest.raises(UnitError) as caught:
        parse_rule({"id": "r1", "scope": "request"})
    assert caught.value.kind is UnitErrorKind.MISSING_FIELD
    assert caught.value.field == "fault"


def test_an_absent_scope_is_invalid_value_and_not_missing_field() -> None:
    """Ported from validateRule, which reaches scope only after id and fault and
    then tests the *value*. A required Pydantic field would report
    missing_field, which is a kind the reference never emits for this."""
    for document in ({"id": "r1", "fault": "x"}, {"id": "r1", "fault": "x", "scope": "REQUEST"}):
        with pytest.raises(UnitError) as caught:
            parse_rule(document)
        assert caught.value.kind is UnitErrorKind.INVALID_VALUE
        assert caught.value.field == "scope"


def test_the_reference_checks_run_before_pydantic_structural_ones() -> None:
    """Order is contract: a document that is wrong in two ways reports the field
    the reference names, not whichever one Pydantic reached first."""
    with pytest.raises(UnitError) as caught:
        parse_rule({"fault": "x", "scope": "request", "when": {"nonsense": 1}})
    assert caught.value.field == "id"


# ---------------------------------------------------------------------------
# The silences the grammar closes.
# ---------------------------------------------------------------------------


def test_a_misspelled_condition_key_is_rejected() -> None:
    """The typo that actually happens. Under a permissive parser this is an
    UNCONDITIONAL rule -- shouldFire sees no recognised condition and fires on
    every match -- which is the loudest possible wrong behaviour arrived at by
    the quietest possible mistake."""
    with pytest.raises(UnitError) as caught:
        parse_rule({**MINIMAL, "when": {"nth_": [2]}})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "when.nth_"


def test_a_misspelled_match_key_is_rejected() -> None:
    with pytest.raises(UnitError) as caught:
        parse_rule({**MINIMAL, "match": {"eventType": "order.*"}})
    assert caught.value.field == "match.eventType"


def test_every_zero_is_rejected_at_the_document_rather_than_at_the_request() -> None:
    """`matches % 0` is NaN in JavaScript (the rule silently never fires) and
    ZeroDivisionError in Python (a 500 on the first matching request). Neither
    is actionable; a field-naming 400 at submission time is."""
    with pytest.raises(UnitError) as caught:
        parse_rule({**MINIMAL, "when": {"every": 0}})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "when.every"


def test_a_quoted_number_is_rejected_rather_than_coerced() -> None:
    """JavaScript's `matches % "3"` works. Accepting it would leave a profile
    carrying a quoted number for years with nobody learning which was meant."""
    with pytest.raises(UnitError):
        parse_rule({**MINIMAL, "when": {"every": "3"}})
    with pytest.raises(UnitError):
        parse_rule({**MINIMAL, "when": {"nth": ["2"]}})


def test_probability_is_bounded_to_a_probability() -> None:
    with pytest.raises(UnitError):
        parse_rule({**MINIMAL, "when": {"probability": 1.5}})
    assert parse_rule({**MINIMAL, "when": {"probability": 1}}).when is not None


def test_a_non_object_document_is_invalid_value() -> None:
    with pytest.raises(UnitError) as caught:
        parse_rule(["not", "a", "rule"])
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "rule"


def test_params_stay_opaque() -> None:
    """Per-fault, and a fork may add faults the core never heard of. Validating
    the shape here would be the core asserting a vendor's vocabulary."""
    rule = parse_rule({**MINIMAL, "params": {"anything": {"nested": [1, 2]}}})
    assert rule.params == {"anything": {"nested": [1, 2]}}


# ---------------------------------------------------------------------------
# Glob matching, which decides what a rule applies to.
# ---------------------------------------------------------------------------


def test_glob_is_anchored_at_both_ends() -> None:
    assert glob_match("POST /v2/orders", "POST /v2/orders") is True
    assert glob_match("POST /v2/orders", "POST /v2/orders/search") is False
    assert glob_match("POST /v2/orders*", "POST /v2/orders/search") is True
    assert glob_match("*/v2/orders", "POST /v2/orders") is True


def test_star_is_the_only_metacharacter() -> None:
    """A route key is full of regex specials -- braces, slashes, dots. If they
    were live, `GET /v2/orders/{order_id}` would match almost nothing and the
    failure would look like the rule simply not firing."""
    assert glob_match("GET /v2/orders/{order_id}", "GET /v2/orders/{order_id}") is True
    assert glob_match("GET /v2/orders/{order_id}", "GET /v2/orders/xorder_idx") is False
    assert glob_match("a.b", "axb") is False
    assert glob_match("a+b", "a+b") is True


def test_matched_routes_reports_what_a_rule_actually_selects() -> None:
    """Zero is the answer a consumer needs at the moment the rule is written --
    a colon-style path template selects nothing, forever, silently."""
    routes = ("POST /v2/orders", "GET /v2/orders/{order_id}", "POST /v2/orders/search")
    stale = ChaosRule(id="r", scope="request", fault="x", match={"route": "GET /v2/orders/:order_id"})
    assert matched_routes(stale, routes) == ()
    braced = ChaosRule(id="r", scope="request", fault="x", match={"route": "GET /v2/orders/{order_id}"})
    assert matched_routes(braced, routes) == ("GET /v2/orders/{order_id}",)
    wild = ChaosRule(id="r", scope="request", fault="x", match={"route": "POST /v2/orders*"})
    assert matched_routes(wild, routes) == ("POST /v2/orders", "POST /v2/orders/search")


def test_a_rule_with_no_route_constraint_selects_every_route() -> None:
    """Reporting zero here would contradict the engine, which applies such a
    rule to everything in its scope."""
    routes = ("POST /v2/orders", "GET /v2/orders/{order_id}")
    assert matched_routes(ChaosRule(id="r", scope="request", fault="x"), routes) == routes
    by_event = ChaosRule(id="r", scope="webhook", fault="x", match={"event_type": "order.*"})
    assert matched_routes(by_event, routes) == routes


# ---------------------------------------------------------------------------
# The published fault catalogue.
# ---------------------------------------------------------------------------


def test_builtin_fault_names_are_unique_and_scoped() -> None:
    names = [spec.name for spec in BUILTIN_FAULTS]
    assert len(names) == len(set(names))
    assert {spec.scope for spec in BUILTIN_FAULTS} == {"request", "webhook"}
    assert all(spec.name.startswith("webhook.") for spec in BUILTIN_FAULTS if spec.scope == "webhook")


def test_the_param_catalogue_promises_snake_case_keys() -> None:
    """The wire convention, and a promise the fault implementations must keep:
    a fault reads `delay_ms`, not `delayMs`."""
    described = {spec.name: spec.params for spec in BUILTIN_FAULTS}
    assert described["timeout"] == "delay_ms (default 100)"
    assert described["rate_limit"] == "retry_after_seconds?"
    assert described["webhook.duplicate"] == "copies (default 1 extra)"
    assert described["server_error"] is None
    assert BUILTIN_FAULTS[0].as_json() == {
        "name": "rate_limit",
        "scope": "request",
        "summary": "Reject the request as rate limited.",
        "provenance": "vendor",
        "phase": "request",
        "params": "retry_after_seconds?",
    }


def test_provenance_distinguishes_vendor_faults_from_transport_ones() -> None:
    """``vendor``: this reproduces something a real vendor does. ``transport``:
    no vendor documents it, because it is what any HTTP dependency can do to a
    response independent of which vendor is behind it. See
    ``core/chaos/faults.py``'s response-scope faults and the README's
    "Transport faults" section.
    """
    by_name = {spec.name: spec.provenance for spec in BUILTIN_FAULTS}
    assert by_name["rate_limit"] == "vendor"
    assert by_name["timeout"] == "vendor"
    assert by_name["webhook.duplicate"] == "vendor"
    for name in ("malformed_body", "body_mutation", "connection_reset", "empty_response", "slow_body"):
        assert by_name[name] == "transport", name
