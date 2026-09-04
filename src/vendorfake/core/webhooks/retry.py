"""The retry *shape*, with no schedule in it. **The core's schedule is empty and stays
empty**, a schedule being one vendor's documented property: the vendor supplies its own
through ``VendorDefinition.retry_defaults``, and ``Unit`` refuses to start when the merge
left it empty. Two types, because the frozen Pydantic policy that parses a profile cannot
be patched and ``POST /__unit/webhooks/retry-policy`` patches the live one at runtime.
``time_scale`` scales every interval, rounding through :func:`js_round`.
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


@dataclass(slots=True)
class MutableRetryPolicy:
    schedule_ms: tuple[int, ...] = ()
    time_scale: float = 1.0
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @classmethod
    def of(cls, policy: RetryPolicy) -> MutableRetryPolicy:
        """Copy a parsed policy into a mutable one, so a patch does not rewrite what the unit
        started with."""
        return cls(
            schedule_ms=tuple(policy.schedule_ms),
            time_scale=float(policy.time_scale),
            timeout_ms=int(policy.timeout_ms),
        )

    def apply(self, patch: Mapping[str, Any]) -> MutableRetryPolicy:
        """Patch in place and return self; the three known keys are coerced, others ignored."""
        if "schedule_ms" in patch:
            raw = patch["schedule_ms"]
            if isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
                self.schedule_ms = tuple(as_int(item, 0) for item in raw)
        if "time_scale" in patch:
            self.time_scale = as_float(patch["time_scale"], self.time_scale)
        if "timeout_ms" in patch:
            self.timeout_ms = as_int(patch["timeout_ms"], self.timeout_ms)
        return self

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
