"""The Lightspeed vendor definition -- everything the core needs to become a
Lightspeed X-Series unit, and nothing else.

FOR: assembling one object that satisfies
:class:`~vendorfake.core.kernel.types.VendorDefinition`. This is the fourth
vendor in the distribution and plugs into the same core the Square, Clover and
Toast vendors do.

INVARIANT: **one vendor instance per unit.** :data:`VENDOR` (in
``__init__.py``) is minted fresh on every attribute access, because a vendor
owns four pieces of mutable state -- two id streams, the retailer's version
counter and the rate limiter's window -- and two units sharing any of them
would interleave.

Configuration resolves in two phases: defaults at construction, then
:meth:`LightspeedVendor.hydrate` re-resolves from ``ctx.config.vendor_config``
-- at start and again on ``POST /__unit/state/reset`` -- rebuilds the error
shaper, reseeds both id streams, restarts the version counter and recomputes
the rate limiter's quota from the number of registers the scenario just
loaded.

``api_version`` is ``"2026-07"``: unlike Toast's, this vendor's version is a
real, named thing -- the document's ``info.version``, and the path segment
every resource route sits under.

WHY THE RATE LIMITER'S QUOTA IS COMPUTED IN ``hydrate`` AND NOWHERE ELSE. The
documented formula is ``300 x <number of registers> + 50``, so the quota is a
function of the scenario -- which does not exist when the vendor is
constructed. Recomputing it per request would be a store scan on the hot path
for a number that only a re-seed can change.

THE ROLE MAPPING, and the one JUDGMENT in it. ``VendorDefinition.roles`` must
map all four neutral roles to capabilities this vendor declares. ``auth``,
``webhooks`` and ``chaos`` map to themselves. ``orders`` maps to
``registers``: Lightspeed's order-equivalent resource is a *sale*, and the
Sales tag arrives in a later slice of konyklabs/roadmap#94, so the transactional
surface this slice actually serves is the till lifecycle -- open, close, and
the payment totals a closure carries. The mapping moves to ``sales`` when that
surface lands, which is a change to this one line and to the ``orders-only``
profile.
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
"""In-band fault triggering, in fields a consumer can set through a real
Lightspeed client: a webhook's ``url`` is an ordinary writable string
(``WebhookRequest``) and ``state`` is the documented opaque round-trip
parameter on the authorize URL, which is exactly the kind of field a caller
controls end to end. Prior art is Square's sandbox magic values; Lightspeed
publishes no equivalent, so the mechanism is this project's, flagged by the
``chaos:`` prefix no real value would carry."""

LIGHTSPEED_ROLES: Mapping[str, str] = {
    "auth": "auth",
    "orders": "registers",
    "webhooks": "webhooks",
    "chaos": "chaos",
}
"""The neutral role vocabulary, mapped to this vendor's own capability names.
See the module docstring for why ``orders`` points at ``registers`` in this
slice."""

_VOLATILE_FIELDS: tuple[str, ...] = (
    "expires_at_ms",
    "created_at_ms",
    "revoked_at_ms",
    "retired_at_ms",
    "used_at_ms",
    "register_close_time",
    "activated_at",
)
"""Entity fields excluded from the state digest because this unit writes them
from its clock. The core matches these names at any depth. ``register_open_time``
is deliberately ABSENT: the seed states it, so it is scenario data rather than a
stamp, and a digest that ignored it could not tell a register the scenario
opened last Tuesday from one it opened this morning. ``register_close_time`` IS
here, because only a close action ever writes it. The digest keeps each
scrubbed field's *presence*, so closing a register still moves it."""

_OPAQUE_FIELDS: tuple[str, ...] = (
    "document",
    "config",
    "attributes",
)
"""Caller (or seed) free-form subtrees the digest takes verbatim: the retailer's
uncomputed blocks, a payment type's ``config`` ("Shape varies by payment type",
``additionalProperties: true``) and an outlet's ``attributes`` list. The
volatile scrub must not descend into any of them -- a retailer's
``document.activated_at`` is state the seed states, not a stamp this unit
wrote."""


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
        """The vendor surface, built once and cached: auth first, so the
        credential-issuing routes are what a reader meets at the top of
        ``GET /__unit/routes``."""
        if self._routes is None:
            self._routes = (
                auth_routes(self)
                + retailer_routes(self)
                + outlet_routes(self)
                + register_routes(self)
                + payment_type_routes(self)
                # konyklabs/roadmap#94, slice L2a. Appended AFTER the register
                # routes deliberately: conformance drives the FIRST route that
                # publishes an `example_body`, and that is still CloseRegister.
                # See the note in surface/products.py.
                + product_routes(self)
                + inventory_routes(self)
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
        """``X-Signature``, the delivery headers, and the form encoding of the
        body itself. See :mod:`.signer`."""
        return self._signer

    @property
    def events(self) -> EventMapper | None:
        """Journal entry to one of the seven documented event types. See
        :mod:`.events`."""
        return self._events

    @property
    def magic(self) -> MagicTriggerSpec | None:
        return LIGHTSPEED_MAGIC

    @property
    def machines(self) -> Mapping[str, MachineDef]:
        """None. A register is open or closed and the two actions are the only
        transitions; that is a boolean rather than a lifecycle, and declaring a
        two-state machine for it would publish a vocabulary no route uses. The
        sale lifecycle (``parked``/``pending``/``voided``/``closed``, an
        ``enum`` on ``SaleRequestBase``) is a real machine and arrives with the
        Sales surface."""
        return {}

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

    def decorate(self, res: MutableResponse, ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp the vendor, the API version, and the two documented rate-limit
        headers.

        The rate-limit pair is here rather than in the handlers because the
        page says they are on EVERY response -- the success, the shaped
        refusal, and the 429 itself. ``decorate`` runs on all three for a
        matched vendor route and on none of the control plane's, which is
        exactly the boundary the documented quota has.
        """
        res.headers["x-unit-vendor"] = ctx.vendor.name
        res.headers["x-unit-api-version"] = API_VERSION
        snapshot = self._limiter.snapshot(ctx)
        res.headers[RATE_LIMIT_LIMIT_HEADER] = str(snapshot.limit)
        res.headers[RATE_LIMIT_REMAINING_HEADER] = str(snapshot.remaining)


def create_lightspeed_vendor(*, vendor_config: dict[str, Any] | None = None, seed: int = 1) -> VendorDefinition:
    """Build a Lightspeed vendor. The return annotation is the protocol, so
    ``mypy --strict`` checks the structural conformance here."""
    return LightspeedVendor(config=resolve_lightspeed_config(vendor_config), seed=seed)
