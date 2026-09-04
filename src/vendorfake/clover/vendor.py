"""The Clover vendor definition: assembles one object satisfying
:class:`~vendorfake.core.kernel.types.VendorDefinition`.

INVARIANT: one vendor instance per unit -- it owns a stateful id stream, and
a shared instance would interleave two units' streams so neither run would
reproduce. Configuration resolves in two phases: defaults at construction,
then :meth:`CloverVendor.hydrate` re-resolves from
``ctx.config.vendor_config`` and re-seeds the id stream.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from vendorfake.clover.auth import CloverAuth
from vendorfake.clover.capabilities import CLOVER_CAPABILITIES, CLOVER_NOT_SUPPORTED
from vendorfake.clover.config import CloverConfig, resolve_clover_config
from vendorfake.clover.errors import CloverErrorShaper
from vendorfake.clover.events import CloverEventMapper
from vendorfake.clover.ids import CloverIds
from vendorfake.clover.machine import ORDER_MACHINE, ORDER_MACHINE_NAME
from vendorfake.clover.retry import clover_retry_defaults
from vendorfake.clover.seed.hydrate import hydrate_clover
from vendorfake.clover.signer import CloverWebhookSigner
from vendorfake.clover.surface.customers import customer_routes
from vendorfake.clover.surface.inventory import inventory_routes
from vendorfake.clover.surface.merchant import merchant_routes
from vendorfake.clover.surface.oauth import oauth_routes
from vendorfake.clover.surface.orders import order_routes
from vendorfake.clover.surface.payments import payment_routes
from vendorfake.clover.surface.webhooks import webhook_routes
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

__all__ = ["CLOVER_MAGIC", "CLOVER_ROLES", "CloverVendor", "create_clover_vendor"]

_PACKAGE_DIR = Path(__file__).resolve().parent

CLOVER_MAGIC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("note", "title", "externalReferenceId"),
    query_params=("state",),
)
"""In-band fault triggering via order fields a real Clover client can set
(``note``, ``title``, ``externalReferenceId``;
https://docs.clover.com/dev/docs/creating-custom-orders). JUDGMENT: Clover
publishes no such mechanism; the ``chaos:`` prefix is this project's own."""

CLOVER_ROLES: Mapping[str, str] = {
    "auth": "oauth",
    "orders": "orders",
    "webhooks": "webhooks",
    "chaos": "chaos",
}
"""The neutral role vocabulary, mapped to Clover's capability names."""

_VOLATILE_FIELDS: tuple[str, ...] = (
    "access_token_expiration_ms",
    "refresh_token_expiration_ms",
    "expires_at_ms",
    "used_at_ms",
    "refresh_used_at_ms",
    "createdTime",
    "modifiedTime",
    "clientCreatedTime",
    "deletedTime",
)
"""Fields excluded from the state digest because they carry wall-clock time
-- two units seeded identically a second apart must still agree."""


class CloverVendor:
    """One Clover vendor, for one unit. Satisfies ``VendorDefinition``."""

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

    def __init__(self, *, config: CloverConfig | None = None, seed: int = 1) -> None:
        self._base_config = CloverConfig() if config is None else config
        self._config = self._base_config
        self._seed = seed
        self._ids = CloverIds(seed)
        self._errors = self._build_errors()
        # Holds *this vendor*, not a copy of its config, so a profile resolved
        # in `hydrate` (after construction) is in force on the next request.
        self._auth = CloverAuth(self)
        self._signer = CloverWebhookSigner()
        self._events = CloverEventMapper(self)
        self._routes: tuple[Route, ...] | None = None

    def _build_errors(self) -> CloverErrorShaper:
        return CloverErrorShaper(
            sidecar=self._config.error_sidecar,
            retry_after_header=self._config.retry_after_header,
        )

    # -- identity ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "clover"

    @property
    def display_name(self) -> str:
        return "Clover (REST v3)"

    @property
    def api_version(self) -> str | None:
        """``None``: Clover has no version header to report."""
        return None

    # -- what this vendor is made of ---------------------------------------

    @property
    def config(self) -> CloverConfig:
        """The resolved configuration. Not part of the protocol; surfaces
        read it directly."""
        return self._config

    @property
    def ids(self) -> CloverIds:
        """This unit's id stream."""
        return self._ids

    @property
    def capabilities(self) -> Sequence[CapabilityDecl]:
        return CLOVER_CAPABILITIES

    @property
    def not_supported(self) -> Mapping[str, str]:
        return CLOVER_NOT_SUPPORTED

    @property
    def roles(self) -> Mapping[str, str]:
        return CLOVER_ROLES

    @property
    def routes(self) -> Sequence[Route]:
        """The vendor surface, built once and cached (``Route`` handlers are
        bound methods; rebuilding them would make two reads compare unequal).
        Webhooks are last so ``/oauth`` and ``/v3`` match first.
        """
        if self._routes is None:
            self._routes = (
                oauth_routes(self)
                + order_routes(self)
                + payment_routes(self)
                + inventory_routes(self)
                + merchant_routes(self)
                + customer_routes(self)
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
        """The static ``X-Clover-Auth`` scheme. See :mod:`.signer`."""
        return self._signer

    @property
    def events(self) -> EventMapper | None:
        """Journal entry to the documented aggregate payload. See
        :mod:`.events`."""
        return self._events

    @property
    def magic(self) -> MagicTriggerSpec | None:
        return CLOVER_MAGIC

    @property
    def machines(self) -> Mapping[str, MachineDef]:
        """The order lifecycle, reachable at ``GET /__unit/machines``."""
        return {ORDER_MACHINE_NAME: ORDER_MACHINE}

    @property
    def retry_defaults(self) -> ProfileDocument:
        return clover_retry_defaults()

    @property
    def volatile_fields(self) -> Sequence[str]:
        return _VOLATILE_FIELDS

    @property
    def opaque_fields(self) -> Sequence[str]:
        """Empty: this surface stores no caller free-form documents."""
        return ()

    @property
    def profile_dir(self) -> Path:
        return _PACKAGE_DIR / "profiles"

    @property
    def base_dir(self) -> Path:
        """What a profile's relative ``seed`` path resolves against."""
        return _PACKAGE_DIR

    # -- lifecycle ---------------------------------------------------------

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        """Phase two of configuration, then load the seed scenario.

        Config resolves first and unconditionally, so seeded tokens are
        stamped with the profile's TTL rather than the built-in default's.
        """
        self._resolve_config(ctx)
        hydrate_clover(ctx, seed, self._config)

    def _resolve_config(self, ctx: UnitContext) -> None:
        """Re-resolve from the profile and rebuild what depends on it.

        The id stream is re-seeded, not continued, so
        ``POST /__unit/state/reset`` reproduces a scenario's ids.
        """
        block = dict(ctx.config.vendor_config)
        self._config = self._base_config if not block else self._base_config.merged_with(block)
        self._errors = self._build_errors()
        self._ids.reseed(ctx.config.chaos.seed)

    def decorate(self, headers: dict[str, str], ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp only ``x-unit-vendor``: Clover has no version header."""
        headers["x-unit-vendor"] = ctx.vendor.name


def create_clover_vendor(
    *,
    vendor_config: dict[str, Any] | None = None,
    seed: int = 1,
) -> VendorDefinition:
    """Build a Clover vendor. ``vendor_config`` is the base a profile's
    ``vendor`` block merges over; ``seed`` seeds the id stream until
    :meth:`CloverVendor.hydrate` re-seeds it. Both exist for tests and manual
    assembly -- ``create_unit(vendor="clover")`` needs neither.
    """
    return CloverVendor(config=resolve_clover_config(vendor_config), seed=seed)
