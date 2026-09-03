"""What an armed request-scope fault actually does.

FOR: turning a :class:`ChaosDecision` -- which is only a name, a rule id and a
bag of untyped parameters -- into the ``UnitError`` the pipeline raises, in one
place, so that adding a fault is one branch and not a search.

INVARIANT: **which phase of the pipeline a fault fires in is a property of the
fault, not of the call site.** ``token_expiry`` means "the token expired while
the request was in flight", so it must fire *after* authentication has
succeeded -- firing it before would be indistinguishable from an ordinary
``unauthorized`` and would prove nothing about a consumer's refresh path.
Every other request-scope fault fires before authentication, because a rate
limit or an outage does not care who is calling. The pipeline therefore calls
this function twice with the same decision, once per phase, and this module
decides which call does something. Both calls are unconditional on the
pipeline's side, which is what stops "we only ran the post-auth phase for
authenticated routes" from becoming a second, divergent rule.

THE ``timeout`` FAULT AND THE CLOCK -- two reversals, recorded because neither
piece of reasoning is obvious.

FIRST REVERSAL: it does not park on a virtual timer. An earlier design routed
``timeout`` through :class:`Clock` unconditionally, on the grounds that a fake
should never really sleep. That is a deadlock. The pipeline holds one
re-entrant lock for the duration of a serialized request; in virtual-clock mode
the only thing that can fire a virtual timer is ``POST /__unit/clock/advance``,
which is itself a request. A request parked on a virtual timer would hold the
unit while the one call that could release it waited for the same lock -- on
the very profile the chaos demonstration runs on.

So the fault splits by clock mode, and neither half ever waits for another
request:

real mode
    The delay is *reported*, on :attr:`UnitError.delay_ms`, and the binding
    carries it out. See the second reversal below.

virtual mode
    :meth:`Clock.advance` on the calling thread, which returns as soon as the
    timers that came due have fired. Time moves by ``delay_ms`` and the request
    is answered immediately, with ``delay_ms=0`` on the response because the
    waiting has already happened -- in scenario time, which is the only clock a
    virtual-mode test is measuring. An elapsed-wall-time assertion is
    meaningless on this branch and is not made; what a virtual-mode test
    asserts instead is that the response is a ``timeout`` and that ``now()``
    moved.

SECOND REVERSAL: on a real clock this module no longer sleeps. It used to call
:func:`time.sleep` here, exactly as the reference does (``await
sleep(delayMs)``), and the reference's own assertion -- ``Date.now() - started
>= 20`` for ``delay_ms: 25`` -- was written against that branch. Two things
were wrong with it.

*It produced no timeout.* In process there is no socket, so a consumer's
``httpx.Client(timeout=...)`` was not consulted by anything: the ``timeout``
fault made the call slow and then answered 504, and the one thing a consumer
wants to rehearse -- their client raising :class:`httpx.ReadTimeout` and their
retry path running -- was unreachable without starting a real server.

*It made the kernel choose a thread to block.* The ASGI binding must not block
the event loop, the async in-process transport must yield to it, and a
file-drop binding wants an interruptible wait so shutdown does not have to
outlast the delay. One ``time.sleep`` in here forces every one of them to be
wrong in the same way.

So the kernel decides *whether* to delay and the binding decides *how*: the
delay travels out on :attr:`UnitError.delay_ms`, the pipeline copies it onto
:attr:`UnitResponse.delay_ms`, and each binding honours it in the terms of the
caller it is holding. The in-process transport turns a delay longer than the
client's read timeout into an immediate ``ReadTimeout``, which is why a
consumer's retry test now runs in a millisecond instead of five seconds.

PARAMETERS ARE COERCED, NEVER INDEXED. They arrive as strings on the in-band
path (``chaos:timeout:delay_ms=250`` is split textually) and as arbitrary JSON
on the rule path, so ``params["delay_ms"] / 1000`` is a ``TypeError`` waiting
for the first consumer who writes a fault into a request field.
:data:`FAULT_PARAM_KEYS` is the promise :data:`BUILTIN_FAULTS` makes about
which keys each fault reads, in one machine-readable place, so the catalogue
prose and the implementation cannot drift apart unnoticed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from vendorfake.core.chaos.engine import ChaosDecision
from vendorfake.core.kernel.types import Logger, UnitError, UnitErrorKind
from vendorfake.core.time.clock import Clock
from vendorfake.core.util.numbers import as_float, as_int, js_number

__all__ = [
    "AUTH_PHASE_FAULTS",
    "DEFAULT_RETRY_AFTER_SECONDS",
    "DEFAULT_TIMEOUT_DELAY_MS",
    "FAULT_PARAM_KEYS",
    "FaultPhase",
    "apply_request_fault",
]

FaultPhase = Literal["pre", "post_auth"]
"""The two moments the pipeline offers a request-scope fault."""

AUTH_PHASE_FAULTS: frozenset[str] = frozenset({"token_expiry"})
"""Faults that fire after authentication. Exactly one today; a set rather than
an equality test so a second one is a data change, not a rewritten condition."""

#: Reference defaults, ported: ``Number(d.params.delayMs ?? 100)`` and
#: ``Number(d.params.retryAfterSeconds ?? 1)``.
DEFAULT_TIMEOUT_DELAY_MS = 100.0
DEFAULT_RETRY_AFTER_SECONDS = 1

FAULT_PARAM_KEYS: Mapping[str, tuple[str, ...]] = {
    "rate_limit": ("retry_after_seconds",),
    "server_error": (),
    "unavailable": (),
    "timeout": ("delay_ms",),
    "token_expiry": (),
    "webhook.duplicate": ("copies",),
    "webhook.delay": ("delay_ms",),
    "webhook.out_of_order": (),
    "webhook.drop_ack": (),
    "webhook.drop": (),
}
"""Every parameter key a built-in fault reads, snake_case, keyed by fault name.

Published so the catalogue in ``chaos/rules.py`` and the implementations here
are checkable against each other rather than merely written next to each other.
The ``webhook.*`` rows are the delivery-scope faults, whose implementations
live with the dispatcher; their keys are declared here so the catalogue has one
owner."""


def _delay_owed(clock: Clock, delay_ms: float) -> int:
    """Account for ``delay_ms`` without ever blocking on another request.

    Returns what the binding still owes the caller in wall-clock milliseconds:
    zero on a virtual clock, where the waiting has already happened by moving
    scenario time; ``delay_ms`` on a real one, where only the binding knows
    whose clock to spend it on. Nothing here sleeps -- see the module
    docstring's second reversal.

    Rounded to a whole millisecond because :attr:`UnitResponse.delay_ms` is an
    ``int``, and rounded rather than truncated so a sub-millisecond delay does
    not silently become no delay at all.
    """
    if delay_ms <= 0:
        return 0
    if clock.mode == "virtual":
        clock.advance(delay_ms)
        return 0
    return max(0, round(delay_ms))


def apply_request_fault(
    decision: ChaosDecision,
    phase: FaultPhase,
    *,
    clock: Clock,
    log: Logger,
) -> None:
    """Raise the ``UnitError`` this decision stands for, if this is its phase.

    Returns normally -- doing nothing -- in three cases, all of them ordinary:
    the decision belongs to the other phase, the fault is a delivery-scope one
    that a request-scope rule named by mistake, or the fault name is one this
    core has never heard of. The last is a ``warn`` and not an error because
    the fault vocabulary is open by design: a fork adds a fault name without
    editing the core, and a unit that refused to start on an unrecognised one
    would make that impossible.
    """
    is_auth_fault = decision.fault in AUTH_PHASE_FAULTS
    if phase == "pre" and is_auth_fault:
        return
    if phase == "post_auth" and not is_auth_fault:
        return

    params = decision.params
    rule = decision.rule_id

    if decision.fault == "rate_limit":
        raise UnitError(
            UnitErrorKind.RATE_LIMITED,
            detail="Too many requests. Retry after a short delay.",
            info={
                "chaos_rule": rule,
                "retry_after_seconds": as_int(params.get("retry_after_seconds"), DEFAULT_RETRY_AFTER_SECONDS),
            },
        )
    if decision.fault == "server_error":
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail="Injected server error.",
            info={"chaos_rule": rule},
        )
    if decision.fault == "unavailable":
        raise UnitError(
            UnitErrorKind.UNAVAILABLE,
            detail="Injected service unavailability.",
            info={"chaos_rule": rule},
        )
    if decision.fault == "timeout":
        delay_ms = as_float(params.get("delay_ms"), DEFAULT_TIMEOUT_DELAY_MS)
        owed = _delay_owed(clock, delay_ms)
        shown = js_number(delay_ms)
        raise UnitError(
            UnitErrorKind.TIMEOUT,
            detail=f"Injected timeout after {shown}ms.",
            # ``info`` is the consumer-visible copy, published in the sidecar
            # and unchanged by this reversal: it still reports the delay the
            # rule asked for, on either clock. ``delay_ms=`` is the instruction
            # to the binding, and is zero in virtual mode because scenario time
            # has already moved.
            info={"chaos_rule": rule, "delay_ms": shown},
            delay_ms=owed,
        )
    if decision.fault == "token_expiry":
        raise UnitError(
            UnitErrorKind.TOKEN_EXPIRED,
            detail="The access token expired while the request was in flight.",
            info={"chaos_rule": rule},
        )

    log.warn("unknown request-scope fault ignored", {"fault": decision.fault, "rule": rule})
