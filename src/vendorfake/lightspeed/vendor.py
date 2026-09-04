"""The Lightspeed vendor definition: assembles the object satisfying
:class:`~vendorfake.core.kernel.types.VendorDefinition` for a Lightspeed X-Series unit.

INVARIANT: one vendor instance per unit -- :data:`VENDOR` (``__init__.py``) is minted fresh
per attribute access, since a vendor owns mutable state (id streams, version counter, rate
limiter window) that two units must not share.

Configuration resolves in two phases: defaults at construction, then
:meth:`LightspeedVendor.hydrate` re-resolves config, rebuilds the error shaper, reseeds ids,
and recomputes the rate limiter's quota (``300 x registers + 50``) from the loaded scenario.

JUDGMENT: ``roles`` maps ``orders`` to ``sales`` -- a Lightspeed *sale* is this vendor's
order-equivalent resource.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from vendorfake.core.config.models import ProfileDocument
from vendorfake.core.kernel.types import (
    AuthAdapter,
    CapabilityDecl,
    ErrorShaper,
    EventMapper,
    MagicTriggerSpec,
    Route,
    Signer,
    UnitContext,
    UnitRequest,
    VendorDefinition,
)
from vendorfake.core.state.machine import MachineDef
from vendorfake.lightspeed.auth import LightspeedAuth
from vendorfake.lightspeed.capabilities import LIGHTSPEED_CAPABILITIES, LIGHTSPEED_NOT_SUPPORTED
from vendorfake.lightspeed.config import LightspeedConfig, resolve_lightspeed_config
from vendorfake.lightspeed.entities import COL
from vendorfake.lightspeed.errors import (
    RATE_LIMIT_LIMIT_HEADER,
    RATE_LIMIT_REMAINING_HEADER,
    LightspeedErrorShaper,
)
from vendorfake.lightspeed.events import LightspeedEventMapper
from vendorfake.lightspeed.ids import LightspeedCredentialIds, LightspeedIds
from vendorfake.lightspeed.machine import SALE_MACHINE, SALE_MACHINE_NAME
from vendorfake.lightspeed.ratelimit import LightspeedRateLimiter
from vendorfake.lightspeed.retry import lightspeed_retry_defaults
from vendorfake.lightspeed.seed.hydrate import hydrate_lightspeed
from vendorfake.lightspeed.signer import LightspeedWebhookSigner
from vendorfake.lightspeed.surface.auth import auth_routes
from vendorfake.lightspeed.surface.customers import customer_routes
from vendorfake.lightspeed.surface.inventory import inventory_routes
from vendorfake.lightspeed.surface.outlets import outlet_routes
from vendorfake.lightspeed.surface.payment_types import payment_type_routes
from vendorfake.lightspeed.surface.products import product_routes
from vendorfake.lightspeed.surface.registers import register_routes
from vendorfake.lightspeed.surface.retailer import retailer_routes
from vendorfake.lightspeed.surface.sales import sale_routes
from vendorfake.lightspeed.surface.webhooks import webhook_routes
from vendorfake.lightspeed.versioning import LightspeedVersions

__all__ = ["LIGHTSPEED_MAGIC", "LIGHTSPEED_ROLES", "LightspeedVendor", "create_lightspeed_vendor"]

_PACKAGE_DIR = Path(__file__).resolve().parent

API_VERSION = "2026-07"
"""The document's ``info.version``, and the path segment every resource route
sits under."""

LIGHTSPEED_MAGIC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("url",),
    query_params=("state",),
)
"""In-band fault triggering via fields a real client can set: a webhook's ``url`` and the
authorize URL's documented opaque ``state`` param. JUDGMENT: Lightspeed has no such
mechanism, so ``chaos:`` is this project's prefix, chosen so no real value would carry it."""

LIGHTSPEED_ROLES: Mapping[str, str] = {
    "auth": "auth",
    "orders": "sales",
    "webhooks": "webhooks",
    "chaos": "chaos",
}
"""Neutral role vocabulary mapped to this vendor's capability names; see the module
docstring for why ``orders`` maps to ``sales``."""

_VOLATILE_FIELDS: tuple[str, ...] = (
    "expires_at_ms",
    "created_at_ms",
    "revoked_at_ms",
    "retired_at_ms",
    "used_at_ms",
    "register_close_time",
    "activated_at",
)
"""Fields excluded from the state digest because this unit writes them from its clock,
matched at any depth. ``register_open_time`` is deliberately ABSENT (scenario data, not a
stamp); ``register_close_time`` IS here, since only a close action writes it."""

_OPAQUE_FIELDS: tuple[str, ...] = (
    "document",
    "config",
    "attributes",
)
"""Caller/seed free-form subtrees the digest takes verbatim; the volatile scrub must not
descend into them."""


class LightspeedVendor:
    """One Lightspeed vendor, for one unit. Satisfies ``VendorDefinition``."""

    __slots__ = (
        "_auth",
        "_base_config",
        "_config",
        "_credential_ids",
        "_errors",
        "_events",
        "_ids",
        "_limiter",
        "_routes",
        "_seed",
        "_signer",
        "_versions",
    )

    def __init__(self, *, config: LightspeedConfig | None = None, seed: int = 1) -> None:
        self._base_config = LightspeedConfig() if config is None else config
        self._config = self._base_config
        self._seed = seed
        self._ids = LightspeedIds(seed)
        self._credential_ids = LightspeedCredentialIds(seed)
        self._versions = LightspeedVersions()
        self._limiter = LightspeedRateLimiter(
            limit=self._config.rate_limit_quota(0), window_ms=self._config.rate_limit_window_ms
        )
        self._errors = self._build_errors()
        self._auth = LightspeedAuth(self)
        self._signer = LightspeedWebhookSigner()
        self._events = LightspeedEventMapper(self)
        self._routes: tuple[Route, ...] | None = None

    def _build_errors(self) -> LightspeedErrorShaper:
        return LightspeedErrorShaper(
            sidecar=self._config.error_sidecar,
            retry_after_header=self._config.retry_after_header,
            window_ms=self._config.rate_limit_window_ms,
        )

    # -- identity ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "lightspeed"

    @property
    def display_name(self) -> str:
        return "Lightspeed Retail X-Series (API 2026-07)"

    @property
    def api_version(self) -> str | None:
        return API_VERSION

    # -- what this vendor is made of ---------------------------------------

    @property
    def config(self) -> LightspeedConfig:
        return self._config

    @property
    def ids(self) -> LightspeedIds:
        return self._ids

    @property
    def credential_ids(self) -> LightspeedCredentialIds:
        return self._credential_ids

    @property
    def versions(self) -> LightspeedVersions:
        return self._versions

    @property
    def limiter(self) -> LightspeedRateLimiter:
        return self._limiter

    @property
    def capabilities(self) -> Sequence[CapabilityDecl]:
        return LIGHTSPEED_CAPABILITIES

    @property
    def not_supported(self) -> Mapping[str, str]:
        return LIGHTSPEED_NOT_SUPPORTED

    @property
    def roles(self) -> Mapping[str, str]:
        return LIGHTSPEED_ROLES

    @property
    def routes(self) -> Sequence[Route]:
        """The vendor surface, built once and cached; auth first, so credential-issuing
        routes lead ``GET /__unit/routes``."""
        if self._routes is None:
            self._routes = (
                auth_routes(self)
                + retailer_routes(self)
                + outlet_routes(self)
                + register_routes(self)
                + payment_type_routes(self)
                # Catalogue, then stock, then customers, then sales; see surface/sales.py.
                + product_routes(self)
                + inventory_routes(self)
                + customer_routes(self)
                + sale_routes(self)
                + webhook_routes(self)
            )
        return self._routes

    @property
    def errors(self) -> ErrorShaper:
        return self._errors

    @property
    def auth(self) -> AuthAdapter:
        return self._auth

    @property
    def signer(self) -> Signer | None:
        """``X-Signature``, delivery headers, and body form-encoding; see :mod:`.signer`."""
        return self._signer

    @property
    def events(self) -> EventMapper | None:
        """Journal entry to one of the seven documented event types; see :mod:`.events`."""
        return self._events

    @property
    def magic(self) -> MagicTriggerSpec | None:
        return LIGHTSPEED_MAGIC

    @property
    def machines(self) -> Mapping[str, MachineDef]:
        """One: the sale lifecycle. A register is deliberately not a machine --
        open/closed is a boolean, not a lifecycle worth its own vocabulary."""
        return {SALE_MACHINE_NAME: SALE_MACHINE}

    @property
    def retry_defaults(self) -> ProfileDocument:
        return lightspeed_retry_defaults()

    @property
    def volatile_fields(self) -> Sequence[str]:
        return _VOLATILE_FIELDS

    @property
    def opaque_fields(self) -> Sequence[str]:
        return _OPAQUE_FIELDS

    @property
    def profile_dir(self) -> Path:
        return _PACKAGE_DIR / "profiles"

    @property
    def base_dir(self) -> Path:
        return _PACKAGE_DIR

    # -- lifecycle ---------------------------------------------------------

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        """Phase two of configuration, then load the seed scenario, then size
        the rate limiter from what the scenario loaded."""
        self._resolve_config(ctx)
        hydrate_lightspeed(ctx, seed, self._config, self._versions)
        self._limiter.reset(limit=self._config.rate_limit_quota(ctx.store.collection(COL.registers).size))

    def _resolve_config(self, ctx: UnitContext) -> None:
        block = dict(ctx.config.vendor_config)
        self._config = self._base_config if not block else self._base_config.merged_with(block)
        self._ids.reseed(ctx.config.chaos.seed)
        self._credential_ids.reseed(ctx.config.chaos.seed)
        self._versions.reset()
        self._errors = self._build_errors()

    def decorate(self, headers: dict[str, str], ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp the vendor, the API version, and the two documented rate-limit headers --
        here, not in the handlers, since the page says they're on EVERY response."""
        headers["x-unit-vendor"] = ctx.vendor.name
        headers["x-unit-api-version"] = API_VERSION
        snapshot = self._limiter.snapshot(ctx)
        headers[RATE_LIMIT_LIMIT_HEADER] = str(snapshot.limit)
        headers[RATE_LIMIT_REMAINING_HEADER] = str(snapshot.remaining)


def create_lightspeed_vendor(*, vendor_config: dict[str, Any] | None = None, seed: int = 1) -> VendorDefinition:
    """Build a Lightspeed vendor. The return annotation is the protocol, so
    ``mypy --strict`` checks the structural conformance here."""
    return LightspeedVendor(config=resolve_lightspeed_config(vendor_config), seed=seed)
