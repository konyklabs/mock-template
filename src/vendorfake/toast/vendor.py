"""The Toast vendor definition -- everything the core needs to become a Toast
unit, and nothing else.

Assembles one object satisfying
:class:`~vendorfake.core.kernel.types.VendorDefinition`, plugging into the
same core the Square and Clover vendors do.

INVARIANT: one vendor instance per unit -- :data:`VENDOR` is minted fresh on
every attribute access, since a vendor owns two stateful id streams (entity
guids and error ``requestId``s) that two units must not interleave.

Configuration resolves in two phases: defaults at construction, then
:meth:`ToastVendor.hydrate` re-resolves at start and on every
``POST /__unit/state/reset``, rebuilding the error shaper and reseeding both
id streams. ``api_version`` is ``None``: Toast's version lives in the path
(``/orders/v2``), not a header.

``signer`` and ``events`` are separate properties, since they answer separate
questions -- what a mutation means on the wire, and how a delivery is proven
to have come from here -- both required before the dispatcher will deliver.
The subscription stand-in is last in the route table so every real Toast
path matches first.
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
from vendorfake.toast.auth import ToastAuth
from vendorfake.toast.capabilities import TOAST_CAPABILITIES, TOAST_NOT_SUPPORTED
from vendorfake.toast.config import ToastConfig, resolve_toast_config
from vendorfake.toast.errors import ToastErrorShaper
from vendorfake.toast.events import ToastEventMapper
from vendorfake.toast.ids import ToastIds, ToastRequestIds
from vendorfake.toast.machine import (
    CHECK_MACHINE,
    CHECK_MACHINE_NAME,
    GUEST_ORDER_MACHINE,
    GUEST_ORDER_MACHINE_NAME,
)
from vendorfake.toast.retry import toast_retry_defaults
from vendorfake.toast.seed.hydrate import hydrate_toast
from vendorfake.toast.signer import ToastWebhookSigner
from vendorfake.toast.surface.auth import auth_routes
from vendorfake.toast.surface.config import config_routes
from vendorfake.toast.surface.menus import menu_routes
from vendorfake.toast.surface.orders import order_routes
from vendorfake.toast.surface.partners import partner_routes
from vendorfake.toast.surface.payments import payment_routes
from vendorfake.toast.surface.restaurants import restaurant_routes
from vendorfake.toast.surface.stock import stock_routes
from vendorfake.toast.surface.webhooks import webhook_routes

__all__ = ["TOAST_MAGIC", "TOAST_ROLES", "ToastVendor", "create_toast_vendor"]

_PACKAGE_DIR = Path(__file__).resolve().parent

TOAST_MAGIC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("externalId", "deliveryInfo.notes"),
    query_params=("pageToken",),
)
"""In-band fault triggering, in fields a real Toast client can write: an
order's ``externalId`` and ``deliveryInfo.notes`` (toast-orders-api.yaml),
and the config API's ``pageToken``. JUDGMENT: Toast publishes no equivalent
mechanism; the ``chaos:`` prefix flags a value no real client would send."""

TOAST_ROLES: Mapping[str, str] = {
    "auth": "auth",
    "orders": "orders",
    "webhooks": "webhooks",
    "chaos": "chaos",
}
"""The neutral role vocabulary, mapped to Toast's own capability names --
the identity map here, stated explicitly since the mapping is part of every
vendor's contract, not an accident of naming. See ``VendorDefinition.roles``."""

_VOLATILE_FIELDS: tuple[str, ...] = (
    "expires_at_ms",
    "access_token",
    "createdDate",
    "openedDate",
    "modifiedDate",
    "paidDate",
    "closedDate",
    "voidDate",
    "deletedDate",
    "promisedDate",
    "estimatedFulfillmentDate",
    "businessDate",
    "paidBusinessDate",
    "voidBusinessDate",
    "lastModified",
    "lastUpdated",
    "publishedDate",
)
"""Entity fields excluded from the state digest for carrying wall-clock time,
matched at ANY depth (konyklabs/roadmap#35) so nested checks/selections are
scrubbed too. ``access_token`` is here because a minted JWT's ``iat``/``exp``
differ per run; each field's *presence* is kept, so a void that only stamps
``voidDate`` still moves the digest."""

_OPAQUE_FIELDS: tuple[str, ...] = (
    "curbsidePickupInfo",
    "appliedPackagingInfo",
    "marketplaceFacilitatorTaxInfo",
    "thirdPartyProviderInfo",
    "location",
    "urls",
    "schedules",
    "delivery",
    "onlineOrdering",
    "prepTimes",
    "pricingRules",
    "availability",
    "contentAdvisories",
)
"""Caller/seed free-form subtrees the digest takes verbatim
(``TOAST_NOT_MODELED``), so a caller's ``modifiedDate`` inside one of these
is state, not a stamp."""


class ToastVendor:
    """One Toast vendor, for one unit. Satisfies ``VendorDefinition``."""

    __slots__ = (
        "_auth",
        "_base_config",
        "_config",
        "_errors",
        "_events",
        "_ids",
        "_request_ids",
        "_routes",
        "_seed",
        "_signer",
    )

    def __init__(self, *, config: ToastConfig | None = None, seed: int = 1) -> None:
        self._base_config = ToastConfig() if config is None else config
        self._config = self._base_config
        self._seed = seed
        self._ids = ToastIds(seed)
        self._request_ids = ToastRequestIds(seed)
        self._errors = self._build_errors()
        self._auth = ToastAuth(self)
        self._signer = ToastWebhookSigner()
        self._events = ToastEventMapper(self)
        self._routes: tuple[Route, ...] | None = None

    def _build_errors(self) -> ToastErrorShaper:
        return ToastErrorShaper(
            request_ids=self._request_ids,
            sidecar=self._config.error_sidecar,
            retry_after_header=self._config.retry_after_header,
        )

    # -- identity ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "toast"

    @property
    def display_name(self) -> str:
        return "Toast (REST v2/v3)"

    @property
    def api_version(self) -> str | None:
        return None

    # -- what this vendor is made of ---------------------------------------

    @property
    def config(self) -> ToastConfig:
        return self._config

    @property
    def ids(self) -> ToastIds:
        return self._ids

    @property
    def request_ids(self) -> ToastRequestIds:
        return self._request_ids

    @property
    def capabilities(self) -> Sequence[CapabilityDecl]:
        return TOAST_CAPABILITIES

    @property
    def not_supported(self) -> Mapping[str, str]:
        return TOAST_NOT_SUPPORTED

    @property
    def roles(self) -> Mapping[str, str]:
        return TOAST_ROLES

    @property
    def routes(self) -> Sequence[Route]:
        """The vendor surface, built once and cached: the login first, the
        subscription stand-in last."""
        if self._routes is None:
            self._routes = (
                auth_routes(self)
                + order_routes(self)
                + payment_routes(self)
                + menu_routes(self)
                + config_routes(self)
                + restaurant_routes(self)
                + partner_routes(self)
                + stock_routes(self)
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
        """``Toast-Signature`` and every delivery header. See :mod:`.signer`."""
        return self._signer

    @property
    def events(self) -> EventMapper | None:
        """Journal entry to the documented envelope. See :mod:`.events`."""
        return self._events

    @property
    def magic(self) -> MagicTriggerSpec | None:
        return TOAST_MAGIC

    @property
    def machines(self) -> Mapping[str, MachineDef]:
        return {CHECK_MACHINE_NAME: CHECK_MACHINE, GUEST_ORDER_MACHINE_NAME: GUEST_ORDER_MACHINE}

    @property
    def retry_defaults(self) -> ProfileDocument:
        return toast_retry_defaults()

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
        """Phase two of configuration, then load the seed scenario."""
        self._resolve_config(ctx)
        hydrate_toast(ctx, seed, self._config)

    def _resolve_config(self, ctx: UnitContext) -> None:
        block = dict(ctx.config.vendor_config)
        self._config = self._base_config if not block else self._base_config.merged_with(block)
        self._ids.reseed(ctx.config.chaos.seed)
        self._request_ids.reseed(ctx.config.chaos.seed)
        self._errors = self._build_errors()

    def decorate(self, headers: dict[str, str], ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp only ``x-unit-vendor``: Toast has no version header."""
        headers["x-unit-vendor"] = ctx.vendor.name


def create_toast_vendor(*, vendor_config: dict[str, Any] | None = None, seed: int = 1) -> VendorDefinition:
    """Build a Toast vendor. The return annotation is the protocol, so
    ``mypy --strict`` checks the structural conformance here."""
    return ToastVendor(config=resolve_toast_config(vendor_config), seed=seed)
