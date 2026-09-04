"""What an armed request-scope fault does: turns a :class:`ChaosDecision` into
the ``UnitError`` the pipeline raises, in one place.
INVARIANT: which phase a fault fires in is a property of the fault, not the
call site. ``token_expiry`` fires only after authentication; every other
request-scope fault fires before it, and the pipeline calls this function
twice, unconditionally, once per phase.
``timeout`` never parks on a virtual timer, which would deadlock the request
lock; real mode reports the delay on :attr:`UnitError.delay_ms`, virtual mode
advances :class:`Clock` and reports ``delay_ms=0`` (konyklabs/roadmap#101,
item 18). Parameters are coerced, never indexed -- :data:`FAULT_PARAM_KEYS` is
the promise of which keys each fault reads. Response-scope faults
(:data:`RESPONSE_PHASE_FAULTS`) are a third phase, run by
:func:`apply_response_fault` once an answer exists to corrupt.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from vendorfake.core.chaos.engine import ChaosDecision
from vendorfake.core.chaos.rules import BUILTIN_FAULTS, FaultProvenance
from vendorfake.core.chaos.rules import FaultPhase as _PublishedPhase
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
    "FAULT_PHASE",
    "FAULT_PROVENANCE",
    "INTACT_RESPONSE_FAULTS",
    "RESPONSE_PHASE_FAULTS",
    "RequestMoment",
    "apply_request_fault",
    "apply_response_fault",
    "is_transport_fault",
]

RequestMoment = Literal["pre", "post_auth"]
"""The two moments the pipeline offers a request-phase fault; response-phase
faults are a third, with no argument of their own."""

AUTH_PHASE_FAULTS: frozenset[str] = frozenset({"token_expiry"})
"""Faults that fire after authentication. Exactly one today."""

FAULT_PHASE: Mapping[str, _PublishedPhase] = {spec.name: spec.phase for spec in BUILTIN_FAULTS}
"""Phase per fault, derived from the catalogue."""

RESPONSE_PHASE_FAULTS: frozenset[str] = frozenset(name for name, phase in FAULT_PHASE.items() if phase == "response")
"""Faults applied to a successful handler response -- see :func:`apply_response_fault`."""

INTACT_RESPONSE_FAULTS: frozenset[str] = frozenset({"slow_body"})
"""Response-phase faults that hand back the handler's answer *unchanged* and
only shape how it travels (``kernel/unit.py``'s ``discarded_mutation``)."""

#: Defaults when a fault's own param is absent.
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
"""Every parameter key a built-in fault reads, snake_case, keyed by fault
name -- checkable against the catalogue in ``chaos/rules.py``."""

FAULT_DESCRIPTIONS: Mapping[str, str] = {spec.name: spec.summary for spec in BUILTIN_FAULTS}
"""One-line description per fault, derived from
:data:`~vendorfake.core.chaos.rules.BUILTIN_FAULTS`."""

FAULT_PROVENANCE: Mapping[str, FaultProvenance] = {spec.name: spec.provenance for spec in BUILTIN_FAULTS}
""":attr:`~vendorfake.core.chaos.rules.FaultSpec.provenance` per fault, what
:func:`is_transport_fault` reads."""


def _delay_owed(clock: Clock, delay_ms: float) -> int:
    """What the binding still owes in wall-clock milliseconds: zero on a
    virtual clock, ``delay_ms`` on a real one. ``math.ceil``, not ``round`` --
    banker's rounding would send ``0.4`` and ``0.5`` both to zero.
    """
    if delay_ms <= 0:
        return 0
    if clock.mode == "virtual":
        clock.advance(delay_ms)
        return 0
    return max(0, math.ceil(delay_ms))


def apply_request_fault(
    decision: ChaosDecision,
    phase: RequestMoment,
    *,
    clock: Clock,
    log: Logger,
) -> None:
    """Raise the ``UnitError`` this decision stands for, if this is its phase.
    Returns normally -- doing nothing -- when the decision belongs to the
    other phase, is a response-scope fault, or names a fault this core has
    never heard of. The last only warns: the fault vocabulary is open by
    design, and a fork adds a name without editing the core.
    """
    if decision.fault in RESPONSE_PHASE_FAULTS:
        # A real fault; it fires from apply_response_fault instead.
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


# -- Response-scope faults: transport-fidelity, provenance: transport. -------
# Corrupts a *successful* handler response, called after it returns.

_HTML_ERROR_PAGE = (
    b"<html><head><title>Bad Gateway</title></head>"
    b"<body><h1>Bad Gateway</h1><p>vendorfake: injected transport fault.</p></body></html>"
)
"""``malformed_body`` mode ``html``: a generic, vendor-neutral error page,
because no vendor sent it -- a proxy or load balancer did."""


def is_transport_fault(response: UnitResponse) -> bool:
    """Whether ``response`` came from a transport-fidelity fault
    (``provenance: "transport"``) rather than any fault at all -- a validator
    must not fail a ``malformed_body`` response for violating the vendor's
    schema, but must still validate a ``rate_limit`` 429. Reads the
    ``vendorfake-fault`` header and looks up its provenance, since every
    faulted response carries that header regardless of kind
    (konyklabs/roadmap#73). Unrecognised is not transport.
    """
    fault = response.headers.get("vendorfake-fault")
    if fault is None:
        return False
    return FAULT_PROVENANCE.get(fault) == "transport"


def apply_response_fault(decision: ChaosDecision, response: UnitResponse, *, log: Logger) -> UnitResponse:
    """Corrupt the answer for a response-scope fault, or hand it back
    unchanged. Runs after any clean response is stored against the
    idempotency key, so no fault enters the store."""
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
    """The two mechanism headers every faulted response carries."""
    headers["vendorfake-fault"] = fault
    headers["vendorfake-rule"] = header_text(rule)


def _malformed_body(response: UnitResponse, params: Mapping[str, Any], *, fault: str, rule: str) -> UnitResponse:
    """``mode: invalid_json | html | empty | truncate``. Keeps its own status
    unless ``params.status`` says otherwise; ``html`` defaults to 502."""
    mode = params.get("mode")
    if mode not in ("invalid_json", "html", "empty", "truncate"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"malformed_body rule {rule!r}: params.mode must be one of invalid_json, html, empty, truncate; "
                f"got {mode!r}."
            ),
            field="params.mode",
            rule_id=rule,
        )
    default_status = 502 if mode == "html" else response.status
    status = as_int(params.get("status"), default_status)
    headers = dict(response.headers)
    if mode == "html":
        body = _HTML_ERROR_PAGE
        headers["content-type"] = "text/html"
    elif mode == "empty":
        body = b""
        headers["content-type"] = "application/json"
    elif mode == "invalid_json":
        # Last byte dropped, so the JSON never closes, and a comma appended.
        body = response.body[:-1] + b","
    else:  # truncate
        body = response.body[: len(response.body) // 2]
    _stamp(headers, fault, rule)
    return UnitResponse(status=status, headers=headers, body=body, delay_ms=response.delay_ms)


def _body_mutation(response: UnitResponse, params: Mapping[str, Any], *, fault: str, rule: str) -> UnitResponse:
    """Apply every ``ops`` entry, in order, to the response's own JSON body.
    A non-JSON response is reported at fire time, not rule-add time."""
    raw_ops = params.get("ops")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: params.ops must be a non-empty list of pointer operations.",
            field="params.ops",
            rule_id=rule,
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
            rule_id=rule,
        ) from exc
    for raw_op in raw_ops:
        document = _apply_pointer_op(document, raw_op, rule=rule)
    headers = dict(response.headers)
    _stamp(headers, fault, rule)
    return UnitResponse(status=response.status, headers=headers, body=dump_json(document), delay_ms=response.delay_ms)


def _directive(response: UnitResponse, fault: str, params: Mapping[str, Any], *, rule: str) -> UnitResponse:
    """``connection_reset`` / ``empty_response`` / ``slow_body``: leave the
    body alone and attach the instruction a binding interprets."""
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
            rule_id=rule,
        )
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer.split("/")[1:]]


def _pointer_step(node: Any, segment: str, *, pointer: str, rule: str) -> Any:
    if isinstance(node, Mapping):
        if segment not in node:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"body_mutation rule {rule!r}: {pointer!r} does not exist in the response body.",
                field="params.ops",
                rule_id=rule,
            )
        return node[segment]
    if isinstance(node, list):
        return node[_pointer_index(node, segment, pointer=pointer, rule=rule)]
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"body_mutation rule {rule!r}: {pointer!r} walks into a scalar value.",
        field="params.ops",
        rule_id=rule,
    )


def _pointer_index(node: list[Any], segment: str, *, pointer: str, rule: str) -> int:
    if not segment.isdigit() or int(segment) >= len(node):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: {pointer!r} is not a valid index into a {len(node)}-element array.",
            field="params.ops",
            rule_id=rule,
        )
    return int(segment)


def _pointer_parent(document: Any, segments: Sequence[str], *, pointer: str, rule: str) -> tuple[Any, str | int]:
    if not segments:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: pointer {pointer!r} must not name the whole document.",
            field="params.ops",
            rule_id=rule,
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
            rule_id=rule,
        )
    return node, last


def _apply_pointer_op(document: Any, raw_op: object, *, rule: str) -> Any:
    if not isinstance(raw_op, Mapping):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: each entry in params.ops must be an object.",
            field="params.ops",
            rule_id=rule,
        )
    op = raw_op.get("op")
    if op not in ("remove", "replace", "retype"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: op must be remove, replace or retype; got {op!r}.",
            field="params.ops",
            rule_id=rule,
        )
    pointer = raw_op.get("pointer")
    if not isinstance(pointer, str):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"body_mutation rule {rule!r}: each entry in params.ops needs a string 'pointer'; got {pointer!r}.",
            field="params.ops",
            rule_id=rule,
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
                rule_id=rule,
            )
        parent[key] = raw_op["value"]
        return document
    current = parent[key]
    parent[key] = _retype(current, raw_op.get("as"), pointer=pointer, rule=rule)
    return document


def _retype(current: Any, as_type: object, *, pointer: str, rule: str) -> Any:
    """Number to string, string to number if parseable, else null -- unless
    ``as`` names the target explicitly."""
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
            rule_id=rule,
        )
    return None


def _default_retype_target(current: Any) -> Literal["string", "number", "null"]:
    """The target ``retype`` picks when ``as`` is absent. JUDGMENT: a boolean
    goes to ``null`` rather than a string or number, the corruption a real
    "field went missing" produces."""
    if isinstance(current, bool):
        return "null"
    if isinstance(current, int | float):
        return "string"
    if isinstance(current, str):
        return "number"
    return "null"
