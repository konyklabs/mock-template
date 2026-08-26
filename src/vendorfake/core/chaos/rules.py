"""The chaos rule grammar, as an external document.

FOR: stating once what a chaos rule *is*, in the one place both the profile
loader and ``POST /__unit/chaos/rules`` can reach. A rule arrives from two
directions -- a JSON file on disk and a JSON request body -- and neither is
written by this codebase, so it is parsed, not trusted.

INVARIANT: **a rule that cannot fire says so at the moment it is written, not
by never firing.** That is the whole reason this file is Pydantic (one of the
four core modules where it is permitted, listed in ``tools/boundary.toml``)
rather than a dataclass and a pile of ``isinstance`` checks. Three specific
silences are closed here:

``extra="forbid"``
    ``{"when": {"nth": [2]}}`` misspelled as ``{"when": {"nth_": [2]}}`` is an
    unconditional rule in the reference -- ``shouldFire`` sees no recognised
    condition and fires on every match. The typo that actually happens is a
    misspelled key, and it is the one a permissive parser cannot catch.

``every`` and ``times`` are bounded
    ``{"every": 0}`` is ``st.matches % 0`` -- ``NaN`` in JavaScript, so the
    rule silently never fires; ``ZeroDivisionError`` in Python, so it would be
    a 500 on the first matching request. ``ge=1`` makes it a field-naming 400
    at the moment the rule is submitted, which is the only one of the three a
    consumer can act on.

Numbers are strict, strings are not
    ``{"every": "3"}`` works in JavaScript because ``matches % "3"`` coerces.
    Accepting it here would mean a profile could carry a quoted number for
    years and nobody would learn which of the two the author meant. Strings
    stay unconstrained because ``fault``, ``route`` and ``event_type`` are open
    vocabularies by design -- a fork adds a fault name without editing core.

**Wire format is snake_case**, here as everywhere else in this package:
``event_type``, ``body_contains``. The reference spelled these ``eventType``
and ``bodyContains``; one convention across profile documents, control-plane
bodies and response keys was chosen in an earlier stage of this build, and a
rule document is on the wire in both directions. ``params`` keys follow the
same convention -- ``delay_ms``, ``retry_after_seconds``, ``copies`` -- which
:data:`BUILTIN_FAULTS` states as a promise the fault implementations must keep.

``params`` itself stays ``dict[str, Any]`` and is *not* modelled. Its shape is
per-fault and a fork may add faults the core has never heard of, so validating
it here would be the core asserting a vendor's vocabulary. The cost is real and
is paid at the point of use: values arrive as strings on the magic path
(``chaos:timeout:delay_ms=250`` is split textually) and as arbitrary JSON on
the rule path, so every consumption site coerces explicitly rather than
indexing and hoping.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    "FaultSpec",
    "glob_match",
    "matched_routes",
    "parse_rule",
    "validate_rule_document",
]

FaultName = str
"""Open vocabulary. The reference writes ``'rate_limit' | ... | (string & {})``;
Python has no such construct and does not need one -- :data:`BUILTIN_FAULTS` is
the suggestion list and a fork's own fault name is just as valid."""

ChaosScope = Literal["request", "webhook"]
"""Closed, and the only closed vocabulary in the grammar: the two scopes are
gated by two different capabilities, so a third would be a capability nobody
declared."""

_MODEL = ConfigDict(extra="forbid", frozen=True)

#: An integer the document must have written as an integer. See the module
#: docstring: a quoted number is a question about intent, not a value.
_StrictInt = Annotated[int, Field(strict=True)]


class ChaosMatch(BaseModel):
    """Which subjects a rule applies to. Every condition is ANDed.

    An absent condition is not a veto: a rule with no ``match`` at all applies
    to every subject in its scope. That is the reference's rule
    (``engine.ts:matches`` returns ``true`` for an absent ``match``) and it is
    what makes ``{"scope": "request", "fault": "server_error"}`` mean "fail
    everything", which is the first thing anyone tries.
    """

    model_config = _MODEL

    #: ``POST /v2/orders``; ``*`` wildcards allowed, e.g. ``POST /v2/orders*``.
    #: Path templates are braces -- ``GET /v2/orders/{order_id}`` -- in every
    #: place a template is written, this key included.
    route: str | None = None
    path: str | None = None
    method: str | None = None
    capability: str | None = None
    #: Webhook scope: match on the vendor event type, e.g. ``order.*``.
    event_type: str | None = None
    #: Header names are compared lower-cased; values are compared exactly.
    header: dict[str, str] | None = None
    body_contains: str | None = None


class ChaosWhen(BaseModel):
    """When a matching subject actually fires the fault.

    Conditions are ANDed and an absent condition is not a veto, so an empty
    ``when`` fires on every match. Deliberately counter-based: "the third
    create fails" is a fact a test can assert, where a coin flip is a flake.
    :attr:`probability` is the one escape hatch and it draws from the seeded
    RNG, so even that run is replayable from ``/__unit/info``.
    """

    model_config = _MODEL

    #: Fire on these 1-based occurrences of a match.
    nth: tuple[Annotated[int, Field(ge=1, strict=True)], ...] | None = None
    #: Fire on every Nth match. ``ge=1``: see the module docstring on ``% 0``.
    every: Annotated[int, Field(ge=1, strict=True)] | None = None
    #: Fire only after N matches have already passed cleanly.
    after: Annotated[int, Field(ge=0, strict=True)] | None = None
    #: Stop firing after this many fires.
    times: Annotated[int, Field(ge=0, strict=True)] | None = None
    #: Accepted, and a no-op in both directions -- including ``always: false``.
    #: The reference declares it and ``shouldFire`` never reads it, so a rule
    #: with no other condition already fires on every match and one that says
    #: ``always: false`` still fires. Kept rather than rejected because a
    #: profile that says what it means is worth more than a 400, and dropping
    #: the field would make the reference's own documents invalid. Pinned by
    #: test so nobody "fixes" it into a veto without meeting the decision.
    always: bool | None = Field(default=None, strict=True)
    #: Seeded-random firing; see the class docstring.
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


@dataclass(frozen=True, slots=True)
class FaultSpec:
    """One fault a fork gets without writing any code. Published by ``/__unit/info``."""

    name: FaultName
    scope: ChaosScope
    summary: str
    #: Prose description of the ``params`` keys this fault reads, if any.
    params: str | None = None

    def as_json(self) -> dict[str, object]:
        body: dict[str, object] = {"name": self.name, "scope": self.scope, "summary": self.summary}
        if self.params is not None:
            body["params"] = self.params
        return body


BUILTIN_FAULTS: tuple[FaultSpec, ...] = (
    FaultSpec("rate_limit", "request", "Reject the request as rate limited.", "retry_after_seconds?"),
    FaultSpec("server_error", "request", "Fail the request with a vendor-shaped 5xx."),
    FaultSpec("unavailable", "request", "Fail the request as temporarily unavailable."),
    FaultSpec("timeout", "request", "Stall the request, then fail it.", "delay_ms (default 100)"),
    FaultSpec(
        "token_expiry",
        "request",
        "Treat the caller token as expired mid-flow, without touching stored state.",
    ),
    FaultSpec(
        "webhook.duplicate", "webhook", "Deliver the same event body more than once.", "copies (default 1 extra)"
    ),
    FaultSpec("webhook.out_of_order", "webhook", "Hold this event until the next one has been delivered."),
    FaultSpec(
        "webhook.drop_ack",
        "webhook",
        "Ignore a successful subscriber response so the retry schedule runs.",
    ),
    FaultSpec("webhook.delay", "webhook", "Delay delivery.", "delay_ms"),
    FaultSpec(
        "webhook.drop",
        "webhook",
        "Silently swallow the delivery: recorded as dropped, never sent to the subscriber. "
        "Filter with match.event_type.",
    ),
)
"""The faults the core implements, as data. The ``params`` prose is a promise:
a fault implementation reads exactly these keys, in snake_case, and coerces
them rather than indexing them."""


def glob_match(pattern: str, value: str) -> bool:
    """``*`` is the only metacharacter; everything else is literal.

    Ported from ``engine.ts:globMatch``. The exact-equality short circuit comes
    first, so a pattern with no ``*`` never builds a regex, and a pattern
    without ``*`` that is not equal cannot match -- which is why a route key is
    compared verbatim and a typo produces silence rather than a near miss.

    Anchored at both ends: ``POST /v2/orders`` does not match
    ``POST /v2/orders/search``, and ``POST /v2/orders*`` does.
    """
    if pattern == value:
        return True
    if "*" not in pattern:
        return False
    expression = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(expression, value) is not None


def matched_routes(rule: ChaosRule, route_keys: Sequence[str]) -> tuple[str, ...]:
    """Which of ``route_keys`` this rule's ``match.route`` selects.

    Pure, and separate from the engine, because the answer is needed at two
    moments the engine is not present for: unit construction, where a
    profile-supplied rule matching nothing is worth a NOTE on the log or a hard
    ``invalid_value`` under a strict setting, and ``POST /__unit/chaos/rules``,
    where the count is echoed back so a consumer learns immediately rather than
    from a transcript with a dead rule in it.

    A rule with no ``match.route`` constrains no route and so selects them all:
    that is what the engine will do, and reporting zero here would be a
    different answer from the one the engine gives.
    """
    if rule.match is None or rule.match.route is None:
        return tuple(route_keys)
    return tuple(key for key in route_keys if glob_match(rule.match.route, key))


def validate_rule_document(document: Mapping[str, Any]) -> None:
    """Reproduce the reference's ``validateRule`` error kinds, exactly.

    Ported from ``control/plane.ts:validateRule``. It exists beside Pydantic
    rather than inside it because the three kinds are contract and Pydantic's
    structural verdicts do not line up with them: an absent ``id`` and an empty
    ``id`` are both ``missing_field`` in the reference (``if (!r.id)``) where a
    ``min_length`` constraint would call the second one ``invalid_value``, and
    an absent ``scope`` is ``invalid_value`` where a required field would be
    ``missing_field``.

    The webhook-scope capability assertion is deliberately NOT here: it needs
    the registry, and putting it in a pure parser would make this module the
    second place that gates ``webhooks.chaos``. The control plane performs it.
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

    Order is contract: the three reference-compatible checks run first, so a
    document missing an ``id`` reports ``missing_field`` on ``id`` and not
    whichever field Pydantic happened to complain about first.
    """
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
