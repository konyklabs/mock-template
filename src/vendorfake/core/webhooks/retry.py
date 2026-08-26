"""The retry *shape*, with no schedule in it.

FOR: letting the core say "retry on the policy" without the core knowing what
any policy is. A retry schedule is a documented property of one vendor's
webhook system -- eleven intervals over twenty-four hours for one, three over
five minutes for another -- and the moment the core ships a default, the
default is one vendor's and the core has a vendor in it.

INVARIANT: **the core's schedule is empty and stays empty.** The reference puts
its vendor's eleven intervals in ``packages/core/src/webhooks/dispatcher.ts``
and imports them from ``packages/core/src/config/profile.ts`` as
``DEFAULT_RETRY``, so a core built from that source cannot be compiled without
that vendor's documentation baked into it. Here, ``schedule_ms`` defaults to
``()``, the vendor supplies its own through ``VendorDefinition.retry_defaults``,
and ``Unit`` refuses to start when a vendor declares the ``webhooks``
capability and the merge left the schedule empty -- because an unmerged default
would present as "every delivery exhausts on its first attempt", which reads
like an unreachable subscriber rather than like a configuration mistake.

WHY THERE ARE TWO TYPES HERE AND NOT ONE. ``core/config/models.py`` already
carries a ``RetryPolicy``: a frozen Pydantic model, because that is what parses
a profile document. The dispatcher cannot hold that one, because the control
plane's ``POST /__unit/webhooks/retry-policy`` patches the live policy at
runtime -- the reference does it with ``Object.assign(this.opts.retry, patch)``
-- and a frozen model has nothing to assign to. So :class:`RetryPolicy` here is
the *protocol*, the read view both types satisfy and the only thing the
dispatcher's signature mentions, and :class:`MutableRetryPolicy` is the plain
dataclass the dispatcher actually holds. Pydantic is forbidden in this module
by ``tools/boundary.toml`` and that is the right way round: this module is on
the delivery path, not the parsing path.

THE SCALE IS NOT A CONVENIENCE. ``time_scale`` multiplies every interval, and
it exists so a test can observe the *shape* of a twenty-four-hour schedule in
milliseconds without changing the schedule itself. Rounding therefore goes
through :func:`js_round` and not Python's :func:`round`: ``round(2.5)`` is 2
and ``Math.round(2.5)`` is 3, and a user-supplied scale lands on a halfway case
the first time someone picks a round number.
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
"""How long to wait for a subscriber before calling the attempt a timeout.

Ten seconds is the reference vendor's documented webhook timeout, and it is the
one number in this module that is a default rather than an empty value. It is
defensible as a neutral default in a way a schedule is not: every vendor has
*some* timeout, and a core that defaulted it to zero would report every
delivery as timed out, while a core that had no default at all would make
``timeout_ms`` a required field on a profile section most profiles omit.
"""


class RetryPolicy(Protocol):
    """The read view of a retry policy. Three numbers and nothing else."""

    @property
    def schedule_ms(self) -> Sequence[int]:
        """Delay before attempt *n+1*, before scaling. Empty in the core."""
        ...

    @property
    def time_scale(self) -> float:
        """Multiplier applied to every interval. ``1.0`` means no compression."""
        ...

    @property
    def timeout_ms(self) -> int:
        """Milliseconds to wait for a subscriber before calling it a timeout."""
        ...


@dataclass(slots=True)
class MutableRetryPolicy:
    """The live policy a dispatcher holds and the control plane patches."""

    schedule_ms: tuple[int, ...] = ()
    time_scale: float = 1.0
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @classmethod
    def of(cls, policy: RetryPolicy) -> MutableRetryPolicy:
        """Copy a parsed policy into a mutable one.

        A copy and not a view: the resolved configuration is what the unit was
        *started* with and is reported as such at ``/__unit/info``, so a runtime
        patch must not rewrite it retroactively.
        """
        return cls(
            schedule_ms=tuple(policy.schedule_ms),
            time_scale=float(policy.time_scale),
            timeout_ms=int(policy.timeout_ms),
        )

    def apply(self, patch: Mapping[str, Any]) -> MutableRetryPolicy:
        """Patch in place and return self, so a caller can report the result.

        Only the three known keys are read, and each is coerced rather than
        indexed. The patch arrives as a parsed JSON body from the control
        plane, where ``{"time_scale": "0.5"}`` is a request a consumer is
        entitled to send and ``"0.5" * 60000`` is a 60000-character string
        rather than an error.

        An unknown key is ignored here rather than rejected: rejecting it is
        the control-plane schema's job, and doing it in both places means doing
        it differently in one of them eventually.
        """
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
        """The published shape, snake_case, all three keys always present."""
        return {
            "schedule_ms": list(self.schedule_ms),
            "time_scale": self.time_scale,
            "timeout_ms": self.timeout_ms,
        }


def schedule_exhausted(policy: RetryPolicy, retry_number: int) -> bool:
    """Has the attempt numbered ``retry_number`` used up the schedule?

    ``retry_number`` is 0 for the first send, so a schedule of eleven intervals
    permits twelve attempts: the initial one plus eleven retries. Ported from
    ``dispatcher.ts:318`` (``q.retryNumber >= schedule.length``) rather than
    re-derived, because the off-by-one here is the difference between a test
    asserting twelve records and one asserting eleven.
    """
    return retry_number >= len(policy.schedule_ms)


def retry_delay_ms(policy: RetryPolicy, retry_number: int) -> int:
    """Scaled delay before the retry that follows attempt ``retry_number``.

    ``js_round`` and not ``round``: see the module docstring. Raises
    :class:`IndexError` when the schedule is exhausted, which is a programming
    error -- :func:`schedule_exhausted` is the question to ask first.
    """
    return js_round(policy.schedule_ms[retry_number] * policy.time_scale)
