"""The retry *shape*, with no schedule in it. **The core's schedule is empty and stays
empty**, a schedule being one vendor's documented property: the vendor supplies its own
through ``VendorDefinition.retry_defaults``, and ``Unit`` refuses to start when the merge
left it empty. Two types, because the Pydantic policy that parses a profile is not the
one ``POST /__unit/webhooks/retry-policy`` patches at runtime.
``time_scale`` scales every interval, rounding through :func:`js_round`. **The live policy
is patched by replacement**: frozen, so an attempt that reads it once holds a schedule
that cannot shorten under it mid-flight.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from vendorfake.core.util.numbers import as_float, as_int, js_round

__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "MutableRetryPolicy",
    "RetryPolicy",
    "retry_delay_ms",
    "schedule_exhausted",
]

DEFAULT_TIMEOUT_MS = 10_000


class RetryPolicy(Protocol):
    @property
    def schedule_ms(self) -> Sequence[int]:
        """Delay before attempt *n+1*, before scaling. Empty in the core."""
        ...

    @property
    def time_scale(self) -> float: ...

    @property
    def timeout_ms(self) -> int: ...


@dataclass(frozen=True, slots=True)
class MutableRetryPolicy:
    """The runtime policy: a patch changes it by producing a new one to swap in."""

    schedule_ms: tuple[int, ...] = ()
    time_scale: float = 1.0
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @classmethod
    def of(cls, policy: RetryPolicy) -> MutableRetryPolicy:
        """Copy a parsed policy into a runtime one, so a patch does not rewrite what the unit
        started with."""
        return cls(
            schedule_ms=tuple(policy.schedule_ms),
            time_scale=float(policy.time_scale),
            timeout_ms=int(policy.timeout_ms),
        )

    def patched(self, patch: Mapping[str, Any]) -> MutableRetryPolicy:
        """A new policy with the patch laid over this one; the three known keys are coerced,
        others ignored."""
        schedule = self.schedule_ms
        raw = patch.get("schedule_ms")
        if raw is not None and isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
            schedule = tuple(as_int(item, 0) for item in raw)
        return MutableRetryPolicy(
            schedule_ms=schedule,
            time_scale=as_float(patch["time_scale"], self.time_scale) if "time_scale" in patch else self.time_scale,
            timeout_ms=as_int(patch["timeout_ms"], self.timeout_ms) if "timeout_ms" in patch else self.timeout_ms,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "schedule_ms": list(self.schedule_ms),
            "time_scale": self.time_scale,
            "timeout_ms": self.timeout_ms,
        }


def schedule_exhausted(policy: RetryPolicy, retry_number: int) -> bool:
    """Has attempt ``retry_number`` used up the schedule? It is 0 for the first send."""
    return retry_number >= len(policy.schedule_ms)


def retry_delay_ms(policy: RetryPolicy, retry_number: int) -> int:
    """Scaled delay before the retry following ``retry_number``; ``IndexError`` past the end."""
    return js_round(policy.schedule_ms[retry_number] * policy.time_scale)
