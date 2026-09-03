"""The profile document and the resolved configuration, as Pydantic models.

FOR: replacing three separate hand-rolled mechanisms in
``packages/core/src/config/profile.ts`` -- an unvalidated ``JSON.parse``, a
hand-written shallow-plus-one-level merge, and a chain of ``??`` defaults --
with one schema that does all three and reports a field path when it fails.

INVARIANT: **a malformed or misspelled profile is a startup failure that names
the field.** The reference parses a profile with no schema at all, so a
``"capabilties"`` typo is not an error, it is a profile with no capabilities,
and the first symptom is a 501 on an endpoint the author believed they had
enabled. Every model here sets ``extra="forbid"``, so the typo is caught at
load with its path.

Four decisions this file makes that the reference did not have to
------------------------------------------------------------------
**No vendor defaults live here.** ``RetryPolicy.schedule_ms`` defaults to
**empty** and ``time_scale`` to ``1.0``. The reference's ``DEFAULT_RETRY``
hard-codes one vendor's documented eleven-retry schedule and the ``1/6000``
scale derived from that vendor's documented one-minute first retry -- both are
vendor facts sitting in shared code. Here the vendor supplies them through
``VendorDefinition.retry_defaults``, which is merged *under* the profile
document. Unit construction then asserts the schedule is non-empty whenever
the webhooks capability is declared, so an unmerged default is a loud startup
error rather than a delivery that reports "exhausted" on its first attempt.

**``extra="forbid"`` and lax coercion, not strict mode.** Strict mode would
reject a JSON array for a ``tuple[...]`` field, and profile authors write JSON.
The error class strictness would have caught -- ``"2000"`` where an int was
meant -- is one the reference coerced silently anyway (``Number(...)``), while
the error ``extra="forbid"`` catches, a misspelled key, is the one that
actually happens and the one the reference could not catch at all.

**Frozen models with tuple containers.** A resolved configuration is read by
every subsystem for the life of the unit; if any one of them could append to
``capabilities`` the others would disagree about what the profile said.

**Chaos rules stay opaque here.** ``rules`` is a tuple of raw documents rather
than a parsed rule model. The rule grammar belongs with the engine that
evaluates it -- it is also the body of ``POST /__unit/chaos/rules``, so it must
live somewhere both the profile loader and the control plane can reach, and
defining it here would make the loader a second place where the grammar is
stated. The engine parses the documents at construction and reports the same
field-path error this module would have.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "ChaosSection",
    "ClockSection",
    "ErrorsSection",
    "ProfileDocument",
    "ResolvedConfig",
    "RetryPolicy",
    "SubscriberConfig",
    "TransportSection",
    "WebhooksSection",
    "merged_over",
    "parse_profile_document",
    "unit_error_from_validation",
]

_MODEL = ConfigDict(extra="forbid", frozen=True)

ModelT = TypeVar("ModelT", bound=BaseModel)


def merged_over(base: ModelT, block: Mapping[str, Any]) -> ModelT:
    """``base`` with ``block`` laid over it, revalidated as ``base``'s own type.

    The profile wins over the base, which is the precedence every layer in
    this project uses: defaults under the vendor's own values, those under the
    profile document, that under the environment. The merge **revalidates
    rather than patching field by field**, so an unknown key in ``block`` is
    refused exactly as it would be on a fresh parse (``extra="forbid"``), a
    value of the wrong shape fails naming its field, and a frozen model is
    never mutated -- ``model_copy(update=...)`` would do all three wrong.

    Every vendor's ``<Vendor>Config.merged_with`` is this call; it lives here
    so the idiom is written once and typed once.
    """
    return type(base).model_validate({**base.model_dump(), **dict(block)})


class RetryPolicy(BaseModel):
    """Delivery retry shape. **No vendor defaults**; see the module docstring."""

    model_config = _MODEL

    #: Delay before attempt N+1, in milliseconds, before scaling. Empty in the
    #: core: a vendor supplies its own documented schedule.
    schedule_ms: tuple[int, ...] = ()
    #: Multiplier applied to every delay. Real vendors retry over hours and a
    #: test suite cannot wait; scaling keeps the SHAPE of the documented
    #: schedule while making it observable. ``1.0`` means "no compression",
    #: which is the only vendor-neutral default there is.
    time_scale: float = 1.0
    #: Milliseconds to wait for a subscriber before calling it a timeout.
    timeout_ms: int = 10_000


class SubscriberConfig(BaseModel):
    """One webhook subscriber declared in configuration.

    ``event_types`` and ``signature_key`` are required rather than defaulted: a
    subscriber with no signing key is a subscriber whose deliveries cannot be
    verified, and defaulting one would hide that.
    """

    model_config = _MODEL

    id: str | None = None
    name: str | None = None
    notification_url: str
    event_types: tuple[str, ...]
    signature_key: str
    enabled: bool = True


class WebhooksSection(BaseModel):
    """The profile's ``webhooks`` block."""

    model_config = _MODEL

    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    subscribers: tuple[SubscriberConfig, ...] = ()
    #: Fail fast instead of retrying -- used by conformance to stay quick.
    disable_delivery: bool = False


class ChaosSection(BaseModel):
    """The profile's ``chaos`` block. Rules stay opaque here by design."""

    model_config = _MODEL

    seed: int = 1
    rules: tuple[dict[str, Any], ...] = ()
    #: Refuse a rule whose ``match.route`` names no registered route, instead
    #: of logging a NOTE and carrying on.
    #:
    #: The reference validates a rule's ``id``, ``fault`` and ``scope`` and
    #: never checks the route, so a typo -- or a path template that moved from
    #: ``:order_id`` to ``{order_id}`` -- is a rule that matches nothing,
    #: forever, silently, and the first symptom is a chaos demo transcript in
    #: which two of four rules did nothing. Shipped profiles set this true;
    #: the default is false because a rule aimed at a route whose capability is
    #: temporarily switched off is a legitimate thing to write.
    strict_rules: bool = False


class ClockSection(BaseModel):
    """The profile's ``clock`` block."""

    model_config = _MODEL

    mode: Literal["real", "virtual"] = "real"
    #: RFC 3339 instant the virtual clock starts at. Ignored in real mode.
    start: str | None = None


class ErrorsSection(BaseModel):
    """The profile's ``errors`` block. Native to this project; the reference
    has no equivalent, because it never emitted the ``unit_error`` sidecar
    anywhere but the body.

    ``sidecar`` says *where* the sidecar rides, not *whether* it exists at
    all -- that switch is each vendor's own ``vendor.error_sidecar`` (e.g.
    :attr:`~vendorfake.square.config.SquareConfig.error_sidecar`), unchanged.
    ``"headers"`` (the default, since konyklabs/roadmap#71) keeps a vendor's
    body byte-for-byte identical to a real, recorded response, which the body
    key never was: a consumer substituting a recorded fixture for this fake's
    answer would see an extra field the real vendor never sends. ``"body"``
    restores the v0.1 behaviour for one minor release
    (DEPRECATED -- see CHANGELOG.md) and ``"both"`` emits it in both places.
    """

    model_config = _MODEL

    sidecar: Literal["headers", "body", "both"] = "headers"


class TransportSection(BaseModel):
    """Which binding the CLI should stand up, and where.

    Resolved from the environment only, exactly as the reference does it: a
    profile describes a vendor's behaviour, not the socket it is served on, so
    the same profile is reusable across an HTTP run and an in-process one.
    """

    model_config = _MODEL

    kind: str = "http"
    port: int = 8080
    host: str | None = None
    dir: str | None = None


class ProfileDocument(BaseModel):
    """A profile as it is written on disk.

    Every field is optional. That is what makes the merge in
    ``profile.py`` expressible as "the fields this document actually set win
    over the fields the layer beneath it set" -- Pydantic's
    ``model_fields_set`` is the record of which those were, and it is the piece
    the reference had to approximate with ``{...a, ...b}``.
    """

    model_config = _MODEL

    name: str | None = None
    summary: str | None = None
    capabilities: tuple[str, ...] = ()
    #: Path to the seed document, relative to the profile's directory.
    seed: str | None = None
    #: Vendor-specific settings, passed through uninterpreted.
    vendor: dict[str, Any] = Field(default_factory=dict)
    webhooks: WebhooksSection = Field(default_factory=WebhooksSection)
    chaos: ChaosSection = Field(default_factory=ChaosSection)
    clock: ClockSection = Field(default_factory=ClockSection)
    errors: ErrorsSection = Field(default_factory=ErrorsSection)


class ResolvedWebhooks(BaseModel):
    """``webhooks`` after the environment layer."""

    model_config = _MODEL

    retry: RetryPolicy
    subscribers: tuple[SubscriberConfig, ...] = ()
    disable_delivery: bool = False


class ResolvedChaos(BaseModel):
    """``chaos`` after the environment layer."""

    model_config = _MODEL

    seed: int
    rules: tuple[dict[str, Any], ...] = ()
    #: See :attr:`ChaosSection.strict_rules`. No environment override: it
    #: changes whether a unit *starts*, and a variable that can stop a
    #: container booting belongs in the profile a reader can diff.
    strict_rules: bool = False


class ResolvedConfig(BaseModel):
    """What the kernel and every subsystem read. Decided once, then frozen."""

    model_config = _MODEL

    profile: str
    capabilities: tuple[str, ...] = ()
    seed_path: str | None = None
    vendor_config: dict[str, Any] = Field(default_factory=dict)
    webhooks: ResolvedWebhooks
    chaos: ResolvedChaos
    clock: ClockSection
    errors: ErrorsSection
    transport: TransportSection
    #: Read here rather than by the logger, so no module reaches for the
    #: process environment on its own. The reference's ``createLogger``
    #: defaulted straight from ``process.env``, which is the leak this closes.
    log_level: str = "info"


def unit_error_from_validation(exc: ValidationError, *, source: str | None = None) -> UnitError:
    """Turn a Pydantic failure into the ``UnitError`` the rest of the core speaks.

    Without this, a validation failure inside a core handler reaches the
    kernel's catch-all and becomes ``internal`` -- a 500 -- where the contract
    is a 400 naming the field. A missing field reports ``missing_field`` and
    anything else reports ``invalid_value``, which is the split the reference's
    hand-written validators made and which conformance asserts.
    """
    errors = exc.errors()
    missing = next((err for err in errors if err.get("type") == "missing"), None)
    chosen = missing if missing is not None else (errors[0] if errors else None)
    kind = UnitErrorKind.MISSING_FIELD if missing is not None else UnitErrorKind.INVALID_VALUE
    field = ".".join(str(part) for part in chosen["loc"]) if chosen is not None else None
    message = chosen["msg"] if chosen is not None else "validation failed"
    where = f" in {source}" if source else ""
    return UnitError(
        kind,
        detail=f"{field or '(document)'}{where}: {message}",
        field=field,
        info={
            "source": source,
            "errors": [
                {
                    "field": ".".join(str(part) for part in err["loc"]),
                    "type": err["type"],
                    "message": err["msg"],
                }
                for err in errors
            ],
        },
    )


def parse_profile_document(data: object, *, source: str | None = None) -> ProfileDocument:
    """Validate a decoded JSON profile, or raise a field-naming ``UnitError``."""
    try:
        return ProfileDocument.model_validate(data)
    except ValidationError as exc:
        raise unit_error_from_validation(exc, source=source) from exc
