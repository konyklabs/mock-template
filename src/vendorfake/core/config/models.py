"""The profile document and the resolved configuration, as Pydantic models.
INVARIANT: a malformed or misspelled profile is a startup failure that names
the field -- every model sets ``extra="forbid"``, so a typo like
``"capabilties"`` is caught at load rather than silently producing a profile
with no capabilities. No vendor defaults live here (a vendor supplies its own
retry schedule, merged under the profile document); models are frozen with
tuple containers; and chaos ``rules`` stay raw documents, parsed by the
engine that evaluates them rather than a second time here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.webhooks.models import check_notification_url

__all__ = [
    "UNMATCHED_POLICIES",
    "ChaosSection",
    "ClockSection",
    "ErrorsSection",
    "ProfileDocument",
    "RequestsSection",
    "ResolvedConfig",
    "RetryPolicy",
    "SubscriberConfig",
    "TransportSection",
    "UnmatchedPolicy",
    "WebhooksSection",
    "merged_over",
    "parse_profile_document",
    "unit_error_from_validation",
]

_MODEL = ConfigDict(extra="forbid", frozen=True)

UnmatchedPolicy = Literal["vendor-404", "error"]
"""``vendor-404`` answers as the vendor would (``Vendorfake-Near-Miss``
header); ``error`` fails the caller. The binding chooses; the kernel always
answers 404."""

UNMATCHED_POLICIES: tuple[UnmatchedPolicy, ...] = ("vendor-404", "error")
"""The two policies, enumerable for error messages and the environment layer."""

ModelT = TypeVar("ModelT", bound=BaseModel)


def merged_over(base: ModelT, block: Mapping[str, Any]) -> ModelT:
    """``base`` with ``block`` laid over it, revalidated as ``base``'s own
    type, so an unknown key is refused and a frozen model never mutated."""
    return type(base).model_validate({**base.model_dump(), **dict(block)})


class RetryPolicy(BaseModel):
    model_config = _MODEL

    #: Delay before attempt N+1, ms. Empty: a vendor supplies its own schedule.
    schedule_ms: tuple[int, ...] = ()
    #: Multiplier on every delay, so a test suite need not wait real hours.
    time_scale: float = 1.0
    timeout_ms: int = 10_000


class SubscriberConfig(BaseModel):
    """``event_types``/``signature_key`` are required, not defaulted -- a
    subscriber with no signing key can't be verified."""

    model_config = _MODEL

    id: str | None = None
    name: str | None = None
    notification_url: str
    event_types: tuple[str, ...]
    signature_key: str
    enabled: bool = True

    @field_validator("notification_url")
    @classmethod
    def _target_the_unit_will_post_to(cls, value: str) -> str:
        return check_notification_url(value)


class WebhooksSection(BaseModel):
    model_config = _MODEL

    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    subscribers: tuple[SubscriberConfig, ...] = ()
    #: Fail fast instead of retrying -- used by conformance to stay quick.
    disable_delivery: bool = False


class ChaosSection(BaseModel):
    """Rules stay opaque here by design."""

    model_config = _MODEL

    seed: int = 1
    rules: tuple[dict[str, Any], ...] = ()
    #: Refuse a rule whose ``match.route`` matches no route, instead of a NOTE.
    strict_rules: bool = False


class ClockSection(BaseModel):
    model_config = _MODEL

    mode: Literal["real", "virtual"] = "real"
    #: RFC 3339 instant the virtual clock starts at. Ignored in real mode.
    start: str | None = None


class RequestsSection(BaseModel):
    model_config = _MODEL

    #: Records the ring buffer holds before evicting the oldest.
    capacity: int = Field(default=10_000, ge=0)


class ErrorsSection(BaseModel):
    """``sidecar`` says *where* the ``unit_error`` sidecar rides, not
    *whether*: ``"headers"`` (default) keeps a real response byte-for-byte;
    ``"body"`` is DEPRECATED v0.1; ``"both"`` emits it in both places."""

    model_config = _MODEL

    sidecar: Literal["headers", "body", "both"] = "headers"


class TransportSection(BaseModel):
    """Resolved from the environment only -- a profile describes a vendor's
    behaviour, not the socket it is served on."""

    model_config = _MODEL

    port: int = 8080
    host: str | None = None


class ProfileDocument(BaseModel):
    """Every field is optional, so ``profile.py``'s merge can say "the fields
    this document set win over the layer beneath it" via ``model_fields_set``.
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
    requests: RequestsSection = Field(default_factory=RequestsSection)
    errors: ErrorsSection = Field(default_factory=ErrorsSection)


class ResolvedWebhooks(BaseModel):
    model_config = _MODEL

    retry: RetryPolicy
    subscribers: tuple[SubscriberConfig, ...] = ()
    disable_delivery: bool = False


class ResolvedChaos(BaseModel):
    model_config = _MODEL

    seed: int
    rules: tuple[dict[str, Any], ...] = ()
    #: See :attr:`ChaosSection.strict_rules`. No environment override -- it
    #: changes whether a unit starts, so belongs in the diffable profile.
    strict_rules: bool = False


class ResolvedConfig(BaseModel):
    """What the kernel and every subsystem read. Decided once, then frozen."""

    model_config = _MODEL

    profile: str
    capabilities: tuple[str, ...] = ()
    seed_path: str | None = None
    #: ``VENDORFAKE_SEED_OVERLAY`` as given. Never published -- may carry credentials.
    seed_overlay: str | None = None
    #: ``"sha256:<hex>"`` over the applied overlay's canonical JSON.
    seed_overlay_digest: str | None = None
    #: The overlay's top-level keys, sorted -- never the values.
    seed_overlay_collections: tuple[str, ...] = ()
    vendor_config: dict[str, Any] = Field(default_factory=dict)
    webhooks: ResolvedWebhooks
    chaos: ResolvedChaos
    clock: ClockSection
    errors: ErrorsSection = Field(default_factory=ErrorsSection)
    transport: TransportSection
    requests: RequestsSection = Field(default_factory=RequestsSection)
    #: Read here, not by the logger, so no module reaches the environment on its own.
    log_level: str = "info"
    #: What ``create_unit(capabilities=[...])`` was asked for; ``None`` for a
    #: unit started by ``profile=``. Published at ``/__unit/info``.
    requested_capabilities: tuple[str, ...] | None = None


def unit_error_from_validation(exc: ValidationError, *, source: str | None = None) -> UnitError:
    """A missing field reports ``missing_field``, anything else
    ``invalid_value`` -- not the kernel's catch-all 500."""
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
