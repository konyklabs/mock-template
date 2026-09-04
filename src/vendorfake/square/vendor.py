"""The Square vendor definition: everything the core needs to become a Square unit, assembled as one
object satisfying :class:`~vendorfake.core.kernel.types.VendorDefinition`.

INVARIANT -- one instance per unit; configuration resolves in two phases (defaults at construction, then
a profile re-resolve in :meth:`SquareVendor.hydrate`), and :attr:`SquareVendor.routes` builds its surfaces
once, each holding this vendor so a later config change is in force on the next request.
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
from vendorfake.square.auth import SquareAuth
from vendorfake.square.capabilities import SQUARE_CAPABILITIES, SQUARE_NOT_SUPPORTED
from vendorfake.square.config import (
    WEBHOOK_SUBSCRIPTIONS_SCOPE,
    SquareConfig,
    resolve_square_config,
)
from vendorfake.square.errors import SquareErrorShaper
from vendorfake.square.events import SquareEventMapper
from vendorfake.square.ids import SquareIds
from vendorfake.square.machine import (
    FULFILLMENT_MACHINE,
    FULFILLMENT_MACHINE_NAME,
    ORDER_MACHINE,
    ORDER_MACHINE_NAME,
    PAYMENT_MACHINE,
    PAYMENT_MACHINE_NAME,
)
from vendorfake.square.retry import square_retry_defaults
from vendorfake.square.seed.hydrate import hydrate_square
from vendorfake.square.signer import SquareWebhookSigner
from vendorfake.square.surface.catalog import catalog_routes
from vendorfake.square.surface.directory import directory_routes
from vendorfake.square.surface.inventory import inventory_routes
from vendorfake.square.surface.loyalty import loyalty_routes
from vendorfake.square.surface.oauth import oauth_routes
from vendorfake.square.surface.orders import FULFILLMENT_STAMPS, order_routes
from vendorfake.square.surface.payments import payment_routes
from vendorfake.square.surface.webhooks import webhook_routes

__all__ = ["SQUARE_MAGIC", "SQUARE_SCOPES", "SquareVendor", "create_square_vendor"]

_PACKAGE_DIR = Path(__file__).resolve().parent

SQUARE_SCOPES: tuple[str, ...] = (
    "MERCHANT_PROFILE_READ",
    "ORDERS_READ",
    "ORDERS_WRITE",
    "ITEMS_READ",
    "ITEMS_WRITE",
    "PAYMENTS_READ",
    "PAYMENTS_WRITE",
    "LOYALTY_READ",
    "LOYALTY_WRITE",
    "INVENTORY_READ",
    "INVENTORY_WRITE",
    WEBHOOK_SUBSCRIPTIONS_SCOPE,
)
"""DOCUMENTED -- all but the last are Square's published OAuth permissions; the last is not, see
:data:`~vendorfake.square.config.WEBHOOK_SUBSCRIPTIONS_SCOPE`.
https://developer.squareup.com/docs/oauth-api/square-permissions"""

SQUARE_MAGIC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("order.reference_id", "idempotency_key", "subscription.name"),
    query_params=("state",),
)
"""In-band fault triggering, in fields a consumer can set through an SDK. DOCUMENTED prior art --
Square's sandbox uses magic values in ordinary fields the same way.
https://developer.squareup.com/docs/devtools/sandbox/testing"""

SQUARE_ROLES: Mapping[str, str] = {
    "auth": "oauth",
    "orders": "order-lifecycle",
    "webhooks": "webhooks",
    "chaos": "chaos",
}
"""The neutral role vocabulary mapped to Square's own capability names. See ``VendorDefinition.roles``."""

_VOLATILE_FIELDS: tuple[str, ...] = (
    "expires_at",
    "refresh_token_expires_at",
    "closed_at",
    "used_at",
    "revoked_at",
    "superseded_at",
    # Stamped from the clock by a mutation.
    "catalog_version",
    "calculated_at",
    "enrolled_at",
    "mapping_created_at",
    # Fulfillment-details stamps; the digest matches names at any depth, covering `tenders[].created_at`.
    *sorted(FULFILLMENT_STAMPS),
)
"""Entity fields excluded from the state digest because this unit writes them from its clock. INVARIANT
-- a caller-supplied value under one of these names is mirrored into ``supplied_stamps`` so the digest
still hashes it as state."""

_OPAQUE_FIELDS: tuple[str, ...] = (
    # DOCUMENTED free-form caller data: https://developer.squareup.com/docs/build-basics/general-considerations/metadata
    "metadata",
    "curbside_pickup_details",
)
"""Caller free-form subtrees the state digest takes verbatim, winning over :data:`_VOLATILE_FIELDS`."""


class SquareVendor:
    """One Square vendor, for one unit. Satisfies ``VendorDefinition``."""

    __slots__ = (
        "_auth",
        "_base_config",
        "_config",
        "_errors",
        "_events",
        "_ids",
        "_routes",
        "_seed",
        "_signer",
    )

    def __init__(self, *, config: SquareConfig | None = None, seed: int = 1) -> None:
        self._base_config = SquareConfig() if config is None else config
        self._config = self._base_config
        self._seed = seed
        self._ids = SquareIds(seed)
        self._errors = self._build_errors()
        self._auth = SquareAuth(self, SQUARE_SCOPES)
        # Both hold *this vendor*, so a profile resolved in `hydrate` is in force on the next delivery.
        self._signer = SquareWebhookSigner(self)
        self._events = SquareEventMapper(self)
        self._routes: tuple[Route, ...] | None = None

    def _build_errors(self) -> SquareErrorShaper:
        return SquareErrorShaper(
            sidecar=self._config.error_sidecar,
            retry_after_header=self._config.retry_after_header,
        )

    # -- identity ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "square"

    @property
    def display_name(self) -> str:
        return "Square (Connect v2)"

    @property
    def api_version(self) -> str | None:
        return self._config.api_version

    # -- what this vendor is made of ---------------------------------------

    @property
    def config(self) -> SquareConfig:
        """The resolved configuration. Not part of the protocol; surfaces read it."""
        return self._config

    @property
    def ids(self) -> SquareIds:
        """This unit's id stream."""
        return self._ids

    @property
    def capabilities(self) -> Sequence[CapabilityDecl]:
        return SQUARE_CAPABILITIES

    @property
    def not_supported(self) -> Mapping[str, str]:
        return SQUARE_NOT_SUPPORTED

    @property
    def roles(self) -> Mapping[str, str]:
        return SQUARE_ROLES

    @property
    def routes(self) -> Sequence[Route]:
        """Built once and cached: ``Route`` handlers are bound methods, so rebuilding would make two
        reads compare unequal."""
        if self._routes is None:
            self._routes = (
                oauth_routes(self)
                + order_routes(self)
                + directory_routes()
                + catalog_routes(self)
                + inventory_routes(self)
                + payment_routes(self)
                + loyalty_routes(self)
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
        """Square's HMAC scheme and every header a delivery carries, one object for both, since a
        delivery signed but missing its retry counter would be silent at the sink."""
        return self._signer

    @property
    def events(self) -> EventMapper | None:
        """Journal entry to Square notification. See :mod:`.events`."""
        return self._events

    @property
    def magic(self) -> MagicTriggerSpec | None:
        return SQUARE_MAGIC

    @property
    def machines(self) -> Mapping[str, MachineDef]:
        """The order, fulfillment and payment lifecycles, at ``GET /__unit/machines``."""
        return {
            ORDER_MACHINE_NAME: ORDER_MACHINE,
            FULFILLMENT_MACHINE_NAME: FULFILLMENT_MACHINE,
            PAYMENT_MACHINE_NAME: PAYMENT_MACHINE,
        }

    @property
    def retry_defaults(self) -> ProfileDocument:
        return square_retry_defaults()

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
        """What a profile's relative ``seed`` path resolves against."""
        return _PACKAGE_DIR

    # -- lifecycle ---------------------------------------------------------

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        """Phase two of configuration, then load the seed: config resolves first so seeded tokens
        get the profile's TTL, not the default's."""
        self._resolve_config(ctx)
        hydrate_square(ctx, seed, self._config)

    def _resolve_config(self, ctx: UnitContext) -> None:
        """Re-resolve from the profile, then rebuild what depends on it. The id stream is re-seeded,
        not continued, so a reset reproduces a scenario's ids."""
        block = dict(ctx.config.vendor_config)
        self._config = self._base_config if not block else self._base_config.merged_with(block)
        self._errors = self._build_errors()
        self._ids.reseed(ctx.config.chaos.seed)

    def decorate(self, headers: dict[str, str], ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp the API version on every response. DOCUMENTED -- always present regardless of the
        request. JUDGMENT / NOT VERIFIED -- an unsupported requested value is echoed unchanged.
        https://developer.squareup.com/docs/build-basics/versioning-overview"""
        requested = req.headers.get("square-version")
        headers["square-version"] = self._config.api_version if requested is None else requested
        headers["x-unit-vendor"] = ctx.vendor.name


def create_square_vendor(
    *,
    vendor_config: dict[str, Any] | None = None,
    seed: int = 1,
) -> VendorDefinition:
    """Build a Square vendor. ``vendor_config`` is the base a profile's ``vendor`` block merges over;
    ``seed`` seeds the id stream until :meth:`SquareVendor.hydrate` re-seeds it."""
    return SquareVendor(config=resolve_square_config(vendor_config), seed=seed)
