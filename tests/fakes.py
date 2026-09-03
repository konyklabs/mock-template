"""A minimal vendor, built for tests that are about the kernel and not a vendor.

Every collaborator here is the smallest thing that satisfies its protocol *and*
records what it was asked to do, because most of what the pipeline guarantees
is an ordering, and an ordering is only observable if each step leaves a trace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vendorfake.core.config.models import ProfileDocument
from vendorfake.core.kernel.types import (
    AuthCredential,
    AuthResult,
    CapabilityDecl,
    EventMeta,
    JournalEntry,
    MagicTriggerSpec,
    MappedEvent,
    MutableResponse,
    Route,
    ShapedError,
    SignerProperties,
    SignInput,
    UnitContext,
    UnitError,
    UnitErrorKind,
    UnitRequest,
)
from vendorfake.core.util.json import sha256_hex
from vendorfake.core.webhooks.models import DeliveryMetadata

# The five statuses the twenty core kinds collapse onto for a fake vendor.
STATUS: Mapping[UnitErrorKind, int] = {
    UnitErrorKind.BAD_REQUEST: 400,
    UnitErrorKind.INVALID_JSON: 400,
    UnitErrorKind.MISSING_FIELD: 400,
    UnitErrorKind.INVALID_VALUE: 400,
    UnitErrorKind.NOT_FOUND: 404,
    UnitErrorKind.METHOD_NOT_ALLOWED: 405,
    UnitErrorKind.UNAUTHORIZED: 401,
    UnitErrorKind.TOKEN_EXPIRED: 401,
    UnitErrorKind.TOKEN_REVOKED: 401,
    UnitErrorKind.FORBIDDEN_SCOPE: 403,
    UnitErrorKind.CAPABILITY_DISABLED: 501,
    UnitErrorKind.VERSION_CONFLICT: 409,
    UnitErrorKind.IDEMPOTENCY_CONFLICT: 409,
    UnitErrorKind.INVALID_CURSOR: 400,
    UnitErrorKind.INVALID_TRANSITION: 400,
    UnitErrorKind.CONFLICT: 409,
    UnitErrorKind.RATE_LIMITED: 429,
    UnitErrorKind.TIMEOUT: 504,
    UnitErrorKind.UNAVAILABLE: 503,
    UnitErrorKind.INTERNAL: 500,
}


class FakeErrors:
    """One shaped body per kind, plus a distinguishable no-route body."""

    def shape(self, err: UnitError, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        # `describing` accepted and ignored, as by any shaper with no
        # per-request field to freeze. Note `info` is echoed verbatim here,
        # which is what makes this double useful for the leak test.
        return ShapedError(
            status=STATUS[err.kind],
            body={"error": {"code": err.kind.value, "detail": err.detail, "field": err.field, "info": err.info}},
        )

    def not_found(self, req: UnitRequest, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        # `describing` accepted and ignored, as it is by any shaper whose
        # envelope carries no per-request id or timestamp to freeze.
        return ShapedError(status=404, body={"error": {"code": "no_route", "path": req.path}})

    def describe(self) -> dict[str, dict[str, Any]]:
        return {kind.value: {"status": status, "provenance": "judgment"} for kind, status in STATUS.items()}


@dataclass
class FakeAuth:
    """Resolves to a fixed principal, or raises, and counts both."""

    scopes: tuple[str, ...] = ("orders.read",)
    raises: UnitError | None = None
    calls: list[str] = field(default_factory=list)

    def describe(self) -> Mapping[str, str]:
        return {"scheme": "test"}

    def resolve(self, args: object, mode: str) -> AuthResult:
        self.calls.append(mode)
        if self.raises is not None:
            raise self.raises
        return AuthResult(principal_id="prn_1", scopes=self.scopes)

    def credentials(self, ctx: object) -> tuple[AuthCredential, ...]:
        """One credential per declared scope subset the fake knows about.

        A fake vendor still has to answer ``GET /__unit/auth`` -- the route is
        the core's, not a vendor's -- and answering with an empty tuple in
        every kernel test would leave the projection untested.
        """
        return (
            AuthCredential(
                label="test-credential",
                mode="test",
                headers={"authorization": "Test prn_1"},
                scopes=self.scopes,
                summary="The fake's only credential.",
            ),
        )


def route(
    method: str,
    path: str,
    handler: Callable[..., object],
    *,
    capability: str = "orders",
    **kwargs: object,
) -> Route:
    """A ``Route`` with the fields a kernel test cares about and no others."""
    return Route(method=method, path=path, capability=capability, handler=handler, **kwargs)  # type: ignore[arg-type]


def capability(name: str, *, kind: str = "surface", requires: Sequence[str] = ()) -> CapabilityDecl:
    return CapabilityDecl(name=name, summary=f"{name} (test)", kind=kind, requires=tuple(requires))  # type: ignore[arg-type]


#: The three core-gated capabilities a vendor must account for, accounted for
#: in the cheapest way: chaos on, delivery declared as out of scope with a
#: reason. Tests that need webhooks declared say so themselves.
DEFAULT_CAPABILITIES: tuple[CapabilityDecl, ...] = (
    capability("orders"),
    capability("chaos", kind="behavior"),
)
DEFAULT_NOT_SUPPORTED: Mapping[str, str] = {
    "webhooks": "this fake has no delivery surface",
    "webhooks.chaos": "no delivery surface to disturb",
}


@dataclass
class FakeVendor:
    """A ``VendorDefinition`` with every hook observable."""

    routes: tuple[Route, ...] = ()
    capabilities: tuple[CapabilityDecl, ...] = DEFAULT_CAPABILITIES
    not_supported: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_NOT_SUPPORTED))
    roles: Mapping[str, str] = field(default_factory=dict)
    auth: FakeAuth = field(default_factory=FakeAuth)
    errors: FakeErrors = field(default_factory=FakeErrors)
    magic: MagicTriggerSpec | None = None
    machines: Mapping[str, object] = field(default_factory=dict)
    retry_defaults: ProfileDocument = field(default_factory=ProfileDocument)
    profile_dir: Path = Path("/nonexistent/profiles")
    base_dir: Path = Path("/nonexistent")
    volatile_fields: tuple[str, ...] = ()
    opaque_fields: tuple[str, ...] = ()
    signer: object | None = None
    events: object | None = None
    name: str = "acme"
    display_name: str = "Acme"
    api_version: str | None = "2024-01-01"
    decorated: list[str] = field(default_factory=list)
    hydrated: int = 0

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        self.hydrated += 1

    def decorate(self, res: MutableResponse, ctx: UnitContext, req: UnitRequest) -> None:
        self.decorated.append(f"{req.method} {req.path}")
        res.headers["acme-version"] = self.api_version or ""


class VendorWithoutRoles:
    """A v0.1.0-vintage third-party vendor: every ``VendorDefinition`` member
    except ``roles``, which did not exist when it was written.

    ``VendorDefinition.roles`` arrived in 0.2 as a *required* protocol member,
    so a vendor registered through the ``vendorfake.vendors`` entry-point group
    and built against 0.1.0 has no such attribute. This stands in for one, to
    pin that the two runtime read sites -- ``registry._translate_capability_names``
    and the control plane's ``info`` handler -- fail legibly rather than with an
    ``AttributeError`` from somewhere the caller cannot connect to anything.

    Delegation rather than a subclass with the field deleted, because a
    dataclass field cannot be un-declared and ``del`` on the instance would
    still leave the class attribute in place; ``__getattr__`` only fires for
    what is genuinely absent, which is exactly the shape being modelled.
    """

    def __init__(self, inner: FakeVendor) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name == "roles":
            raise AttributeError(name)
        return getattr(self._inner, name)


def make_config(
    *,
    profile: str = "test",
    capabilities: Sequence[str] = ("orders", "chaos"),
    chaos_rules: Sequence[Mapping[str, object]] = (),
    chaos_seed: int = 1,
    chaos_strict_rules: bool = False,
    clock_mode: str = "real",
    clock_start: str | None = None,
    error_sidecar: str = "headers",
    log_level: str = "error",
    schedule_ms: Sequence[int] = (),
    time_scale: float = 1.0,
    timeout_ms: int = 10_000,
    subscribers: Sequence[Mapping[str, object]] = (),
    disable_delivery: bool = False,
    request_log_capacity: int | None = None,
    unmatched: str | None = None,
) -> object:
    """A ``ResolvedConfig`` for a kernel test, with the knobs those tests move."""
    from vendorfake.core.config.models import (
        ClockSection,
        ErrorsSection,
        RequestsSection,
        ResolvedChaos,
        ResolvedConfig,
        ResolvedWebhooks,
        RetryPolicy,
        SubscriberConfig,
        TransportSection,
        UnmatchedSection,
    )

    return ResolvedConfig(
        profile=profile,
        capabilities=tuple(capabilities),
        webhooks=ResolvedWebhooks(
            retry=RetryPolicy(schedule_ms=tuple(schedule_ms), time_scale=time_scale, timeout_ms=timeout_ms),
            subscribers=tuple(SubscriberConfig(**dict(s)) for s in subscribers),  # type: ignore[arg-type]
            disable_delivery=disable_delivery,
        ),
        chaos=ResolvedChaos(
            seed=chaos_seed, rules=tuple(dict(r) for r in chaos_rules), strict_rules=chaos_strict_rules
        ),
        clock=ClockSection(mode=clock_mode, start=clock_start),  # type: ignore[arg-type]
        errors=ErrorsSection(sidecar=error_sidecar),  # type: ignore[arg-type]
        transport=TransportSection(),
        requests=RequestsSection() if request_log_capacity is None else RequestsSection(capacity=request_log_capacity),
        unmatched=UnmatchedSection(policy=unmatched),  # type: ignore[arg-type]
        log_level=log_level,
    )


def make_unit(
    routes: Sequence[Route] = (),
    *,
    vendor: FakeVendor | None = None,
    control_routes: object = None,
    sink: object = None,
    **config_kwargs: object,
) -> object:
    """A started :class:`Unit` over a fake vendor. Returns the unit."""
    from vendorfake.core.kernel.unit import Unit

    definition = vendor if vendor is not None else FakeVendor()
    definition.routes = tuple(routes) if routes else definition.routes
    unit = Unit(
        vendor=definition,  # type: ignore[arg-type]
        config=make_config(**config_kwargs),  # type: ignore[arg-type]
        sink=sink,  # type: ignore[arg-type]
        control_routes=control_routes,  # type: ignore[arg-type]
    )
    unit.start()
    return unit


# ---------------------------------------------------------------------------
# The webhook half of a vendor: a signer with both hooks, and an event mapper.
# ---------------------------------------------------------------------------


def _default_signature(payload: SignInput) -> dict[str, str]:
    """A signature bound to the url, the body and the secret -- and to nothing
    else, so a test can vary one input at a time and see the header move."""
    material = payload.notification_url.encode() + payload.raw_body + payload.secret.encode()
    return {"x-fake-signature": sha256_hex(material)}


def _default_delivery_headers(meta: DeliveryMetadata) -> dict[str, str]:
    """What a vendor would put on the wire, in a vendor's own spelling.

    Deliberately prefixed ``acme-``: the point of the hook is that the core
    never learns these names, and a test asserting they came from here rather
    than from the core needs them to be recognisably not-core.
    """
    headers = {"content-type": "application/json", "acme-initial-delivery": meta.initial_delivery_at}
    if meta.is_retry:
        headers["acme-retry-number"] = str(meta.retry_number)
        if meta.retry_reason is not None:
            headers["acme-retry-reason"] = _ACME_RETRY_REASONS[meta.retry_reason]
    return headers


#: The vendor-owned map from the core's neutral outcome to this vendor's wire
#: strings. The whole point of ``DeliveryOutcome``: the core never sees these.
_ACME_RETRY_REASONS: Mapping[str, str] = {
    "timeout": "acme_timed_out",
    "transport_error": "acme_no_answer",
    "http_error": "acme_bad_status",
}


@dataclass
class FakeSigner:
    """A ``Signer`` whose two hooks are both observable and both replaceable."""

    sign_with: Callable[[SignInput], Mapping[str, str]] = _default_signature
    headers_with: Callable[[DeliveryMetadata], Mapping[str, str]] = _default_delivery_headers
    properties: SignerProperties = field(default_factory=SignerProperties)
    sign_calls: list[SignInput] = field(default_factory=list)
    header_calls: list[DeliveryMetadata] = field(default_factory=list)

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        self.sign_calls.append(payload)
        return self.sign_with(payload)

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        self.header_calls.append(meta)
        return self.headers_with(meta)

    def describe(self) -> Mapping[str, str]:
        return {"scheme": "fake-hmac"}


#: A signer that contributes nothing at all. Used to prove the negative: with
#: this installed, every header on the wire would have to have come from the
#: core, so an empty header mapping is the whole assertion.
SILENT_SIGNER_HOOKS: tuple[
    Callable[[SignInput], Mapping[str, str]], Callable[[DeliveryMetadata], Mapping[str, str]]
] = (
    lambda payload: {},
    lambda meta: {},
)


def order_event(entry: JournalEntry) -> Sequence[MappedEvent]:
    """One event per mutation of the ``orders`` collection, and nothing else.

    ``order.created`` / ``order.updated`` / ``order.deleted``, matching the
    shape a real vendor uses, so that the event-type matching tests exercise
    globs rather than single words.
    """
    if entry.collection != "orders":
        return ()
    name = {"insert": "created", "update": "updated", "delete": "deleted"}[entry.op]
    event_type = f"order.{name}"

    def build(meta: EventMeta) -> object:
        return {
            "merchant_id": "MERCHANT",
            "type": event_type,
            "event_id": meta.event_id,
            "created_at": meta.created_at,
            "data": {"type": "order", "id": entry.id, "object": {"version": entry.to_version}},
        }

    return (MappedEvent(type=event_type, entity_id=entry.id, build=build),)


@dataclass
class FakeEvents:
    """An ``EventMapper`` delegating to a plain function, so a test can swap it."""

    mapper: Callable[[JournalEntry], Sequence[MappedEvent]] = order_event
    calls: list[JournalEntry] = field(default_factory=list)

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]:
        self.calls.append(entry)
        return self.mapper(entry)


#: A vendor that declares delivery and its faults, rather than excusing itself
#: from them the way :data:`DEFAULT_CAPABILITIES` does.
WEBHOOK_CAPABILITIES: tuple[CapabilityDecl, ...] = (
    capability("orders"),
    capability("chaos", kind="behavior"),
    capability("webhooks"),
    capability("webhooks.chaos", kind="behavior", requires=("webhooks", "chaos")),
)
