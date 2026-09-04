"""C08, C12, C14, C27 -- fault injection is deterministic, leak-proof, gated, and counts honestly.

Three contracts about one subsystem, and each of them names a defect that has
actually shipped.

C08: the same rules and the same traffic must produce the same failures. A
fault engine that draws from a system random is a flake generator, and a
consumer cannot write "the second create fails" as a test against it.

C12: a per-request trigger must not touch standing state. The trap is not the
configuration -- which is easy to keep clean -- but the *counters*: a one-shot
that advances a standing rule's occurrence count turns "the second create
fails" into "the first", silently, and only for consumers who also use the
in-band trigger.

C14: a capability is only real if every entry point passes the same gate. The
losing bake-off entry read a per-request chaos header before any capability
check, so a unit with fault injection switched off still injected faults for
anyone who knew the header name.
"""

from __future__ import annotations

from typing import Any

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import Requires, require
from vendorfake.core.capability.gates import CoreCapability
from vendorfake.core.chaos.engine import OVERLAY_RULE_ID

__all__ = [
    "every_matching_rule_counts_its_match",
    "identical_rules_and_traffic_agree",
    "in_band_trigger_respects_the_capability_gate",
    "one_shot_chaos_does_not_leak",
]

_PROBE_FAULT = "rate_limit"
_PROBE_ERROR = "rate_limited"
_DETERMINISM_RULE = "conformance-determinism-probe"
_LEAK_RULE = "conformance-leak-probe"


def _standing_state(document: Any) -> dict[str, Any]:
    """Everything a one-shot must leave untouched.

    ``events`` is excluded, and the exclusion is the point rather than a
    convenience: an in-band fire IS recorded, deliberately, under the overlay
    rule id, because a magic-triggered failure that appears nowhere in the
    audit is a fake that is harder to debug than the system it stands in for.
    What must not move is the configuration and the counters, and those are
    what this compares.
    """
    return {
        "enabled": document["enabled"],
        "seed": document["seed"],
        "rules": document["rules"],
    }


def _install_rule(env: CheckEnv, rule: dict[str, Any]) -> None:
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    answered = env.client.call("POST", f"{CONTROL_PREFIX}chaos/rules", json_body=rule)
    require(
        answered.status == 200,
        f"POST /__unit/chaos/rules refused {rule['id']!r} with {answered.status}: {answered.text}. "
        f"The rule names a route taken from this unit's own /__unit/routes, so a refusal means the "
        f"rule grammar in core/chaos/rules.py and the route key in core/kernel/types.py::Route.key "
        f"disagree about how a route is spelled.",
    )


def _fires(env: CheckEnv, rule_id: str) -> int:
    for row in env.chaos()["rules"]:
        if row["id"] == rule_id:
            return int(row["fires"])
    raise AssertionError(f"rule {rule_id!r} vanished from /__unit/chaos")


@check(
    id="C08",
    name="chaos: identical rules and identical traffic produce identical outcomes",
    asserts=(
        "Two fresh units given the same rule and the same five requests answer with the same "
        "status:x-unit-error sequence, firing exactly twice, on the occurrences the rule named."
    ),
    requires=Requires(surface_route=True, chaos=True),
)
def identical_rules_and_traffic_agree(env: CheckEnv) -> str:
    def drive(unit: CheckEnv) -> tuple[tuple[str, ...], int]:
        route = unit.first_vendor_route()
        _install_rule(
            unit,
            {
                "id": _DETERMINISM_RULE,
                "scope": "request",
                "fault": _PROBE_FAULT,
                "match": {"route": route.key},
                "when": {"nth": [2, 4]},
            },
        )
        seen: list[str] = []
        for _ in range(5):
            answered = unit.client.call(route.method, route.probe_path, json_body={})
            seen.append(f"{answered.status}:{answered.error_kind or '-'}")
        return tuple(seen), _fires(unit, _DETERMINISM_RULE)

    first, first_fires = drive(env)
    with env.fresh() as other:
        second, second_fires = drive(other)

    require(
        first == second,
        f"two fresh units on profile {env.profile!r}, given the same rule and the same five "
        f"requests, answered differently:\n"
        f"  unit A: {list(first)}\n"
        f"  unit B: {list(second)}\n"
        f"Fault selection must be a function of the rule's counters and nothing else. A draw from "
        f"the RNG taken BEFORE the deterministic conditions are evaluated is the usual cause -- "
        f"core/chaos/engine.py must consult `when.probability` last, and only when it is set.",
    )
    fired_at = [index + 1 for index, entry in enumerate(first) if entry.endswith(_PROBE_ERROR)]
    require(
        fired_at == [2, 4],
        f"the rule declared when.nth=[2, 4] and fired on matches {fired_at}. The occurrence counter "
        f"is 1-based and counts MATCHES, not requests that fired (core/chaos/engine.py::evaluate).",
    )
    require(
        first_fires == second_fires == 2,
        f"the rule reports {first_fires} fires on unit A and {second_fires} on unit B, expected 2 "
        f"on each. The counters published at /__unit/chaos are what a consumer debugs a scenario "
        f"with; they must count exactly the fires that happened.",
    )
    return f"both units: {list(first)}; fired on matches {fired_at}; 2 fires recorded on each"


@check(
    id="C12",
    name="chaos: a one-shot trigger mutates neither configuration nor counters",
    asserts=(
        "An in-band trigger fires without changing any standing rule's matches or fires, records "
        "exactly one overlay event, and leaves the standing rule's own budget intact."
    ),
    requires=Requires(mutating_route=True, chaos=True, in_band_trigger=True),
)
def one_shot_chaos_does_not_leak(env: CheckEnv) -> str:
    trigger = env.in_band_trigger()
    route = env.first_mutating_route()
    _install_rule(
        env,
        {
            "id": _LEAK_RULE,
            "scope": "request",
            "fault": _PROBE_FAULT,
            "match": {"route": route.key},
            "when": {"nth": [2]},
        },
    )
    before_document = env.chaos()
    before = _standing_state(before_document)
    events_before = len(before_document["events"])

    fired = env.client.call(route.method, route.probe_path, **trigger.request(_PROBE_FAULT))
    require(
        fired.error_kind == _PROBE_ERROR,
        f"the in-band trigger ({trigger.describe}) did not inject anything: {route.key} answered "
        f"{fired.status} with x-unit-error={fired.error_kind!r}. This contract is vacuous unless "
        f"the trigger actually fires, so a trigger that does nothing is a failure here rather than "
        f"a quiet pass. Check the magic spec published at /__unit/info against "
        f"core/kernel/magic.py::extract_magic.",
    )

    after_document = env.chaos()
    after = _standing_state(after_document)
    require(
        after == before,
        f"a one-shot trigger changed standing chaos state.\n"
        f"  before: {before}\n"
        f"  after:  {after}\n"
        f"core/chaos/selector.py::FaultSelector.select_request must return the in-band decision "
        f"BEFORE entering the standing-rule loop and before touching any counter. Snapshot and "
        f"thread the decision; never set-then-unset, which leaves a window and a restore path that "
        f"an exception can skip.",
    )

    events = list(after_document["events"])
    require(
        len(events) == events_before + 1,
        f"the in-band fire produced {len(events) - events_before} new chaos events, expected "
        f"exactly 1. A one-shot is recorded once, as observability -- recording nothing makes a "
        f"magic-triggered failure invisible at /__unit/chaos, and recording more than once means "
        f"it went round the engine as well.",
    )
    require(
        events[-1]["rule_id"] == OVERLAY_RULE_ID,
        f"the in-band fire was recorded under rule_id={events[-1]['rule_id']!r}, expected "
        f"{OVERLAY_RULE_ID!r}. Attributing it to a standing rule would make the audit claim that "
        f"rule fired, which is exactly the confusion the separate id prevents.",
    )

    # The half the losing entry's own evidence did not cover: the configuration
    # was leak-proof and the counters were not.
    first = env.client.call(route.method, route.probe_path, json_body={})
    require(
        first.error_kind != _PROBE_ERROR,
        "the standing rule fired on its FIRST match after a one-shot ran. The one-shot advanced "
        "the rule's occurrence counter, so 'the second call fails' silently became 'the first' "
        "for every consumer who also uses the in-band trigger. The in-band path must never reach "
        "ChaosEngine.evaluate.",
    )
    second = env.client.call(route.method, route.probe_path, json_body={})
    require(
        second.error_kind == _PROBE_ERROR,
        f"the standing rule (when.nth=[2]) did not fire on its second match after a one-shot ran: "
        f"{route.key} answered {second.status} with x-unit-error={second.error_kind!r}. The "
        f"one-shot consumed part of the rule's budget.",
    )
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    return (
        f"{trigger.describe} fired; rules and counters byte-identical across it; one overlay event "
        f"recorded as {OVERLAY_RULE_ID!r}; the standing rule still fired on its own second match"
    )


@check(
    id="C14",
    name="chaos: an in-band trigger respects the capability gate",
    asserts=(
        "With the chaos capability off, an in-band trigger injects nothing and is recorded nowhere "
        "-- the gate runs before the request is even scanned."
    ),
    requires=Requires(mutating_route=True, in_band_trigger=True),
)
def in_band_trigger_respects_the_capability_gate(env: CheckEnv) -> str:
    gate = CoreCapability.CHAOS.value
    trigger = env.in_band_trigger()
    route = env.first_mutating_route(exclude_capability=gate)
    original = [row.name for row in env.capabilities() if row.enabled]
    toggled = gate in original
    try:
        if toggled:
            env.set_capabilities([name for name in original if name != gate and not name.startswith(f"{gate}.")])
        require(
            not env.capability_enabled(gate),
            f"capability {gate!r} is still reported enabled after being removed from the enabled "
            f"set. core/capability/registry.py::set_enabled replaces the set outright; a name that "
            f"survives it is being re-added somewhere.",
        )
        before = len(env.chaos()["events"])
        answered = env.client.call(route.method, route.probe_path, **trigger.request(_PROBE_FAULT))
        require(
            answered.error_kind != _PROBE_ERROR,
            f"with {gate!r} disabled, the in-band trigger ({trigger.describe}) still injected a "
            f"fault: {route.key} answered {answered.status}. A capability is only real when EVERY "
            f"entry point passes the same gate -- route the in-band path through "
            f"core/chaos/selector.py::FaultSelector.select_request, which asks the registry before "
            f"it parses anything, and pass the extraction as a callable so a shut gate never even "
            f"scans the body.",
        )
        after = len(env.chaos()["events"])
        require(
            after == before,
            f"with {gate!r} disabled, the trigger recorded {after - before} chaos event(s). A gated-"
            f"off trigger is not merely harmless, it never happened: nothing may be counted, "
            f"recorded or reported.",
        )
    finally:
        env.set_capabilities(original)
    how = "disabled for this check" if toggled else f"already off in profile {env.profile!r}"
    return f"with {gate!r} {how}, {trigger.describe} injected nothing and recorded nothing"


# ---------------------------------------------------------------------------
# C27 -- every matching rule counts, whether or not an earlier one fired.
# ---------------------------------------------------------------------------

_CLAIMING_RULE = "conformance-claims-every-request"
_COUNTING_RULE = "conformance-counts-underneath"


def _status(env: CheckEnv, rule_id: str) -> tuple[int, int]:
    for row in env.chaos()["rules"]:
        if row["id"] == rule_id:
            return int(row["matches"]), int(row["fires"])
    raise AssertionError(f"rule {rule_id!r} vanished from /__unit/chaos")


@check(
    id="C27",
    name="chaos: a rule below the one that fired still counts its match",
    asserts=(
        "With two rules matching one route, the upper firing on every request, the lower rule's "
        "matches advance on every request all the same and its fires stay at zero; after the upper "
        "rule is removed, a lower rule whose nth has already passed does not fire."
    ),
    requires=Requires(surface_route=True, chaos=True),
)
def every_matching_rule_counts_its_match(env: CheckEnv) -> str:
    """The engine's second invariant, read from the counters it publishes.

    ``core/chaos/engine.py`` names the ``break`` after a decision as "the
    single easiest line in this file to optimise into a bug", and until this
    check it was pinned by a unit test only: C08 installs one rule and C12
    one, so no contract had ever read the counters of a rule that did NOT
    fire. Breaking the loop left the matrix green (konyklabs/roadmap#10,
    N-3f; tracked as konyklabs/roadmap#15).

    Why it matters to a consumer is the second half. ``when.nth: [2]`` means
    "the second request this rule matched", not "the second request no
    earlier rule claimed". If the lower rule stopped counting while an upper
    rule was firing, removing the upper rule would make the lower one fire on
    what it counts as its second match -- a scenario that passed yesterday
    fails today for reasons nothing reports. So after the counters are read,
    the upper rule is deleted and two more requests go through: on a correct
    engine the lower rule's second match is long past and neither fires.
    """
    route = env.first_vendor_route()
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    for rule in (
        {"id": _CLAIMING_RULE, "scope": "request", "fault": _PROBE_FAULT, "match": {"route": route.key}},
        {
            "id": _COUNTING_RULE,
            "scope": "request",
            "fault": _PROBE_FAULT,
            "match": {"route": route.key},
            "when": {"nth": [2]},
        },
    ):
        answered = env.client.call("POST", f"{CONTROL_PREFIX}chaos/rules", json_body=rule)
        require(
            answered.status == 200,
            f"POST /__unit/chaos/rules refused {rule['id']!r} with {answered.status}: {answered.text}.",
        )

    claimed = [env.client.call(route.method, route.probe_path, json_body={}).error_kind for _ in range(2)]
    require(
        claimed == [_PROBE_ERROR, _PROBE_ERROR],
        f"the upper rule (no `when`, so every match fires) answered {claimed} over two requests to "
        f"{route.key}, expected two {_PROBE_ERROR!r}. This contract needs a rule that claims every "
        f"request in order to ask what the rule beneath it counts.",
    )
    upper_matches, upper_fires = _status(env, _CLAIMING_RULE)
    lower_matches, lower_fires = _status(env, _COUNTING_RULE)
    require(
        (upper_matches, upper_fires) == (2, 2),
        f"the upper rule reports matches={upper_matches}, fires={upper_fires} after two requests, expected 2 and 2.",
    )
    require(
        lower_matches == 2,
        f"the lower rule reports matches={lower_matches} after two requests the upper rule claimed, "
        f"expected 2. core/chaos/engine.py::evaluate must advance EVERY matching rule's counter whether "
        f"or not an earlier rule already fired -- the loop does not break on a decision. With a break, "
        f"`when.nth: [2]` on a lower rule means 'the second request no earlier rule claimed', and adding "
        f"a rule above another silently re-numbers every rule below it.",
    )
    require(
        lower_fires == 0,
        f"the lower rule reports fires={lower_fires} while the upper rule claimed both requests; at "
        f"most one fault is armed per request, so a rule that lost the decision must not count a fire.",
    )

    removed = env.client.call("DELETE", f"{CONTROL_PREFIX}chaos/rules/{_CLAIMING_RULE}")
    require(removed.status == 200, f"DELETE /__unit/chaos/rules/{_CLAIMING_RULE} answered {removed.status}.")
    later = [env.client.call(route.method, route.probe_path, json_body={}).error_kind for _ in range(2)]
    require(
        _PROBE_ERROR not in later,
        f"after the upper rule was removed, the lower rule (when.nth=[2]) fired on a later request "
        f"({later}). Its second match happened while the upper rule was still claiming requests and "
        f"is past; firing now means it was not counting then, which is the same defect seen from the "
        f"consumer's side -- a scenario re-numbered by a rule that was above it.",
    )
    lower_matches_after, lower_fires_after = _status(env, _COUNTING_RULE)
    require(
        (lower_matches_after, lower_fires_after) == (4, 0),
        f"the lower rule reports matches={lower_matches_after}, fires={lower_fires_after} after four "
        f"requests in total, expected 4 and 0.",
    )
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    return (
        f"{route.key}: two requests claimed by the upper rule (matches 2, fires 2); the lower rule "
        f"counted both (matches 2, fires 0); with the upper rule removed, two more requests answered "
        f"{later} and the lower rule stands at matches 4, fires 0"
    )
