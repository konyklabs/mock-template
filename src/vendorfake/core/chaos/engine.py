"""The chaos engine: which standing rule fires, and why it fired.

FOR: turning "sometimes the third create fails" into a fact a test can assert.

INVARIANT (design choice, justified in the README and ported verbatim from
``packages/core/src/chaos/engine.ts``): **triggering is DETERMINISTIC by
default.** A rule fires on a counter -- ``nth``, ``every``, ``after``,
``times`` -- never on a coin flip, so "the third create fails" is a fact rather
than a flake. Two escape hatches exist:

  - ``probability``, which does use the seeded RNG. The seed lives in the
    profile and is reported by ``/__unit/info``, so the run is still replayable.
  - magic values in ordinary request fields (``kernel/magic.py``), for
    consumers that drive the unit through a vendor SDK and cannot reach the
    control API.

SECOND INVARIANT, and the one that is easy to get wrong: **every matching
rule's counter advances, whether or not it fires.** ``when.nth: [2]`` means
"the second request this rule matched", not "the second request no earlier rule
claimed". Without it, adding a rule above another would silently re-number
every rule below it, and a scenario that passed yesterday would fail today for
reasons nothing reports. It is pinned by its own test.

THIRD INVARIANT: **this class is the only writer of the counters and of the
history, and it may be reached only from ``chaos/selector.py``.**
``tools/boundary_check.py``'s call-shape pass fails the build if any other core
module calls ``.evaluate`` on a chaos engine. The reason is not tidiness: the
losing bake-off entry shipped a second arming path -- a per-request header
merged over the global config with no capability check anywhere -- and one
choke point makes that unrepresentable rather than merely discouraged.

Three deliberate departures from the reference, each recorded rather than
absorbed:

*The engine takes a lock.* The reference relies on Node's single thread. This
core is synchronous and multi-threaded: the pipeline holds one lock for most
routes, but routes declaring ``serialized=False`` run concurrently, and the
webhook-scope evaluation happens on whichever thread committed the journal
entry. Two threads incrementing ``matches`` without a lock lose counts, and a
lost count is exactly the failure this engine exists to make impossible.
``provenance: judgment``.

*``record_overlay`` exists.* The reference records nothing when a magic value
fires, which leaves a consumer debugging a magic-driven run with no audit trail
-- against this engine's own stated purpose. An overlay fire is appended to the
history under the rule id ``magic``, and touches no counter. Callers of
``/__unit/chaos`` therefore see ``enabled``, ``seed`` and every rule's
``matches``/``fires`` unchanged, and exactly one new event.
``provenance: judgment``.

*Rules are parsed.* The reference stores whatever object the control plane
handed it. Documents go through ``chaos/rules.py`` here, so a misspelled
``when`` key is a 400 rather than an unconditional rule.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from vendorfake.core.chaos.rules import ChaosRule, ChaosScope, FaultName, glob_match, parse_rule
from vendorfake.core.rand.rng import Rng

__all__ = [
    "OVERLAY_RULE_ID",
    "ChaosDecision",
    "ChaosEngine",
    "ChaosEvent",
    "ChaosSubject",
    "RuleStatus",
]

OVERLAY_RULE_ID = "magic"
"""The rule id recorded for an in-band fire. Not a real rule: it has no
counters, cannot be listed, removed or replaced, and exists only so the history
can explain a run that a standing rule did not cause."""


@dataclass(frozen=True, slots=True)
class ChaosSubject:
    """What is being evaluated: one request, or one outbound event.

    ``headers`` keys are already lower-cased by the transport binding, which is
    why ``matches`` lower-cases only the *pattern* side.
    """

    scope: ChaosScope
    route_key: str | None = None
    method: str | None = None
    path: str | None = None
    capability: str | None = None
    event_type: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    body_text: str | None = None

    def label(self) -> str:
        """The one-line description recorded in the history.

        ``route_key``, then ``event_type``, then ``path``, then a literal
        placeholder. Written with ``is not None`` and not with truthiness: the
        reference uses ``??``, so an empty-string ``route_key`` is kept and does
        not fall through to ``event_type``. Porting ``??`` as ``or`` is the
        classic way to change a fallback chain by accident.
        """
        if self.route_key is not None:
            return self.route_key
        if self.event_type is not None:
            return self.event_type
        if self.path is not None:
            return self.path
        return "(unknown)"


@dataclass(frozen=True, slots=True)
class ChaosDecision:
    """One fault, armed for one subject."""

    rule_id: str
    fault: FaultName
    params: Mapping[str, Any]
    #: 1-based count of matches for this rule, including this one. Always 1 for
    #: an in-band decision, which matched nothing and counted nothing.
    occurrence: int

    def as_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "fault": self.fault,
            "params": dict(self.params),
            "occurrence": self.occurrence,
        }


@dataclass(frozen=True, slots=True)
class ChaosEvent:
    """A decision, plus when it happened and to what. Published at ``/__unit/chaos``."""

    rule_id: str
    fault: FaultName
    params: Mapping[str, Any]
    occurrence: int
    at: str
    subject: str

    @classmethod
    def of(cls, decision: ChaosDecision, *, at: str, subject: str) -> ChaosEvent:
        return cls(
            rule_id=decision.rule_id,
            fault=decision.fault,
            params=dict(decision.params),
            occurrence=decision.occurrence,
            at=at,
            subject=subject,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "fault": self.fault,
            "params": dict(self.params),
            "occurrence": self.occurrence,
            "at": self.at,
            "subject": self.subject,
        }


@dataclass(frozen=True, slots=True)
class RuleStatus:
    """A rule with its counters, as ``/__unit/chaos`` reports it."""

    rule: ChaosRule
    matches: int
    fires: int

    def as_json(self) -> dict[str, Any]:
        # ``exclude_none`` so an unset ``match``/``when``/``params``/``note`` is
        # absent from the document rather than present as null. The reference
        # spreads the rule object, and JavaScript has no key for an undefined
        # field; a null here would make two units that were configured
        # identically produce two different documents.
        body: dict[str, Any] = self.rule.model_dump(exclude_none=True)
        body["matches"] = self.matches
        body["fires"] = self.fires
        return body


@dataclass(slots=True)
class _RuleState:
    matches: int = 0
    fires: int = 0


class ChaosEngine:
    """Standing rules, their counters, and the history of what fired."""

    __slots__ = ("_enabled", "_history", "_lock", "_now_iso", "_rng", "_rules", "_state")

    def __init__(
        self,
        rng: Rng,
        now_iso: Callable[[], str],
        rules: Iterable[ChaosRule | Mapping[str, Any]] = (),
    ) -> None:
        self._rng = rng
        self._now_iso = now_iso
        self._lock = threading.RLock()
        self._rules: list[ChaosRule] = []
        self._state: dict[str, _RuleState] = {}
        self._history: list[ChaosEvent] = []
        self._enabled = True
        self.replace(rules)

    # -- the runtime toggle -------------------------------------------------

    def set_enabled(self, on: bool) -> None:
        """Silence or resume the standing rules.

        This is *not* the capability. It silences rules; the ``chaos``
        capability silences fault injection as a whole, in-band triggers
        included. Two switches because they answer two different questions:
        "stop the scenario I configured" and "this deployment does not inject
        faults at all".
        """
        with self._lock:
            self._enabled = on

    @property
    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    # -- the rule set -------------------------------------------------------

    def list(self) -> tuple[ChaosRule, ...]:
        """The rules, deep-copied. A caller cannot reach the engine's own."""
        with self._lock:
            return tuple(rule.model_copy(deep=True) for rule in self._rules)

    def status(self) -> tuple[RuleStatus, ...]:
        """Each rule with its counters, in insertion order."""
        with self._lock:
            return tuple(
                RuleStatus(
                    rule=rule.model_copy(deep=True),
                    matches=self._state[rule.id].matches,
                    fires=self._state[rule.id].fires,
                )
                for rule in self._rules
            )

    def replace(self, rules: Iterable[ChaosRule | Mapping[str, Any]]) -> None:
        """Swap the whole set, resetting every counter.

        Counters reset because the rules they counted are gone; keeping a
        counter across a replace would make ``nth: [2]`` mean "the second match
        since some earlier rule with the same id", which nothing could reason
        about.
        """
        parsed = [rule if isinstance(rule, ChaosRule) else parse_rule(rule) for rule in rules]
        with self._lock:
            self._rules = [rule.model_copy(deep=True) for rule in parsed]
            self._state = {rule.id: _RuleState() for rule in self._rules}

    def add(self, rule: ChaosRule | Mapping[str, Any]) -> ChaosRule:
        """Add one rule, replacing any rule with the same id.

        A re-added id goes to the *end* of the list and starts from zero, which
        is the reference's behaviour: ``filter`` then ``push``. Insertion order
        is the tie-break for which rule claims a subject, so a re-add is a
        deliberate demotion rather than an in-place edit.
        """
        parsed = rule if isinstance(rule, ChaosRule) else parse_rule(rule)
        with self._lock:
            self._rules = [existing for existing in self._rules if existing.id != parsed.id]
            self._rules.append(parsed.model_copy(deep=True))
            self._state[parsed.id] = _RuleState()
        return parsed.model_copy(deep=True)

    def remove(self, rule_id: str) -> bool:
        """Remove one rule. ``False`` when there was nothing to remove."""
        with self._lock:
            before = len(self._rules)
            self._rules = [rule for rule in self._rules if rule.id != rule_id]
            self._state.pop(rule_id, None)
            return len(self._rules) != before

    def reset(self) -> None:
        """Clear rules, counters and history. Restores a pristine unit."""
        with self._lock:
            self._rules = []
            self._state.clear()
            self._history = []
            self._rng.reset()
            self._enabled = True

    def reset_counters(self) -> None:
        """Reset only the counters, keeping the rules -- for repeating a scenario.

        The RNG is reset too, which is what makes "the same rules and the same
        traffic give the same outcomes twice" true for a rule using
        ``probability``. Without it the second run would draw from wherever the
        first one stopped.
        """
        with self._lock:
            for state in self._state.values():
                state.matches = 0
                state.fires = 0
            self._history = []
            self._rng.reset()

    # -- the history --------------------------------------------------------

    def events(self) -> tuple[ChaosEvent, ...]:
        """Everything that fired, oldest first. Copies, not the records."""
        with self._lock:
            return tuple(
                ChaosEvent.of(
                    ChaosDecision(
                        rule_id=event.rule_id,
                        fault=event.fault,
                        params=dict(event.params),
                        occurrence=event.occurrence,
                    ),
                    at=event.at,
                    subject=event.subject,
                )
                for event in self._history
            )

    def record_overlay(self, decision: ChaosDecision, subject: ChaosSubject) -> ChaosEvent:
        """Record an in-band fire. Touches the history and nothing else.

        Called only by ``chaos/selector.py``, and only after the ``chaos``
        capability gate has passed. It deliberately does not go near
        ``_state``: an in-band trigger is a per-request instruction, and a
        standing rule's budget must survive it untouched -- that is the whole
        of the one-shot leak-proofing, and it is asserted twice, here by test
        and by the conformance check that reads ``/__unit/chaos`` before and
        after.
        """
        event = ChaosEvent.of(decision, at=self._now_iso(), subject=subject.label())
        with self._lock:
            self._history.append(event)
        return event

    # -- evaluation ---------------------------------------------------------

    def evaluate(self, subject: ChaosSubject) -> ChaosDecision | None:
        """At most one fault per subject: the first eligible rule in insertion order.

        Every matching rule's counter advances, whether or not it fires, so
        ``when.nth: [2]`` means "the second request this rule matched" rather
        than "the second request no earlier rule claimed". Without that, adding
        a rule would silently re-number every rule below it.

        Note that the loop does *not* break once a decision is taken: later
        rules still count their matches. That is the invariant above, and it is
        the single easiest line in this file to "optimise" into a bug.
        """
        with self._lock:
            if not self._enabled:
                return None
            decision: ChaosDecision | None = None
            for rule in self._rules:
                if rule.scope != subject.scope:
                    continue
                if not self._matches(rule, subject):
                    continue
                state = self._state.setdefault(rule.id, _RuleState())
                state.matches += 1
                if decision is None and self._should_fire(rule, state):
                    state.fires += 1
                    decision = ChaosDecision(
                        rule_id=rule.id,
                        fault=rule.fault,
                        params=dict(rule.params or {}),
                        occurrence=state.matches,
                    )
            if decision is not None:
                self._history.append(ChaosEvent.of(decision, at=self._now_iso(), subject=subject.label()))
            return decision

    def _matches(self, rule: ChaosRule, subject: ChaosSubject) -> bool:
        """Ported from ``engine.ts:matches``. Conditions ANDed; absent is not a veto."""
        criteria = rule.match
        if criteria is None:
            return True
        if criteria.route and not (subject.route_key is not None and glob_match(criteria.route, subject.route_key)):
            return False
        if criteria.path and not (subject.path is not None and glob_match(criteria.path, subject.path)):
            return False
        if criteria.method and criteria.method.upper() != (subject.method or "").upper():
            return False
        if criteria.capability and criteria.capability != subject.capability:
            return False
        if criteria.event_type and not (
            subject.event_type is not None and glob_match(criteria.event_type, subject.event_type)
        ):
            return False
        if criteria.body_contains and criteria.body_contains not in (subject.body_text or ""):
            return False
        if criteria.header:
            for name, value in criteria.header.items():
                if subject.headers.get(name.lower()) != value:
                    return False
        return True

    def _should_fire(self, rule: ChaosRule, state: _RuleState) -> bool:
        """Ported from ``engine.ts:shouldFire``, condition order included.

        Conditions are ANDed, and an absent condition is not a veto: a rule with
        no ``when`` fires on every match. ``times`` is checked first so an
        exhausted rule costs nothing, and ``probability`` last so it draws from
        the RNG only for a match that has already satisfied every deterministic
        condition -- otherwise the seeded stream would depend on traffic the
        rule was never going to fire on.

        The order is contract, not style. Move ``probability`` above ``nth`` and
        two runs of the same scenario stop producing the same outcomes, which is
        the property this whole subsystem exists to provide.

        ``if w.nth`` and not ``if w.nth is not None``: an empty list is a
        vetoless condition in the reference, because ``[]`` is falsy in
        JavaScript, and an empty tuple is falsy here for the same effect.
        """
        conditions = rule.when
        if conditions is None:
            return True
        if conditions.times is not None and state.fires >= conditions.times:
            return False
        if conditions.nth and state.matches not in conditions.nth:
            return False
        if conditions.after is not None and state.matches <= conditions.after:
            return False
        if conditions.every is not None and state.matches % conditions.every != 0:
            return False

        # The five conditions are a ported sequence and their order is contract.
        # Inlining this last one into the return (SIM103) would make it read as
        # the answer rather than as the fifth veto, and the next person adding a
        # sixth would have to re-derive the shape.
        if conditions.probability is not None and self._rng.next() >= conditions.probability:  # noqa: SIM103
            return False
        return True
