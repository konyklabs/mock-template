"""The chaos rule grammar, as an external document: JSON from disk or a
request body, parsed rather than trusted. INVARIANT: a rule that cannot fire
says so when written, not by never firing -- ``extra="forbid"`` catches a
misspelled condition key, and ``every``/``times`` are bounded to ``ge=1``.
Wire format is snake_case throughout, including ``params`` keys, a promise
:data:`BUILTIN_FAULTS` states; ``params`` itself stays ``dict[str, Any]`` and
is not modelled.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.config.models import unit_error_from_validation
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "BUILTIN_FAULTS",
    "ChaosMatch",
    "ChaosRule",
    "ChaosScope",
    "ChaosWhen",
    "FaultName",
    "FaultPhase",
    "FaultProvenance",
    "FaultSpec",
    "glob_match",
    "matched_routes",
    "parse_rule",
    "validate_rule_document",
]

FaultName = str
"""Open vocabulary: :data:`BUILTIN_FAULTS` is a suggestion list, not a closed set."""

ChaosScope = Literal["request", "webhook"]
"""The only closed vocabulary here: the two scopes are gated by two different capabilities."""

_MODEL = ConfigDict(extra="forbid", frozen=True)

#: An integer the document must have written as an integer, not a quoted one.
_StrictInt = Annotated[int, Field(strict=True)]


class ChaosMatch(BaseModel):
    """Which subjects a rule applies to, every condition ANDed and an absent
    one not a veto -- a bare ``{"scope": "request", "fault": "server_error"}``
    means "fail everything"."""

    model_config = _MODEL

    #: ``POST /v2/orders``; ``*`` wildcards allowed, e.g. ``POST /v2/orders*``.
    route: str | None = None
    path: str | None = None
    method: str | None = None
    capability: str | None = None
    #: Webhook scope: the vendor event type, e.g. ``order.*``.
    event_type: str | None = None
    #: Names compared lower-cased; values compared exactly.
    header: dict[str, str] | None = None
    body_contains: str | None = None


class ChaosWhen(BaseModel):
    """When a matching subject fires the fault. Conditions ANDed, absent not a
    veto; deliberately counter-based so "the third create fails" is a fact,
    not a flake. :attr:`probability` is the one escape hatch, seeded."""

    model_config = _MODEL

    #: 1-based occurrences of a match to fire on.
    nth: tuple[Annotated[int, Field(ge=1, strict=True)], ...] | None = None
    every: Annotated[int, Field(ge=1, strict=True)] | None = None
    after: Annotated[int, Field(ge=0, strict=True)] | None = None
    times: Annotated[int, Field(ge=0, strict=True)] | None = None
    #: Accepted and a no-op, including ``always: false`` -- pinned by test.
    always: bool | None = Field(default=None, strict=True)
    probability: Annotated[float, Field(ge=0.0, le=1.0)] | None = None


class ChaosRule(BaseModel):
    """One standing rule. Frozen: the engine hands copies out, never this."""

    model_config = _MODEL

    id: str = Field(min_length=1)
    scope: ChaosScope
    fault: FaultName = Field(min_length=1)
    match: ChaosMatch | None = None
    when: ChaosWhen | None = None
    params: dict[str, Any] | None = None
    #: Free text shown in the control-plane listing.
    note: str | None = None


FaultProvenance = Literal["vendor", "transport"]
"""``"vendor"`` reproduces documented behaviour; ``"transport"`` is a dropped
connection or mangled body no vendor documents."""

FaultPhase = Literal["request", "response", "delivery"]
"""When a fault fires relative to the handler (konyklabs/roadmap#101, item
17): instead of it, on its committed answer, or as a webhook delivery."""


@dataclass(frozen=True, slots=True)
class FaultSpec:
    """One fault a fork gets without writing any code. Published by ``/__unit/info``."""

    name: FaultName
    scope: ChaosScope
    summary: str
    #: Keyword-only so a four-positional call cannot silently misbind this.
    provenance: FaultProvenance = field(kw_only=True, default="vendor")
    phase: FaultPhase = field(kw_only=True, default="request")
    params: str | None = None

    def as_json(self) -> dict[str, object]:
        body: dict[str, object] = {
            "name": self.name,
            "scope": self.scope,
            "summary": self.summary,
            "provenance": self.provenance,
            "phase": self.phase,
        }
        if self.params is not None:
            body["params"] = self.params
        return body


BUILTIN_FAULTS: tuple[FaultSpec, ...] = (
    FaultSpec(
        "rate_limit",
        "request",
        "Reject the request as rate limited.",
        "retry_after_seconds?",
        provenance="vendor",
        phase="request",
    ),
    FaultSpec(
        "server_error", "request", "Fail the request with a vendor-shaped 5xx.", provenance="vendor", phase="request"
    ),
    FaultSpec(
        "unavailable", "request", "Fail the request as temporarily unavailable.", provenance="vendor", phase="request"
    ),
    FaultSpec(
        "timeout",
        "request",
        "Stall the request, then fail it.",
        "delay_ms (default 100)",
        provenance="vendor",
        phase="request",
    ),
    FaultSpec(
        "token_expiry",
        "request",
        "Treat the caller token as expired mid-flow, without touching stored state.",
        provenance="vendor",
        phase="request",
    ),
    FaultSpec(
        "webhook.duplicate",
        "webhook",
        "Deliver the same event body more than once.",
        "copies (default 1 extra)",
        provenance="vendor",
        phase="delivery",
    ),
    FaultSpec(
        "webhook.out_of_order",
        "webhook",
        "Hold this event until the next one has been delivered.",
        provenance="vendor",
        phase="delivery",
    ),
    FaultSpec(
        "webhook.drop_ack",
        "webhook",
        "Ignore a successful subscriber response so the retry schedule runs.",
        provenance="vendor",
        phase="delivery",
    ),
    FaultSpec("webhook.delay", "webhook", "Delay delivery.", "delay_ms", provenance="vendor", phase="delivery"),
    FaultSpec(
        "webhook.drop",
        "webhook",
        "Silently swallow the delivery: recorded as dropped, never sent to the subscriber. "
        "Filter with match.event_type.",
        provenance="vendor",
        phase="delivery",
    ),
    # -- transport faults: provenance: transport -- see FaultProvenance above.
    FaultSpec(
        "malformed_body",
        "request",
        "Replace a successful response's body with something the vendor's own schema forbids.",
        "mode (invalid_json|html|empty|truncate), status (default 200; html defaults 502)",
        provenance="transport",
        phase="response",
    ),
    FaultSpec(
        "body_mutation",
        "request",
        "Apply RFC 6901 JSON-pointer operations to a successful JSON response body, after the handler ran.",
        "ops (list of {op, pointer, value?, as?})",
        provenance="transport",
        phase="response",
    ),
    FaultSpec(
        "connection_reset",
        "request",
        "Drop the connection after the response starts, before it completes.",
        provenance="transport",
        phase="response",
    ),
    FaultSpec(
        "empty_response",
        "request",
        "Drop the connection as close to before any bytes as the binding can manage.",
        provenance="transport",
        phase="response",
    ),
    FaultSpec(
        "slow_body",
        "request",
        "Stream a successful response body in chunks, with a delay between them.",
        "chunk_bytes (default 64), chunk_delay_ms (default 100)",
        provenance="transport",
        phase="response",
    ),
)
"""The faults the core implements, as data; ``params`` is a promise each
implementation reads exactly those keys and coerces rather than indexes."""


def glob_match(pattern: str, value: str) -> bool:
    """``*`` is the only metacharacter, matched anchored at both ends: a
    pattern with no ``*`` short-circuits on equality and never builds a regex.
    """
    if pattern == value:
        return True
    if "*" not in pattern:
        return False
    expression = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(expression, value) is not None


def matched_routes(rule: ChaosRule, route_keys: Sequence[str]) -> tuple[str, ...]:
    """Which of ``route_keys`` this rule's ``match.route`` selects; no
    ``match.route`` selects every route, matching the engine's own behaviour.
    """
    if rule.match is None or rule.match.route is None:
        return tuple(route_keys)
    return tuple(key for key in route_keys if glob_match(rule.match.route, key))


def validate_rule_document(document: Mapping[str, Any]) -> None:
    """An absent or empty ``id``/``fault`` is ``missing_field``; an absent
    ``scope`` is ``invalid_value``. No capability check here -- that needs the
    registry, so the control plane performs it.
    """
    identifier = document.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise UnitError(
            UnitErrorKind.MISSING_FIELD,
            detail="A chaos rule requires an id.",
            field="id",
        )
    fault = document.get("fault")
    if not isinstance(fault, str) or not fault:
        raise UnitError(
            UnitErrorKind.MISSING_FIELD,
            detail="A chaos rule requires a fault.",
            field="fault",
        )
    if document.get("scope") not in ("request", "webhook"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="A chaos rule scope must be 'request' or 'webhook'.",
            field="scope",
        )


def parse_rule(document: object, *, source: str | None = None) -> ChaosRule:
    """Validate one rule document, or raise a field-naming ``UnitError``.
    :func:`validate_rule_document`'s checks run first, so a missing ``id``
    reports ``missing_field`` on ``id`` and not whichever field Pydantic
    happens to complain about."""
    if not isinstance(document, Mapping):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="A chaos rule must be an object.",
            field="rule",
        )
    validate_rule_document(document)
    try:
        return ChaosRule.model_validate(dict(document))
    except ValidationError as exc:
        raise unit_error_from_validation(exc, source=source) from exc
