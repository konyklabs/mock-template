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

RESPONSE-SCOPE FAULTS ARE A THIRD PHASE, NOT A THIRD CALL SITE HERE. The five
faults in :data:`RESPONSE_PHASE_FAULTS` -- ``malformed_body``,
``body_mutation``, ``connection_reset``, ``empty_response``, ``slow_body`` --
do not raise a ``UnitError`` at all: a fault that corrupts "the vendor
returned garbage" needs the vendor's *real* answer to corrupt, so it has to
run after the handler, not before or after auth. :func:`apply_response_fault`
is that third call site, in ``kernel/unit.py`` where the handler's result is
in scope; this module still owns the vocabulary, so ``apply_request_fault``
recognises the five names and does nothing for them at either phase, rather
than falling through to "unknown fault ignored" -- which is a real warning for
a fault this core has genuinely never heard of, and would be a false one here.
See ``core/kernel/types.py`` (:class:`~vendorfake.core.kernel.types.TransportDirective`)
and the README's "Transport faults" section.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from vendorfake.core.chaos.engine import ChaosDecision
from vendorfake.core.chaos.rules import BUILTIN_FAULTS
from vendorfake.core.kernel.shaping import header_text
from vendorfake.core.kernel.types import (
    Logger,
    TransportDirective,
    UnitError,
    UnitErrorKind,
    UnitResponse,
)
from vendorfake.core.time.clock import Clock
from vendorfake.core.util.json import dump_json
from vendorfake.core.util.numbers import as_float, as_int, as_str, js_number, js_parse_float

__all__ = [
    "AUTH_PHASE_FAULTS",
    "DEFAULT_RETRY_AFTER_SECONDS",
    "DEFAULT_TIMEOUT_DELAY_MS",
    "FAULT_DESCRIPTIONS",
    "FAULT_PARAM_KEYS",
    "RESPONSE_PHASE_FAULTS",
    "FaultPhase",
    "apply_request_fault",
    "apply_response_fault",
    "is_transport_fault",
]

FaultPhase = Literal["pre", "post_auth"]
"""The two moments the pipeline offers a request-scope fault. Response-scope
faults (:data:`RESPONSE_PHASE_FAULTS`) are a third moment with no phase
argument of their own -- see :func:`apply_response_fault`."""

AUTH_PHASE_FAULTS: frozenset[str] = frozenset({"token_expiry"})
"""Faults that fire after authentication. Exactly one today; a set rather than
an equality test so a second one is a data change, not a rewritten condition."""

RESPONSE_PHASE_FAULTS: frozenset[str] = frozenset(
    {"malformed_body", "body_mutation", "connection_reset", "empty_response", "slow_body"}
)
"""Faults applied to a successful handler response, after it returns -- see
:func:`apply_response_fault`. ``apply_request_fault`` checks membership here
first so it can skip these silently instead of warning "unknown fault"."""

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
    "malformed_body": ("mode", "status"),
    "body_mutation": ("ops",),
    "connection_reset": (),
    "empty_response": (),
    "slow_body": ("chunk_bytes", "chunk_delay_ms"),
}
"""Every parameter key a built-in fault reads, snake_case, keyed by fault name.

Published so the catalogue in ``chaos/rules.py`` and the implementations here
are checkable against each other rather than merely written next to each other.
The ``webhook.*`` rows are the delivery-scope faults, whose implementations
live with the dispatcher; their keys are declared here so the catalogue has one
owner."""

FAULT_DESCRIPTIONS: Mapping[str, str] = {spec.name: spec.summary for spec in BUILTIN_FAULTS}
"""One-line description per fault, keyed by name exactly as :data:`FAULT_PARAM_KEYS`
-- the same summaries :data:`~vendorfake.core.chaos.rules.BUILTIN_FAULTS` already
publishes, derived rather than retyped so ``vendorfake faults``, ``vendorfake info``
(which prints ``GET /__unit/info`` unchanged) and the catalogue cannot say two
different things about the same fault, and so a fault added to the catalogue
(the five transport-fidelity kinds, for one) appears in every listing at once."""


def _delay_owed(clock: Clock, delay_ms: float) -> int:
    """Account for ``delay_ms`` without ever blocking on another request.

    Returns what the binding still owes the caller in wall-clock milliseconds:
    zero on a virtual clock, where the waiting has already happened by moving
    scenario time; ``delay_ms`` on a real one, where only the binding knows
    whose clock to spend it on. Nothing here sleeps -- see the module
    docstring's second reversal.

    Rounded up to a whole millisecond because :attr:`UnitResponse.delay_ms` is
    an ``int``: ``math.ceil`` rather than ``round`` so a sub-millisecond delay
    does not silently become no delay at all -- ``round`` is banker's rounding
    to even and sends both ``0.4`` and ``0.5`` to zero, which would make the
    guarantee false for exactly the case it names.
    """
    if delay_ms <= 0:
        return 0
    if clock.mode == "virtual":
        clock.advance(delay_ms)
        return 0
    return max(0, math.ceil(delay_ms))


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
    if decision.fault in RESPONSE_PHASE_FAULTS:
        # A real fault this core knows well; it just does not fire here. See
        # the module docstring's "RESPONSE-SCOPE FAULTS" note and
        # ``kernel/unit.py``'s call to :func:`apply_response_fault`.
        return

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
            fault=decision.fault,
            rule_id=rule,
        )
    if decision.fault == "server_error":
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail="Injected server error.",
            info={"chaos_rule": rule},
            fault=decision.fault,
            rule_id=rule,
        )
    if decision.fault == "unavailable":
        raise UnitError(
            UnitErrorKind.UNAVAILABLE,
            detail="Injected service unavailability.",
            info={"chaos_rule": rule},
            fault=decision.fault,
            rule_id=rule,
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
            fault=decision.fault,
            rule_id=rule,
        )
    if decision.fault == "token_expiry":
        raise UnitError(
            UnitErrorKind.TOKEN_EXPIRED,
            detail="The access token expired while the request was in flight.",
            info={"chaos_rule": rule},
            fault=decision.fault,
            rule_id=rule,
        )

    log.warn("unknown request-scope fault ignored", {"fault": decision.fault, "rule": rule})


# ---------------------------------------------------------------------------
# Response-scope faults: transport-fidelity, provenance: transport.
#
# Everything below corrupts a *successful* handler response rather than
# refusing the request, which is why it is a function the pipeline calls after
# ``route.handler(args)`` returns rather than a branch in the function above.
# See the module docstring's "RESPONSE-SCOPE FAULTS" note.
# ---------------------------------------------------------------------------

_HTML_ERROR_PAGE = (
    b"<html><head><title>Bad Gateway</title></head>"
    b"<body><h1>Bad Gateway</h1><p>vendorfake: injected transport fault.</p></body></html>"
)
"""``malformed_body`` mode ``html``. A generic, vendor-neutral error page -- no
vendor documents *this* body, because no vendor sent it; a proxy or a load
balancer did. That is the point under test."""


def is_transport_fault(response: UnitResponse) -> bool:
    """Whether ``response`` was produced by a transport fault rather than by a
    vendor's own handler.

    FOR: stream #55's ``ValidatingClient``, which checks a response against the
    vendor's documented schema and must not fail a request this fake never
    claimed matches one -- a ``malformed_body`` or ``body_mutation`` response
    is supposed to violate the schema. Reading the ``vendorfake-fault`` header
    this module stamps is enough: nothing else in this distribution sets it,
    on any response, ever.
    """
    return "vendorfake-fault" in response.headers


def apply_response_fault(decision: ChaosDecision, response: UnitResponse, *, log: Logger) -> UnitResponse:
    """Corrupt a handler's real response for a response-scope fault, or hand
    it back unchanged.

    Called once per request, after the handler ran and before idempotency
    storage, with whatever :class:`ChaosDecision` fault selection armed for
    this request -- the same decision :func:`apply_request_fault` already saw
    twice and did nothing with, because a fault in :data:`RESPONSE_PHASE_FAULTS`
    is this function's alone.

    ``log`` is accepted for symmetry with :func:`apply_request_fault` and
    because a future fault kind may want it; none of today's five do.
    """
    fault = decision.fault
    if fault not in RESPONSE_PHASE_FAULTS:
        return response
    rule = decision.rule_id
    params = decision.params
    if fault == "malformed_body":
        return _malformed_body(response, params, fault=fault, rule=rule)
    if fault == "body_mutation":
        return _body_mutation(response, params, fault=fault, rule=rule)
    return _directive(response, fault, params, rule=rule)


def _stamp(headers: dict[str, str], fault: str, rule: str) -> None:
    """The two mechanism headers every faulted response carries -- old kinds
    too, via ``UnitError.fault``/``UnitError.rule_id`` and ``kernel/unit.py``'s
    ``_shape``. Lower-case keys, matching every other header this package sets
    (``x-unit-error``, ``retry-after``, ...); the prose name is
    ``Vendorfake-Fault`` / ``Vendorfake-Rule``, HTTP being case-insensitive
    about it either way.
    """
    headers["vendorfake-fault"] = fault
    headers["vendorfake-rule"] = header_text(rule)


def _malformed_body(response: UnitResponse, params: Mapping[str, Any], *, fault: str, rule: str) -> UnitResponse:
    """``mode: invalid_json | html | empty | truncate``.

    ``status`` is the fault's own parameter, not the real response's status:
    the real response is always a success (nothing here runs otherwise -- see
    the module docstring), and "the vendor answered 200 with garbage" is
    exactly as real a case as "the vendor answered 502 with an HTML page", so
    the fault states which one it is rather than inheriting one.
    """
    mode = params.get("mode")
    if mode not in ("invalid_json", "html", "empty", "truncate"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"malformed_body rule {rule!r}: params.mode must be one of invalid_json, html, empty, truncate; "
                f"got {mode!r}."
            ),
            field="params.mode",
        )
    default_status = 502 if mode == "html" else 200
    status = as_int(params.get("status"), default_status)
    headers = dict(response.headers)
    if mode == "html":
        body = _HTML_ERROR_PAGE
        headers["content-type"] = "text/html"
    elif mode == "empty":
        body = b""
        headers["content-type"] = "application/json"
    elif mode == "invalid_json":
        # The real body with its last byte dropped -- so the JSON never
        # closes -- and a stray comma appended in its place.
        body = response.body[:-1] + b","
    else:  # truncate
        body = response.body[: len(response.body) // 2]
    _stamp(headers, fault, rule)
    return UnitResponse(status=status, headers=headers, body=body, delay_ms=response.delay_ms)


def _body_mutation(response: UnitResponse, params: Mapping[str, Any], *, fault: str, rule: str) -> UnitResponse:
    """Apply every ``ops`` entry, in order, to the response's own JSON body.

    Firing this against a route whose response is not JSON is reported at fire
    time, not at rule-add time: the grammar in ``chaos/rules.py`` validates a
    rule with no route table and no vendor in reach (see its module
    docstring), and the route's *response shape* is knowable only once a
    response actually exists. Said plainly in the ``Open questions`` of the
    stream that added this.
    """
    raw_ops = params.get("ops")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: params.ops must be a non-empty list of pointer operations.",
            field="params.ops",
        )
    try:
        document = json.loads(response.body)
    except ValueError as exc:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"body_mutation rule {rule!r}: the matched route's response is not JSON, so no pointer "
                f"operation can apply ({exc})."
            ),
            field="params.ops",
        ) from exc
    for raw_op in raw_ops:
        document = _apply_pointer_op(document, raw_op, rule=rule)
    headers = dict(response.headers)
    _stamp(headers, fault, rule)
    return UnitResponse(status=response.status, headers=headers, body=dump_json(document), delay_ms=response.delay_ms)


def _directive(response: UnitResponse, fault: str, params: Mapping[str, Any], *, rule: str) -> UnitResponse:
    """``connection_reset`` / ``empty_response`` / ``slow_body``: leave the
    body alone and attach the instruction a binding interprets. See
    :class:`~vendorfake.core.kernel.types.TransportDirective`.
    """
    headers = dict(response.headers)
    _stamp(headers, fault, rule)
    directive: TransportDirective
    if fault == "connection_reset":
        directive = TransportDirective(kind="connection_reset")
    elif fault == "empty_response":
        directive = TransportDirective(kind="empty_response")
    else:
        chunk_bytes = max(1, as_int(params.get("chunk_bytes"), 64))
        chunk_delay_ms = max(0, as_int(params.get("chunk_delay_ms"), 100))
        directive = TransportDirective(kind="slow_body", chunk_bytes=chunk_bytes, chunk_delay_ms=chunk_delay_ms)
    return UnitResponse(
        status=response.status,
        headers=headers,
        body=response.body,
        delay_ms=response.delay_ms,
        transport=directive,
    )


# -- RFC 6901 JSON pointers, the small part of it this fault needs ----------


def _pointer_segments(pointer: str, *, rule: str) -> list[str]:
    if not pointer.startswith("/"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: pointer must be an RFC 6901 pointer starting with '/'; got {pointer!r}.",
            field="params.ops",
        )
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer.split("/")[1:]]


def _pointer_step(node: Any, segment: str, *, pointer: str, rule: str) -> Any:
    if isinstance(node, Mapping):
        if segment not in node:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"body_mutation rule {rule!r}: {pointer!r} does not exist in the response body.",
                field="params.ops",
            )
        return node[segment]
    if isinstance(node, list):
        return node[_pointer_index(node, segment, pointer=pointer, rule=rule)]
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"body_mutation rule {rule!r}: {pointer!r} walks into a scalar value.",
        field="params.ops",
    )


def _pointer_index(node: list[Any], segment: str, *, pointer: str, rule: str) -> int:
    if not segment.isdigit() or int(segment) >= len(node):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: {pointer!r} is not a valid index into a {len(node)}-element array.",
            field="params.ops",
        )
    return int(segment)


def _pointer_parent(document: Any, segments: Sequence[str], *, pointer: str, rule: str) -> tuple[Any, str | int]:
    if not segments:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: pointer {pointer!r} must not name the whole document.",
            field="params.ops",
        )
    node = document
    for segment in segments[:-1]:
        node = _pointer_step(node, segment, pointer=pointer, rule=rule)
    last = segments[-1]
    if isinstance(node, list):
        return node, _pointer_index(node, last, pointer=pointer, rule=rule)
    if not isinstance(node, Mapping) or last not in node:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: {pointer!r} does not exist in the response body.",
            field="params.ops",
        )
    return node, last


def _apply_pointer_op(document: Any, raw_op: object, *, rule: str) -> Any:
    if not isinstance(raw_op, Mapping):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: each entry in params.ops must be an object.",
            field="params.ops",
        )
    op = raw_op.get("op")
    if op not in ("remove", "replace", "retype"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: op must be remove, replace or retype; got {op!r}.",
            field="params.ops",
        )
    pointer = raw_op.get("pointer")
    if not isinstance(pointer, str):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: each entry in params.ops needs a string 'pointer'; got {pointer!r}.",
            field="params.ops",
        )
    segments = _pointer_segments(pointer, rule=rule)
    parent, key = _pointer_parent(document, segments, pointer=pointer, rule=rule)
    if op == "remove":
        del parent[key]
        return document
    if op == "replace":
        if "value" not in raw_op:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"body_mutation rule {rule!r}: op 'replace' on {pointer!r} requires a value.",
                field="params.ops",
            )
        parent[key] = raw_op["value"]
        return document
    current = parent[key]
    parent[key] = _retype(current, raw_op.get("as"), pointer=pointer, rule=rule)
    return document


def _retype(current: Any, as_type: object, *, pointer: str, rule: str) -> Any:
    """ "number -> its decimal string, string -> number if parseable else
    error, anything -> null" -- unless ``as`` names the target explicitly.
    """
    target = as_type if as_type in ("string", "number", "null") else _default_retype_target(current)
    if target == "string":
        return as_str(current, json.dumps(current))
    if target == "number":
        if isinstance(current, bool):
            pass  # fall through to the error below; bool is not "a number" here
        elif isinstance(current, int | float):
            return current
        elif isinstance(current, str):
            parsed = js_parse_float(current)
            if parsed is not None:
                return js_number(parsed)
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: {pointer!r} ({current!r}) cannot retype to a number.",
            field="params.ops",
        )
    return None


def _default_retype_target(current: Any) -> Literal["string", "number", "null"]:
    """The target ``retype`` picks when ``as`` is absent.

    JUDGMENT: a boolean goes to ``null``, not to ``"true"``/``"false"`` and not
    to ``1``/``0``. JSON booleans are the one scalar consumers parse with the
    least defensiveness, and a vendor that turns one into a *string* is a
    documented case nowhere; ``null`` is the corruption a real "field went
    missing in a refactor" produces, so it is the one worth rehearsing by
    default. An explicit ``as: "number"`` on a boolean is refused rather than
    coerced (``True`` is not "a number" a consumer's parser would accept from a
    vendor), and ``as: "string"`` gives the JSON spelling.
    """
    if isinstance(current, bool):
        return "null"
    if isinstance(current, int | float):
        return "string"
    if isinstance(current, str):
        return "number"
    return "null"
