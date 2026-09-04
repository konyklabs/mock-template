"""The one place a fault is armed, gated before anything is parsed.
``select_request`` (gate ``chaos``) covers standing rules and per-request
magic values; ``select_webhook`` (gate ``webhooks.chaos``) covers
delivery-scope faults only. An in-band trigger wins over a standing rule,
returns before the rule loop runs and touches no counter, but is still
recorded in the history under rule id ``magic``.
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
    """What the selector decided; ``in_band_*`` fields are empty when ``chaos`` is off."""

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
        """Arm at most one request-scope fault. Gate: ``chaos``. Order: gate,
        then ``in_band`` (wins, no counter touched), then standing rules."""
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
        """Arm at most one delivery-scope fault. Gate: ``webhooks.chaos``. No in-band path."""
        if subject.scope != "webhook":
            raise ValueError(f"select_webhook needs a webhook-scope subject, got {subject.scope!r}")
        if not self._capabilities.is_enabled(CoreCapability.WEBHOOKS_CHAOS.value):
            return None
        return self._engine.evaluate(subject)
