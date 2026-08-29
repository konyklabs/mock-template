"""The Clover vendor definition -- everything the core needs to become a Clover
unit, and nothing else.

FOR: assembling one object that satisfies
:class:`~vendorfake.core.kernel.types.VendorDefinition`. This is the second
vendor in the distribution, and it plugs into the same core the Square vendor
does with zero core changes -- which is the authoring-economics claim this
project makes.

INVARIANT: **one vendor instance per unit.** :data:`VENDOR` (in
``__init__.py``) is minted fresh on every attribute access. A vendor owns a
*stateful* id stream, and the whole point of that stream is that two runs of
the same scenario produce the same ids so a transcript can be diffed. A single
shared instance would have two units in one process -- exactly what the
conformance suite builds, a fresh unit per check -- drawing from one stream
and interleaving, so neither run would reproduce.

Configuration resolves in two phases, exactly as Square's does: defaults at
construction, then :meth:`CloverVendor.hydrate` re-resolves from
``ctx.config.vendor_config`` -- at start and again on
``POST /__unit/state/reset`` -- rebuilds what depends on it (the error
shaper), and re-seeds the id stream from the unit's seed.

PR-B shape: **the OAuth surface and the real auth adapter are live**; orders,
inventory and merchant land in PR C. ``signer`` and ``events`` stay ``None``
and the two webhook gates sit in ``not_supported`` until PR D ships the seams
that would make them deliverable (see ``capabilities.py``). The machines,
retry defaults, error table, id stream and configuration are final-shape.

``api_version`` is ``None``, and that is a statement about Clover rather than
an omission: Clover documents no version request or response header -- the
version lives in the path (``/v3/``, ``/oauth/v2/``) -- so there is nothing
for :meth:`decorate` to stamp beyond the core's own ``x-unit-vendor``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from vendorfake.clover.auth import CloverAuth
from vendorfake.clover.capabilities import CLOVER_CAPABILITIES, CLOVER_NOT_SUPPORTED
from vendorfake.clover.config import CloverConfig, resolve_clover_config
from vendorfake.clover.errors import CloverErrorShaper
from vendorfake.clover.ids import CloverIds
from vendorfake.clover.machine import ORDER_MACHINE, ORDER_MACHINE_NAME
from vendorfake.clover.retry import clover_retry_defaults
from vendorfake.clover.surface.oauth import oauth_routes
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
    UnitError,
    UnitErrorKind,
    UnitRequest,
    VendorDefinition,
)
from vendorfake.core.state.machine import MachineDef

__all__ = ["CLOVER_MAGIC", "CloverVendor", "create_clover_vendor"]

_PACKAGE_DIR = Path(__file__).resolve().parent

CLOVER_MAGIC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("note", "title", "externalReferenceId"),
    query_params=("state",),
)
"""In-band fault triggering, in fields a consumer can set through a real
Clover client: an order's ``note``, ``title`` and ``externalReferenceId`` are
all ordinary writable order fields
(https://docs.clover.com/dev/docs/creating-custom-orders). Prior art for the
convention is Square's sandbox magic values
(https://developer.squareup.com/docs/devtools/sandbox/testing); Clover
publishes no equivalent, so the mechanism is this project's, flagged by the
``chaos:`` prefix no real value would carry."""

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
"""Entity fields excluded from the state digest because they carry wall-clock
time. Two units seeded identically a second apart must still agree. The core
already excludes ``created_at``/``updated_at``, but every stored instant in
this package is a camelCase Clover field or a ``_ms``-suffixed internal one
(see ``entities.py``), so each is listed explicitly. The OAuth expirations
appear under their stored ``_ms`` names: the digest hashes entities, and the
Unix-seconds spellings exist only on the wire."""


class CloverVendor:
    """One Clover vendor, for one unit. Satisfies ``VendorDefinition``."""

    __slots__ = (
        "_auth",
        "_base_config",
        "_config",
        "_errors",
        "_ids",
        "_routes",
        "_seed",
    )

    def __init__(self, *, config: CloverConfig | None = None, seed: int = 1) -> None:
        self._base_config = CloverConfig() if config is None else config
        self._config = self._base_config
        self._seed = seed
        self._ids = CloverIds(seed)
        self._errors = self._build_errors()
        # Holds *this vendor*, not a copy of its configuration, so a profile
        # resolved in `hydrate` -- which runs after construction -- is in
        # force on the next request. Same rule as the surfaces; see
        # `surface/common.py`.
        self._auth = CloverAuth(self)
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
        """``None``: Clover has no version header to report. See the module
        docstring."""
        return None

    # -- what this vendor is made of ---------------------------------------

    @property
    def config(self) -> CloverConfig:
        """The resolved configuration. Not part of the protocol; the surfaces
        read it, and a test asserting that a profile's ``vendor`` block took
        effect reads it too."""
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
    def routes(self) -> Sequence[Route]:
        """The vendor surface, built once and cached.

        Cached because ``Route`` handlers are bound methods of a surface
        object and rebuilding them on every access would make two reads of
        this property produce routes that compare unequal. OAuth for now;
        orders, inventory and merchant land in PR C, webhooks in PR D.
        """
        if self._routes is None:
            self._routes = oauth_routes(self)
        return self._routes

    @property
    def errors(self) -> ErrorShaper:
        return self._errors

    @property
    def auth(self) -> AuthAdapter:
        return self._auth

    @property
    def signer(self) -> Signer | None:
        """``None`` until PR D. The dispatcher delivers nothing for a vendor
        without a signer, which is the correct behaviour for a vendor that has
        no webhook surface yet."""
        return None

    @property
    def events(self) -> EventMapper | None:
        """``None`` until PR D, for the same reason as :attr:`signer`."""
        return None

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
    def profile_dir(self) -> Path:
        return _PACKAGE_DIR / "profiles"

    @property
    def base_dir(self) -> Path:
        """What a profile's relative ``seed`` path resolves against: the
        package root, one level above the profiles."""
        return _PACKAGE_DIR

    # -- lifecycle ---------------------------------------------------------

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        """Phase two of configuration; the seed scenario lands in PR E.

        The configuration step happens first and unconditionally, so that a
        profile's ``vendor`` block is in force even with nothing to seed. A
        profile that *does* name a seed document is refused loudly rather than
        silently ignored -- a scenario that "loaded" into nothing would be the
        worst version of this gap.
        """
        self._resolve_config(ctx)
        if seed is not None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    "This Clover vendor has no seed parser yet (the scenario lands in PR E); "
                    "remove the profile's seed path."
                ),
                field="seed",
            )

    def _resolve_config(self, ctx: UnitContext) -> None:
        """Re-resolve from the profile, then rebuild what depends on it.

        The id stream is re-seeded rather than continued: a unit that
        re-hydrates must mint the same ids it minted the first time, which is
        what makes ``POST /__unit/state/reset`` reproduce a scenario instead
        of merely repeating it.
        """
        block = dict(ctx.config.vendor_config)
        self._config = self._base_config if not block else self._base_config.merged_with(block)
        self._errors = self._build_errors()
        self._ids.reseed(ctx.config.chaos.seed)

    def decorate(self, res: MutableResponse, ctx: UnitContext, req: UnitRequest) -> None:
        """Stamp only ``x-unit-vendor``: Clover has no version header. See the
        module docstring."""
        res.headers["x-unit-vendor"] = ctx.vendor.name


def create_clover_vendor(
    *,
    vendor_config: dict[str, Any] | None = None,
    seed: int = 1,
) -> VendorDefinition:
    """Build a Clover vendor.

    ``vendor_config`` is the base a profile's ``vendor`` block is merged over,
    and ``seed`` seeds the id stream until :meth:`CloverVendor.hydrate`
    re-seeds it from the unit. Both exist for tests and for a caller
    assembling a unit by hand; ``create_unit(vendor="clover")`` needs neither.

    The return annotation is the protocol, so ``mypy --strict`` checks the
    structural conformance of :class:`CloverVendor` here, at one call site,
    rather than wherever a unit happens to be built.
    """
    return CloverVendor(config=resolve_clover_config(vendor_config), seed=seed)
