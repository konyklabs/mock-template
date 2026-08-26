"""The one place a fault is armed.

FOR: making "a fault can only be armed here, and only after a capability
check" a mechanical fact. ``tools/boundary_check.py``'s call-shape pass fails
the build if any core module outside this file calls ``.evaluate`` on a chaos
engine, and ``core/capability/gates.py`` names the two methods below as the
call sites of two of the core's three gates.

INVARIANT: **every arming route passes through here, and the capability gate
runs before anything is parsed.** There are exactly two routes, and they are
gated by two *different* capabilities, which is why this file has two entry
points rather than one:

``select_request`` -- gate ``chaos``
    Request-scope faults from every source: standing rules AND per-request
    magic values.

``select_webhook`` -- gate ``webhooks.chaos``
    Delivery-scope faults only.

Collapsing them into one gate would change *which* capability disables
delivery faults. A profile that wants request faults but honest delivery -- or
the reverse -- is a real configuration, and one gate cannot express it.

WHY THIS FILE EXISTS AT ALL. The losing bake-off entry had a second arming
path: ``dispatch()`` read a per-request ``x-chaos`` header, and
``ChaosEngine.effectiveConfig(override)`` merged it over the global config
unconditionally. The merge was correct about one-shot semantics -- it never
mutated global state, which is the right instinct -- and wrong about the thing
that mattered: no capability was consulted anywhere on that path, so a unit
with fault injection switched off still injected faults for any caller who
knew the header name. This module reproduces the correct half and closes the
hole, by checking the gate *first* and only then evaluating the in-band
trigger. The in-band trigger is passed as a callable for exactly that reason:
with the gate shut, the request body is never even scanned.

ONE-SHOT LEAK-PROOFING, stated precisely. When an in-band trigger fires,
``select_request`` returns **before** the standing-rule loop is entered and
before any counter is touched. No standing rule's ``matches`` advances, no
rule's ``fires`` advances, and no rule's budget is consumed -- so a rule
configured to fire on its second match still fires on its second match, and
the request carrying the magic value did not count as the first. The leak is
unrepresentable here rather than tested for: the only counter writer is
``ChaosEngine.evaluate``, and this path does not call it.

The one thing an in-band fire *does* touch is the history. It is appended
under the rule id ``magic`` so that a magic-driven run can still be explained
-- which is the engine's stated purpose, and which the reference gave up by
recording nothing. ``/__unit/chaos`` is therefore identical across ``enabled``,
``seed`` and every rule's counters, and carries exactly one new event.

The engine's own ``enabled`` toggle does NOT veto an in-band trigger, matching
the reference, whose pipeline bypasses ``evaluate`` entirely when a magic value
is present. The two switches answer different questions: ``enabled: false``
means "stop the scenario I configured", and a consumer who then writes
``chaos:rate_limit`` into a reference id has explicitly asked for this one. The
total off switch is the ``chaos`` capability, which is what the conformance
check for a disabled unit uses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from vendorfake.core.capability.gates import CoreCapability
from vendorfake.core.capability.registry import CapabilityRegistry
from vendorfake.core.chaos.engine import OVERLAY_RULE_ID, ChaosDecision, ChaosEngine, ChaosSubject
from vendorfake.core.kernel.magic import MagicExtraction

__all__ = ["FaultSelection", "FaultSelector"]

FaultSource = Literal["none", "in_band", "rule"]


@dataclass(frozen=True, slots=True)
class FaultSelection:
    """What the selector decided, and where it came from.

    ``in_band_faults`` and ``in_band_params`` are published here rather than
    written into a per-request scratch object, and they are EMPTY whenever the
    ``chaos`` capability is off -- a disabled unit hands a vendor nothing to
    have to remember to ignore.
    """

    decision: ChaosDecision | None = None
    source: FaultSource = "none"
    in_band_faults: tuple[str, ...] = ()
    in_band_params: Mapping[str, str] = field(default_factory=dict)


_NOTHING = FaultSelection()


class FaultSelector:
    """The choke point. Holds the engine and the registry; owns neither."""

    __slots__ = ("_capabilities", "_engine")

    def __init__(self, engine: ChaosEngine, capabilities: CapabilityRegistry) -> None:
        self._engine = engine
        self._capabilities = capabilities

    def select_request(
        self,
        subject: ChaosSubject,
        in_band: Callable[[], MagicExtraction] | None = None,
    ) -> FaultSelection:
        """Arm at most one request-scope fault. Gate: ``chaos``.

        ``in_band`` is a callable, not a value, so that the capability check
        genuinely precedes the parse rather than merely preceding the use of
        its result. Order in this method is contract, and each step is here for
        a reason a test pins:

        1. Gate. Off means nothing is scanned, nothing is counted, nothing is
           recorded, and the caller is told nothing was armed.
        2. In-band trigger. If it names a fault, it wins -- an explicit
           per-request instruction beats a standing rule rather than competing
           with it -- and the method returns without touching a counter.
        3. Standing rules, via the engine's single ``evaluate``.

        The gate is silent (``is_enabled``) rather than raising: a request to a
        unit with fault injection off is an ordinary request with an ordinary
        response, not a 501.
        """
        if subject.scope != "request":
            raise ValueError(f"select_request needs a request-scope subject, got {subject.scope!r}")
        if not self._capabilities.is_enabled(CoreCapability.CHAOS.value):
            return _NOTHING

        if in_band is not None:
            extraction = in_band()
            if extraction.armed:
                decision = ChaosDecision(
                    rule_id=OVERLAY_RULE_ID,
                    fault=extraction.faults[0],
                    params=dict(extraction.params),
                    occurrence=1,
                )
                self._engine.record_overlay(decision, subject)
                return FaultSelection(
                    decision=decision,
                    source="in_band",
                    in_band_faults=extraction.faults,
                    in_band_params=dict(extraction.params),
                )

        standing = self._engine.evaluate(subject)
        if standing is None:
            return _NOTHING
        return FaultSelection(decision=standing, source="rule")

    def select_webhook(self, subject: ChaosSubject) -> ChaosDecision | None:
        """Arm at most one delivery-scope fault. Gate: ``webhooks.chaos``.

        No in-band path: an outbound event carries no consumer-supplied field
        to hide a magic value in. Returns the decision directly rather than a
        :class:`FaultSelection`, because there is no second thing to report and
        a struct with three permanently-empty fields would invite someone to
        fill them.
        """
        if subject.scope != "webhook":
            raise ValueError(f"select_webhook needs a webhook-scope subject, got {subject.scope!r}")
        if not self._capabilities.is_enabled(CoreCapability.WEBHOOKS_CHAOS.value):
            return None
        return self._engine.evaluate(subject)
