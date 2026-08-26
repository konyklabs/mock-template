"""Semantics of the one place a fault is armed.

Three claims, and everything here serves one of them: the capability gate runs
before anything is parsed, a per-request trigger leaks nothing into global
state, and the two entry points are gated by two different capabilities.
"""

from __future__ import annotations

import pytest

from vendorfake.core.capability.registry import CapabilityRegistry
from vendorfake.core.chaos.engine import ChaosEngine, ChaosSubject
from vendorfake.core.chaos.selector import FaultSelector
from vendorfake.core.kernel.magic import MagicExtraction, extract_magic
from vendorfake.core.kernel.types import CapabilityDecl, MagicTriggerSpec, UnitRequest
from vendorfake.core.rand.rng import Rng
from vendorfake.core.time.clock import Clock

START = "2024-01-01T00:00:00Z"

DECLS = (
    CapabilityDecl(name="orders", summary="Orders."),
    CapabilityDecl(name="chaos", summary="Request faults.", kind="behavior"),
    CapabilityDecl(name="webhooks", summary="Signed delivery."),
    CapabilityDecl(
        name="webhooks.chaos",
        summary="Delivery faults.",
        kind="behavior",
        requires=("webhooks", "chaos"),
    ),
)
ALL_ON = ("orders", "chaos", "webhooks", "webhooks.chaos")

SPEC = MagicTriggerSpec(prefix="chaos:", body_paths=("order.reference_id",))


def registry(*enabled: str) -> CapabilityRegistry:
    return CapabilityRegistry(DECLS, (), enabled or ALL_ON, "test")


def build(*rules: object, enabled: tuple[str, ...] = ALL_ON) -> tuple[FaultSelector, ChaosEngine]:
    clock = Clock(mode="virtual", start=START)
    unit = ChaosEngine(Rng(7), clock.iso_ms, list(rules))
    return FaultSelector(unit, registry(*enabled)), unit


def post_orders() -> ChaosSubject:
    return ChaosSubject(
        scope="request",
        route_key="POST /v2/orders",
        method="POST",
        path="/v2/orders",
        capability="orders",
    )


def request() -> UnitRequest:
    return UnitRequest(
        id="req-1",
        method="POST",
        path="/v2/orders",
        query={},
        headers={},
        raw_body=b"",
        transport="inprocess",
        received_at=START,
    )


def magic(value: str | None) -> MagicExtraction:
    body = {} if value is None else {"order": {"reference_id": value}}
    return extract_magic(SPEC, request(), body)


# ---------------------------------------------------------------------------
# One-shot leak-proofing. This is the point of the module.
# ---------------------------------------------------------------------------


def test_a_one_shot_trigger_leaves_global_config_and_every_counter_untouched() -> None:
    """The losing bake-off entry got the one-shot half right -- it never mutated
    global chaos config -- and this keeps that. What it did not have is the
    guarantee below it: no standing rule's counters move either, because the
    selector returns before the standing-rule loop is entered and the only
    counter writer is `ChaosEngine.evaluate`."""
    selector, unit = build({"id": "rl", "scope": "request", "fault": "rate_limit", "when": {"nth": [1]}})
    before_rules = [status.as_json() for status in unit.status()]
    before_enabled = unit.is_enabled

    selection = selector.select_request(post_orders(), lambda: magic("chaos:timeout:delay_ms=15"))

    assert selection.source == "in_band"
    assert selection.decision is not None
    assert selection.decision.fault == "timeout"
    assert selection.decision.rule_id == "magic"
    assert selection.decision.occurrence == 1
    assert dict(selection.decision.params) == {"delay_ms": "15"}

    assert [status.as_json() for status in unit.status()] == before_rules
    assert unit.is_enabled is before_enabled


def test_a_one_shot_trigger_does_not_consume_a_standing_rule_budget() -> None:
    """The consequence a consumer actually meets. A rule set to fire on its
    second match must still fire on its second match; the request carrying the
    magic value did not count as its first."""
    selector, _unit = build({"id": "rl", "scope": "request", "fault": "rate_limit", "when": {"nth": [2]}})

    assert selector.select_request(post_orders(), lambda: magic("chaos:server_error")).source == "in_band"

    first = selector.select_request(post_orders(), lambda: magic(None))
    assert first.decision is None
    second = selector.select_request(post_orders(), lambda: magic(None))
    assert second.decision is not None
    assert second.decision.rule_id == "rl"
    assert second.decision.occurrence == 2


def test_a_one_shot_fire_records_exactly_one_event_under_the_id_magic() -> None:
    """Recorded divergence from the reference, which audits nothing for a magic
    fire and leaves a consumer debugging such a run with no trail at all --
    against the engine's own stated purpose. `/__unit/chaos` is therefore
    identical across `enabled`, `seed` and every counter, and carries one new
    event; the conformance check excludes `events` for this reason and says so."""
    selector, unit = build({"id": "rl", "scope": "request", "fault": "rate_limit"})

    selector.select_request(post_orders(), lambda: magic("chaos:timeout"))

    events = unit.events()
    assert len(events) == 1
    assert events[0].rule_id == "magic"
    assert events[0].fault == "timeout"
    assert events[0].subject == "POST /v2/orders"
    assert events[0].occurrence == 1


def test_an_explicit_per_request_instruction_beats_a_standing_rule() -> None:
    selector, _ = build({"id": "rl", "scope": "request", "fault": "rate_limit"})
    selection = selector.select_request(post_orders(), lambda: magic("chaos:server_error"))
    assert selection.decision is not None
    assert selection.decision.fault == "server_error"


# ---------------------------------------------------------------------------
# The gate, and the order it runs in.
# ---------------------------------------------------------------------------


def test_a_per_request_trigger_is_refused_when_its_capability_is_off() -> None:
    """The defect the choke point exists to make unrepresentable. The losing
    entry merged a per-request chaos header over the global config with no
    capability check anywhere on that path, so a unit with fault injection
    switched off still injected faults for any caller who knew the header
    name."""
    selector, unit = build(enabled=("orders", "webhooks"))

    selection = selector.select_request(post_orders(), lambda: magic("chaos:server_error"))

    assert selection.decision is None
    assert selection.source == "none"
    assert selection.in_band_faults == ()
    assert dict(selection.in_band_params) == {}
    assert unit.events() == ()


def test_with_the_gate_shut_the_request_is_never_even_scanned() -> None:
    """`in_band` is a callable and not a value precisely so that the capability
    check genuinely precedes the parse rather than merely preceding the use of
    its result."""
    selector, _ = build(enabled=("orders",))
    calls: list[int] = []

    def scan() -> MagicExtraction:
        calls.append(1)
        return magic("chaos:server_error")

    assert selector.select_request(post_orders(), scan).decision is None
    assert calls == []


def test_with_the_gate_shut_standing_rules_do_not_fire_or_count_either() -> None:
    """`chaos` gates request-scope faults from EVERY source, which is what makes
    it the total off switch a conformance check can rely on."""
    selector, unit = build({"id": "rl", "scope": "request", "fault": "rate_limit"}, enabled=("orders",))
    assert selector.select_request(post_orders()).decision is None
    assert unit.status()[0].matches == 0
    assert unit.events() == ()


def test_the_engine_toggle_does_not_veto_an_in_band_trigger() -> None:
    """Ported: the reference's pipeline bypasses `evaluate` entirely when a
    magic value is present, so `enabled: false` never reaches it. The two
    switches answer different questions -- 'stop the scenario I configured'
    versus 'this deployment does not inject faults' -- and a consumer who then
    writes a magic value has explicitly asked for this one."""
    selector, unit = build({"id": "rl", "scope": "request", "fault": "rate_limit"})
    unit.set_enabled(False)

    assert selector.select_request(post_orders(), lambda: magic(None)).decision is None
    armed = selector.select_request(post_orders(), lambda: magic("chaos:timeout"))
    assert armed.decision is not None
    assert armed.decision.fault == "timeout"


def test_the_gate_is_silent_rather_than_raising() -> None:
    """A request to a unit with fault injection off is an ordinary request with
    an ordinary response, not a 501."""
    selector, _ = build(enabled=("orders",))
    assert selector.select_request(post_orders()).decision is None


# ---------------------------------------------------------------------------
# Two entry points, two capabilities.
# ---------------------------------------------------------------------------


def test_webhook_scope_is_gated_on_webhooks_chaos_and_not_on_chaos() -> None:
    """Collapsing the two gates would change WHICH capability disables delivery
    faults. A profile that wants request faults but honest delivery is a real
    configuration and one gate cannot express it."""
    selector, _ = build(
        {"id": "d", "scope": "webhook", "fault": "webhook.drop"},
        {"id": "r", "scope": "request", "fault": "rate_limit"},
        enabled=("orders", "chaos", "webhooks"),
    )
    assert selector.select_webhook(ChaosSubject(scope="webhook", event_type="order.created")) is None
    assert selector.select_request(post_orders()).decision is not None


def test_webhook_scope_fires_when_its_own_capability_is_on() -> None:
    selector, _ = build({"id": "d", "scope": "webhook", "fault": "webhook.drop"})
    decision = selector.select_webhook(ChaosSubject(scope="webhook", event_type="order.created"))
    assert decision is not None
    assert decision.fault == "webhook.drop"


def test_disabling_chaos_takes_webhook_chaos_with_it() -> None:
    """`webhooks.chaos` declares `requires=('webhooks', 'chaos')`, checked by the
    core rather than trusted to a vendor: a unit with fault injection switched
    off that nonetheless drops webhooks would be lying about itself."""
    selector, _ = build({"id": "d", "scope": "webhook", "fault": "webhook.drop"}, enabled=("orders", "webhooks"))
    assert selector.select_webhook(ChaosSubject(scope="webhook", event_type="order.created")) is None


def test_each_entry_point_refuses_the_other_scope() -> None:
    """A programming error, not a consumer error. Silently evaluating a webhook
    subject through the request gate would apply the wrong capability, which is
    the exact mistake the two entry points exist to prevent."""
    selector, _ = build()
    with pytest.raises(ValueError):
        selector.select_request(ChaosSubject(scope="webhook", event_type="order.created"))
    with pytest.raises(ValueError):
        selector.select_webhook(post_orders())


# ---------------------------------------------------------------------------
# The reported result.
# ---------------------------------------------------------------------------


def test_a_standing_rule_decision_reports_its_source_and_no_in_band_fields() -> None:
    selector, _ = build({"id": "rl", "scope": "request", "fault": "rate_limit"})
    selection = selector.select_request(post_orders(), lambda: magic(None))
    assert selection.source == "rule"
    assert selection.in_band_faults == ()


def test_the_in_band_fields_are_published_for_the_armed_request_only() -> None:
    """The reference wrote these into a per-request scratch object and then read
    them nowhere. Publishing them on the result keeps extraction pure and
    removes the scratch; publishing them EMPTY under a shut gate means a
    disabled unit hands a vendor nothing to remember to ignore."""
    selector, _ = build()
    armed = selector.select_request(post_orders(), lambda: magic("chaos:timeout:delay_ms=15"))
    assert armed.in_band_faults == ("timeout",)
    assert dict(armed.in_band_params) == {"delay_ms": "15"}
