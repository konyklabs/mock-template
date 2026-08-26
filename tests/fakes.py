"""A minimal vendor, built for tests that are about the kernel and not a vendor.

Every collaborator here is the smallest thing that satisfies its protocol *and*
records what it was asked to do, because most of what the pipeline guarantees
is an ordering, and an ordering is only observable if each step leaves a trace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from vendorfake.core.config.models import ProfileDocument
from vendorfake.core.kernel.types import (
    AuthResult,
    CapabilityDecl,
    MagicTriggerSpec,
    MutableResponse,
    Route,
    ShapedError,
    UnitContext,
    UnitError,
    UnitErrorKind,
    UnitRequest,
)

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

    def shape(self, err: UnitError, ctx: UnitContext) -> ShapedError:
        return ShapedError(
            status=STATUS[err.kind],
            body={"error": {"code": err.kind.value, "detail": err.detail, "field": err.field, "info": err.info}},
        )

    def not_found(self, req: UnitRequest, ctx: UnitContext) -> ShapedError:
        return ShapedError(status=404, body={"error": {"code": "no_route", "path": req.path}})


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
    auth: FakeAuth = field(default_factory=FakeAuth)
    errors: FakeErrors = field(default_factory=FakeErrors)
    magic: MagicTriggerSpec | None = None
    machines: Mapping[str, object] = field(default_factory=dict)
    retry_defaults: ProfileDocument = field(default_factory=ProfileDocument)
    profile_dir: Path = Path("/nonexistent/profiles")
    base_dir: Path = Path("/nonexistent")
    volatile_fields: tuple[str, ...] = ()
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


def make_config(
    *,
    profile: str = "test",
    capabilities: Sequence[str] = ("orders", "chaos"),
    chaos_rules: Sequence[Mapping[str, object]] = (),
    chaos_seed: int = 1,
    clock_mode: str = "real",
    clock_start: str | None = None,
    log_level: str = "error",
    schedule_ms: Sequence[int] = (),
) -> object:
    """A ``ResolvedConfig`` for a kernel test, with the knobs those tests move."""
    from vendorfake.core.config.models import (
        ClockSection,
        ResolvedChaos,
        ResolvedConfig,
        ResolvedWebhooks,
        RetryPolicy,
        TransportSection,
    )

    return ResolvedConfig(
        profile=profile,
        capabilities=tuple(capabilities),
        webhooks=ResolvedWebhooks(retry=RetryPolicy(schedule_ms=tuple(schedule_ms))),
        chaos=ResolvedChaos(seed=chaos_seed, rules=tuple(dict(r) for r in chaos_rules)),
        clock=ClockSection(mode=clock_mode, start=clock_start),  # type: ignore[arg-type]
        transport=TransportSection(),
        log_level=log_level,
    )


def make_unit(
    routes: Sequence[Route] = (),
    *,
    vendor: FakeVendor | None = None,
    control_routes: object = None,
    **config_kwargs: object,
) -> object:
    """A started :class:`Unit` over a fake vendor. Returns the unit."""
    from vendorfake.core.kernel.unit import Unit

    definition = vendor if vendor is not None else FakeVendor()
    definition.routes = tuple(routes) if routes else definition.routes
    unit = Unit(
        vendor=definition,  # type: ignore[arg-type]
        config=make_config(**config_kwargs),  # type: ignore[arg-type]
        control_routes=control_routes,  # type: ignore[arg-type]
    )
    unit.start()
    return unit
