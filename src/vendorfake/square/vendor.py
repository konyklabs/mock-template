"""The Square vendor definition -- everything the core needs to become a Square
unit, and nothing else.

FOR: assembling one object that satisfies
:class:`~vendorfake.core.kernel.types.VendorDefinition`. Compare this file's
length with the core it plugs into: that ratio is the authoring-economics claim
this project makes.

INVARIANT: **one vendor instance per unit.** :data:`VENDOR` is minted fresh on
every attribute access, which is unusual enough to state plainly. A vendor owns
a *stateful* id stream, and the whole point of that stream is that two runs of
the same scenario produce the same ids so a transcript can be diffed. A single
shared instance would have two units in one process -- which is exactly what the
conformance suite builds, a fresh unit per check -- drawing from one stream and
interleaving, so neither run would reproduce. The reference has no such problem
because its ``createSquareVendor`` factory is called per unit; the registry here
resolves a module *attribute*, so the module makes that attribute a factory.

Configuration resolves in two phases
------------------------------------
A profile's ``vendor`` block is part of the *profile*, and the profile is
loaded after the vendor is resolved -- ``create_unit`` needs the vendor to know
where the profiles are. So this object starts with defaults and re-resolves in
:meth:`SquareVendor.hydrate`, which the unit calls at start and again on
``POST /__unit/state/reset``, from ``ctx.config.vendor_config``. Anything built
out of the config (the error shaper) is rebuilt there too, and the id stream is
re-seeded from the unit's seed, which is what makes a re-hydrated unit mint the
ids it minted the first time.

Routes are built once, bound to this object
-------------------------------------------
:attr:`SquareVendor.routes` builds its surfaces on first access and caches
them, and each surface holds *this vendor*, not a copy of its configuration.
That is what lets the routes exist before ``hydrate`` has resolved the profile:
a handler reads ``deps.config`` when it runs, so a profile that replaces the
application secret is in force on the next request rather than on the next
process.

The webhook seams, and why they are two
--------------------------------------
``signer`` and ``events`` are separate properties because they answer separate
questions: what a mutation *means* on the wire, and how a delivery is proven to
have come from here. The dispatcher requires both before it will deliver
anything, which is why a vendor that supplied only one would send nothing
rather than send something unverifiable. ``None`` remains a legitimate answer
for a vendor with no webhook scheme at all; this one has both.
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
"""The scopes this unit's routes ask for.

All but the last are a subset of Square's published OAuth permissions
(https://developer.squareup.com/docs/oauth-api/square-permissions) -- the ones
the modelled surface actually needs. A route names the scopes it requires and
the kernel checks them against the token, so this tuple is the vocabulary and
not the policy.

The last one is not on that page, and is the only entry here that is not:
Square publishes no webhook permission because the Webhook Subscriptions API is
application-owned rather than seller-owned. The citations and the JUDGMENT are
on :data:`~vendorfake.square.config.WEBHOOK_SUBSCRIPTIONS_SCOPE`.
"""

SQUARE_MAGIC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("order.reference_id", "idempotency_key", "subscription.name"),
    query_params=("state",),
)
"""In-band fault triggering, in fields a consumer can set through an SDK.

Prior art is Square's own sandbox, which uses magic values in ordinary request
fields (``cnon:card-nonce-declined``) rather than a control channel, so a
consumer's real client library can drive a fault.
https://developer.squareup.com/docs/devtools/sandbox/testing
"""

_VOLATILE_FIELDS: tuple[str, ...] = (
    "expires_at",
    "refresh_token_expires_at",
    "closed_at",
    "used_at",
    "revoked_at",
    "superseded_at",
    # Stamped from the clock by a mutation: a catalog upsert sets the
    # millisecond-epoch `catalog_version`, an inventory change `calculated_at`,
    # a loyalty enrolment `enrolled_at` and `mapping_created_at`.
    "catalog_version",
    "calculated_at",
    "enrolled_at",
    "mapping_created_at",
    # Fulfillment-details stamps, one level down inside `fulfillments[]`:
    # `placed_at` on creation and every transition stamp, exactly the set
    # surface/orders.py can write from the clock. The digest matches names at
    # any depth, so a name here covers `tenders[].created_at` too without
    # listing it (the core already covers `created_at`).
    *sorted(FULFILLMENT_STAMPS),
)
"""Entity fields whose values are excluded from the state digest because this
unit writes them from its clock. Two units seeded identically a second apart,
and driven with the same traffic, must still agree, and these are the fields
that would otherwise make them differ.

The rule, stated once: **a stamp the unit set is volatile; a value the caller
sent is state.** Two properties of the core digest (``Store.entity_digest``)
carry the first half -- a name matches at any depth, so the stamps inside
``tenders[]``, ``fulfillments[].pickup_details`` and ``reward_tiers[]`` are
covered, and a set field still hashes as *set*, so a spent authorization code
(``used_at``) and a fresh one digest differently although the instant itself
is ignored. The second half is the vendor's: a fulfillment stamp the *caller*
supplied under one of these names (``picked_up_at`` beside ``state:
COMPLETED``, ``expires_at`` on pickup details) is mirrored into the
fulfillment's ``supplied_stamps`` -- ``[name, value]`` pairs, so no volatile
name appears as a key -- which the digest hashes and the wire never shows -- so two orders that differ only in a caller-sent
instant digest differently. ``tests/unit/square/test_digest_determinism.py``
pins both halves.

Not listed on purpose: ``pickup_at``, ``deliver_at``, ``courier_pickup_at``
and the other *schedule* instants, which only a caller ever sets. They are
what the consumer asked for, not what the clock said, and stay in the digest
under their own names."""


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
        # Both hold *this vendor*, not a copy of its configuration, so that a
        # profile resolved in `hydrate` -- which runs after construction -- is
        # in force on the next delivery rather than on the next process. That
        # is the same rule the surfaces follow; see `surface/common.py`.
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
        """The resolved configuration. Not part of the protocol; the surfaces
        read it, and a test asserting that a profile's ``vendor`` block took
        effect reads it too."""
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
    def routes(self) -> Sequence[Route]:
        """The vendor surface, built once and cached.

        Cached because ``Route`` handlers are bound methods of a surface object
        and rebuilding them on every access would make two reads of this
        property produce routes that compare unequal -- which the router, the
        capability index and the OpenAPI document would each see differently.
        """
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
        """Square's HMAC scheme, and every header a delivery carries.

        One object for both because the signature is a header too: two hooks
        would be two chances to register only one, and a delivery that is
        signed but carries no retry counter -- or counted but unsigned -- is
        silent at the sink.
        """
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
        """The order, fulfillment and payment lifecycles, at ``GET /__unit/machines``.

        This is the registration the reference lacks: its ``orderMachine`` is a
        module-level singleton nothing publishes, so "every declared terminal
        state really is terminal" could not be asserted from outside the vendor
        package.
        """
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
    def profile_dir(self) -> Path:
        return _PACKAGE_DIR / "profiles"

    @property
    def base_dir(self) -> Path:
        """What a profile's relative ``seed`` path resolves against.

        The package root, one level above the profiles, which is where the
        reference resolves seeds from.
        """
        return _PACKAGE_DIR

    # -- lifecycle ---------------------------------------------------------

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        """Phase two of configuration, then load the seed scenario.

        The configuration step happens first and unconditionally, so that a
        profile's ``vendor`` block is in force even when hydration fails -- and
        so that the tokens the scenario seeds are stamped with the expiry the
        *profile's* TTL implies rather than the built-in default's.
        """
        self._resolve_config(ctx)
        hydrate_square(ctx, seed, self._config)

    def _resolve_config(self, ctx: UnitContext) -> None:
        """Re-resolve from the profile, then rebuild what depends on it.

        The id stream is re-seeded rather than continued: a unit that
        re-hydrates must mint the same ids it minted the first time, which is
        what makes ``POST /__unit/state/reset`` reproduce a scenario instead of
        merely repeating it.
        """
        block = dict(ctx.config.vendor_config)
        self._config = self._base_config if not block else self._base_config.merged_with(block)
        self._errors = self._build_errors()
        self._ids.reseed(ctx.config.chaos.seed)

    def decorate(self, res: MutableResponse, ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp the API version on every response, success or error.

        "Regardless of whether you explicitly specify a version in the request,
        the response always returns the Square-Version header so you know which
        API version is used."
        https://developer.squareup.com/docs/build-basics/versioning-overview

        JUDGMENT -- **whatever the request sent is echoed, unchanged.** An empty
        ``square-version``, an unsupported date, ``banana`` -- all come back
        verbatim. The versioning page documents only that "the response always
        returns the ``Square-Version`` header"; it says nothing about what a
        request carrying a version this API does not support gets back, and
        this unit implements exactly one API version, so it has no supported
        set to check a value against. **NOT VERIFIED**: a consumer must not
        read the echo as "this version was accepted". The alternative --
        substituting the configured version whenever the request's value is
        unrecognised -- would quietly hide a consumer's typo instead, which is
        the failure mode a fake exists to surface.
        """
        requested = req.headers.get("square-version")
        res.headers["square-version"] = self._config.api_version if requested is None else requested
        res.headers["x-unit-vendor"] = ctx.vendor.name


def create_square_vendor(
    *,
    vendor_config: dict[str, Any] | None = None,
    seed: int = 1,
) -> VendorDefinition:
    """Build a Square vendor.

    ``vendor_config`` is the base a profile's ``vendor`` block is merged over,
    and ``seed`` seeds the id stream until :meth:`SquareVendor.hydrate` re-seeds
    it from the unit. Both exist for tests and for a caller assembling a unit by
    hand; ``create_unit(vendor="square")`` needs neither.

    The return annotation is the protocol, so ``mypy --strict`` checks the
    structural conformance of :class:`SquareVendor` here, at one call site,
    rather than wherever a unit happens to be built.
    """
    return SquareVendor(config=resolve_square_config(vendor_config), seed=seed)
