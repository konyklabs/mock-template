"""The Toast vendor definition -- everything the core needs to become a Toast
unit, and nothing else.

FOR: assembling one object that satisfies
:class:`~vendorfake.core.kernel.types.VendorDefinition`. This is the third
vendor in the distribution and plugs into the same core the Square and Clover
vendors do, with zero core changes.

INVARIANT: **one vendor instance per unit.** :data:`VENDOR` (in
``__init__.py``) is minted fresh on every attribute access, because a vendor
owns two stateful id streams (entity guids and error ``requestId``s) and two
units sharing either would interleave their draws.

Configuration resolves in two phases: defaults at construction, then
:meth:`ToastVendor.hydrate` re-resolves from ``ctx.config.vendor_config`` -- at
start and again on ``POST /__unit/state/reset`` -- rebuilds the error shaper,
and reseeds both id streams from the unit's seed.

``api_version`` is ``None``: Toast documents no version request or response
header -- the version lives in the path (``/orders/v2``, ``/menus/v3``).

Step 1 of the build (konyklabs/roadmap#39): the foundation. No routes yet; the
auth adapter, the error table, the machines, the id streams, the retry
defaults, the seed and the configuration are final-shape. The surfaces, the
signer and the event mapper land in the following steps.
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
    MutableResponse,
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
from vendorfake.toast.ids import ToastIds, ToastRequestIds
from vendorfake.toast.machine import (
    CHECK_MACHINE,
    CHECK_MACHINE_NAME,
    GUEST_ORDER_MACHINE,
    GUEST_ORDER_MACHINE_NAME,
)
from vendorfake.toast.retry import toast_retry_defaults
from vendorfake.toast.seed.hydrate import hydrate_toast
from vendorfake.toast.surface.auth import auth_routes
from vendorfake.toast.surface.config import config_routes
from vendorfake.toast.surface.menus import menu_routes
from vendorfake.toast.surface.orders import order_routes
from vendorfake.toast.surface.partners import partner_routes
from vendorfake.toast.surface.payments import payment_routes
from vendorfake.toast.surface.restaurants import restaurant_routes
from vendorfake.toast.surface.stock import stock_routes

__all__ = ["TOAST_MAGIC", "ToastVendor", "create_toast_vendor"]

_PACKAGE_DIR = Path(__file__).resolve().parent

TOAST_MAGIC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("externalId", "deliveryInfo.notes"),
    query_params=("pageToken",),
)
"""In-band fault triggering, in fields a consumer can set through a real Toast
client: an order's ``externalId`` and ``deliveryInfo.notes`` are ordinary
writable fields (toast-orders-api.yaml), and ``pageToken`` is the config API's
documented paging parameter. Prior art is Square's sandbox magic values; Toast
publishes no equivalent, so the mechanism is this project's, flagged by the
``chaos:`` prefix no real value would carry."""

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
"""Entity fields excluded from the state digest because they carry wall-clock
time. Every stored instant is a camelCase Toast field or a ``_ms`` internal
one, so each is listed. ``access_token`` is here because a minted JWT carries
``iat``/``exp`` claims and therefore differs per run on a real clock; the
seeded tokens are constants regardless."""


class ToastVendor:
    """One Toast vendor, for one unit. Satisfies ``VendorDefinition``."""

    __slots__ = (
        "_auth",
        "_base_config",
        "_config",
        "_errors",
        "_ids",
        "_request_ids",
        "_routes",
        "_seed",
    )

    def __init__(self, *, config: ToastConfig | None = None, seed: int = 1) -> None:
        self._base_config = ToastConfig() if config is None else config
        self._config = self._base_config
        self._seed = seed
        self._ids = ToastIds(seed)
        self._request_ids = ToastRequestIds(seed)
        self._errors = self._build_errors()
        self._auth = ToastAuth(self)
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
    def routes(self) -> Sequence[Route]:
        """The vendor surface, built once and cached: the login first, so the
        first route of the table is the one a consumer calls first."""
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
        return None

    @property
    def events(self) -> EventMapper | None:
        return None

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

    def decorate(self, res: MutableResponse, ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp only ``x-unit-vendor``: Toast has no version header."""
        res.headers["x-unit-vendor"] = ctx.vendor.name


def create_toast_vendor(*, vendor_config: dict[str, Any] | None = None, seed: int = 1) -> VendorDefinition:
    """Build a Toast vendor. The return annotation is the protocol, so
    ``mypy --strict`` checks the structural conformance here."""
    return ToastVendor(config=resolve_toast_config(vendor_config), seed=seed)
