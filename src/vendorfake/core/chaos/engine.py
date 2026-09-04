"""The chaos engine: which standing rule fires, and why.
INVARIANTS: triggering is deterministic by default (``probability`` is the
one seeded exception); every matching rule's counter advances whether or not
it fires, so adding a rule never re-numbers the ones below it; and this class
is the only writer of the counters and history, reachable only from
``chaos/selector.py`` (enforced by ``tools/boundary_check.py``). The engine
takes a lock since routes may run concurrently (``provenance: judgment``),
and ``record_overlay`` appends a magic-driven fire under rule id ``magic``
without touching a counter (``provenance: judgment``).
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from vendorfake.core.chaos.rules import ChaosRule, ChaosScope, FaultName, glob_match, parse_rule
from vendorfake.core.rand.rng import Rng

__all__ = [
    "CHAOS_HISTORY_CAPACITY",
    "OVERLAY_RULE_ID",
    "ChaosDecision",
    "ChaosEngine",
    "ChaosEvent",
    "ChaosSubject",
    "RuleStatus",
]

OVERLAY_RULE_ID = "magic"
"""The rule id recorded for an in-band fire. Not a real rule: no counters, not listable."""

CHAOS_HISTORY_CAPACITY = 10_000
"""Fires kept in the history, oldest evicted; the per-rule counters stay exact and unbounded."""


@dataclass(frozen=True, slots=True)
class ChaosSubject:
    """What is being evaluated: one request, or one outbound event. ``headers``
    keys are already lower-cased by the transport binding."""

    scope: ChaosScope
    route_key: str | None = None
    method: str | None = None
    path: str | None = None
    capability: str | None = None
    event_type: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    body_text: str | None = None

    def label(self) -> str:
        """``route_key``, then ``event_type``, then ``path``, then a placeholder;
        checked with ``is not None`` so an empty-string ``route_key`` is kept."""
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
    #: 1-based match count for this rule; always 1 for an in-band decision.
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
        # exclude_none: an unset match/when/params/note is absent, not null.
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
        self._history: deque[ChaosEvent] = deque(maxlen=CHAOS_HISTORY_CAPACITY)
        self._enabled = True
        self.replace(rules)

    # -- the runtime toggle -------------------------------------------------

    def set_enabled(self, on: bool) -> None:
        """Silence or resume the standing rules. Not the ``chaos`` capability,
        which also silences in-band triggers."""
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
        """Swap the whole set, resetting every counter -- the rules they counted are gone."""
        parsed = [rule if isinstance(rule, ChaosRule) else parse_rule(rule) for rule in rules]
        with self._lock:
            self._rules = [rule.model_copy(deep=True) for rule in parsed]
            self._state = {rule.id: _RuleState() for rule in self._rules}

    def add(self, rule: ChaosRule | Mapping[str, Any]) -> ChaosRule:
        """Add one rule, replacing any rule with the same id. A re-added id goes
        to the end and starts from zero -- a demotion, not an in-place edit."""
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
            self._history = deque(maxlen=CHAOS_HISTORY_CAPACITY)
            self._rng.reset()
            self._enabled = True

    def reset_counters(self) -> None:
        """Reset only the counters, keeping the rules. The RNG resets too, so a
        ``probability`` rule repeats its outcome."""
        with self._lock:
            for state in self._state.values():
                state.matches = 0
                state.fires = 0
            self._history = deque(maxlen=CHAOS_HISTORY_CAPACITY)
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
        """Record an in-band fire. Touches the history and nothing else --
        a standing rule's counters must survive it untouched."""
        event = ChaosEvent.of(decision, at=self._now_iso(), subject=subject.label())
        with self._lock:
            self._history.append(event)
        return event

    # -- evaluation ---------------------------------------------------------

    def evaluate(self, subject: ChaosSubject) -> ChaosDecision | None:
        """At most one fault per subject: the first eligible rule in insertion
        order. The loop does NOT break once a decision is taken -- later rules
        still count their matches."""
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
        """Conditions are ANDed; an absent one is not a veto."""
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
        """Conditions ANDed, absent not a veto. Order is contract: ``times``
        first so an exhausted rule costs nothing, ``probability`` last so the
        RNG is drawn only once every other condition already passed.
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

        # Order is contract; not inlined into the return.
        if conditions.probability is not None and self._rng.next() >= conditions.probability:  # noqa: SIM103
            return False
        return True
