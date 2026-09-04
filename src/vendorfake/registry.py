"""Which vendor, and the one constructor that builds a unit from it.

Invariant: a typo in a vendor name is a startup failure listing the real ones, a
silently unloaded vendor otherwise presenting as "every endpoint 404s".
Invariant: ``create_unit``'s ``env`` defaults to ``{}``; the process environment
enters only through :func:`ambient_env`, which every binding layers explicit
configuration over. Vendors come from
the ``vendorfake.vendors`` entry-point group, with a built-in map for a source
tree that has no installation metadata, both filtered through an importability
check so no advertised name fails to load.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import entry_points

from vendorfake.core.config.models import parse_profile_document
from vendorfake.core.config.profile import load_profile
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.types import Logger, SeedingVendor, VendorDefinition
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.webhooks.sink import DeliverySink

__all__ = [
    "ENTRY_POINT_GROUP",
    "ROLE_NAMES",
    "VENDOR_ENV_VAR",
    "ProfileInfo",
    "RouteInfo",
    "SeedingVendor",
    "VendorDefinition",
    "ambient_env",
    "available_profiles",
    "available_vendors",
    "create_unit",
    "resolve_capabilities",
    "resolve_vendor",
    "routes",
]

ENTRY_POINT_GROUP = "vendorfake.vendors"
"""Group a distribution declares to publish a vendor, e.g.
``square = "vendorfake.square:VENDOR"``."""

VENDOR_ENV_VAR = "VENDORFAKE_VENDOR"
"""Selects the vendor when ``create_unit`` is given none. Absent from the profile
loader's table: it decides which module to import, before a profile exists."""

_BUILTIN: Mapping[str, str] = {
    "clover": "vendorfake.clover:VENDOR",
    "lightspeed": "vendorfake.lightspeed:VENDOR",
    "square": "vendorfake.square:VENDOR",
    "toast": "vendorfake.toast:VENDOR",
}
"""Vendors shipped in this distribution, as ``module:attribute`` targets: the
fallback for a source tree with no installation metadata. Entry points win."""


def _targets() -> dict[str, str]:
    """Every declared vendor name mapped to its ``module:attribute`` target."""
    found = dict(_BUILTIN)
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        found[entry.name] = entry.value
    return found


def _importable(target: str) -> bool:
    """Whether ``target``'s module can be found, without executing it."""
    module_name = target.split(":", 1)[0]
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def available_vendors() -> tuple[str, ...]:
    """Every vendor name that would actually resolve, sorted."""
    return tuple(sorted(name for name, target in _targets().items() if _importable(target)))


# Discovery: profiles, routes, and the neutral capability-role vocabulary. Every
# function below reads through the same loader or route table ``create_unit`` and
# the control plane use, so what it reports cannot disagree with what a unit
# accepts or serves.

ROLE_NAMES: tuple[str, ...] = ("auth", "orders", "webhooks", "chaos")
"""The neutral capability roles every vendor's ``VendorDefinition.roles`` maps,
accepted by ``capabilities=`` alongside a vendor's own names. Fixed at four: a
fifth needs a role in every shipped vendor and the clause that checks it."""


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """One profile a vendor ships, as :func:`available_profiles` publishes it."""

    vendor: str
    name: str
    summary: str
    capabilities: tuple[str, ...]
    #: The seed document the profile names, or ``None`` if it loads none.
    seed: str | None


def _profiles_of(definition: VendorDefinition) -> tuple[ProfileInfo, ...]:
    """The profile scan, off an already-resolved :class:`VendorDefinition` rather
    than a name, so a caller holding one pays for no second lookup and a fixture
    vendor that is not a registered entry point can be scanned at all."""
    out: list[ProfileInfo] = []
    for path in sorted(definition.profile_dir.glob("*.json"), key=lambda candidate: candidate.stem):
        document = parse_profile_document(json.loads(path.read_text(encoding="utf-8")), source=str(path))
        out.append(
            ProfileInfo(
                vendor=definition.name,
                # The file's stem, not `document.name`: `load_profile` addresses a
                # profile by stem, so the name reported must be the one that loads.
                name=path.stem,
                summary=document.summary or "",
                capabilities=document.capabilities,
                seed=document.seed,
            )
        )
    return tuple(out)


def available_profiles(vendor: str) -> tuple[ProfileInfo, ...]:
    """Every profile ``vendor`` ships, sorted by name, read through the same schema
    ``load_profile`` validates against, each ``name`` being the file's stem it
    addresses. Not the fully resolved config: no environment layer."""
    return _profiles_of(resolve_vendor(vendor))


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """One row of a vendor's route table, trimmed of what the control plane also
    publishes. Deliberately a smaller type than
    :class:`vendorfake.core.kernel.unit.RouteInfo`, never re-exported beside it."""

    method: str
    path: str
    operation_id: str | None
    capability: str
    summary: str | None
    internal: bool


def routes(vendor: str, profile: str = "full") -> tuple[RouteInfo, ...]:
    """Every route ``vendor``'s surface and control plane serve, built from the
    table ``GET /__unit/routes`` answers rather than reassembled, so a row here is
    one the unit will match. ``profile`` exists because building a unit needs one;
    the table does not vary by profile, every declared route being registered
    whether or not its capability is enabled."""
    built = create_unit(vendor=vendor, profile=profile)
    try:
        return tuple(
            RouteInfo(
                method=row.method,
                path=row.path,
                operation_id=row.operation_id,
                capability=row.capability,
                summary=row.summary,
                internal=row.internal,
            )
            for row in built.control.list_routes()
        )
    finally:
        built.stop()


def _translate_capability_names(definition: VendorDefinition, requested: Sequence[str]) -> tuple[str, ...]:
    """A role name becomes this vendor's own capability name; anything else passes
    through as already being one. ``roles`` is read with :func:`getattr`, an older
    third-party vendor not having the property. A vendor that maps no roles raises
    ``ValueError`` rather than passing ``"auth"`` through as a capability name."""
    roles: Mapping[str, str] = getattr(definition, "roles", {})
    if not roles:
        asked = [name for name in requested if name in ROLE_NAMES]
        if asked:
            raise ValueError(
                f"Vendor {definition.name!r} maps no capability roles, so capabilities="
                f"{list(requested)!r} cannot be resolved: {', '.join(asked)} "
                f"{'is a role name' if len(asked) == 1 else 'are role names'} "
                f"({', '.join(ROLE_NAMES)}) and this vendor publishes no VendorDefinition.roles "
                "to translate it through. Implement `roles` on the vendor definition (see the "
                "shipped vendors, and CHANGELOG.md's Breaking changes for 0.2), or ask for this "
                "vendor's own capability names instead."
            )
    return tuple(roles.get(name, name) for name in requested)


def _narrowest_profile_for(definition: VendorDefinition, translated: Sequence[str]) -> str | None:
    """The shipped profile whose capability set is the smallest superset of
    ``translated``, ties broken by name, or ``None`` when none qualifies."""
    wanted = frozenset(translated)
    candidates = [profile for profile in _profiles_of(definition) if wanted <= frozenset(profile.capabilities)]
    if not candidates:
        return None
    chosen = min(candidates, key=lambda profile: (len(profile.capabilities), profile.name))
    return chosen.name


def resolve_vendor(name: str) -> VendorDefinition:
    """Load the vendor called ``name``. Raises ``ValueError``, not a ``UnitError``:
    this happens before a unit exists, so there is no vendor to shape an error
    with, and a caller at the edge turns it into whatever its surface needs."""
    targets = _targets()
    target = targets.get(name)
    if target is None or not _importable(target):
        offered = available_vendors()
        listing = ", ".join(offered) if offered else "(none installed)"
        raise ValueError(f"no vendor named {name!r}. Available: {listing}")
    module_name, _, attribute = target.partition(":")
    module = importlib.import_module(module_name)
    definition: VendorDefinition = getattr(module, attribute or "VENDOR")
    return definition


def _pick(vendor: str | VendorDefinition | None, env: Mapping[str, str]) -> VendorDefinition:
    if vendor is None:
        named = env.get(VENDOR_ENV_VAR)
        if named:
            return resolve_vendor(named)
        offered = available_vendors()
        if len(offered) == 1:
            # Exactly one vendor installed, so there is no choice to make.
            return resolve_vendor(offered[0])
        listing = ", ".join(offered) if offered else "(none installed)"
        raise ValueError(
            f"create_unit needs a vendor: pass vendor=..., or set {VENDOR_ENV_VAR} in the env mapping. "
            f"Available: {listing}"
        )
    if isinstance(vendor, str):
        return resolve_vendor(vendor)
    return vendor


def ambient_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The ``VENDORFAKE_*`` variables of the process environment: the one place a
    binding reads it. ``unit()``, ``served()`` and the CLI all layer explicit
    configuration over this, so an exported variable means one thing everywhere."""
    source = os.environ if environ is None else environ
    return {key: value for key, value in source.items() if key.startswith("VENDORFAKE_")}


def resolve_capabilities(
    definition: VendorDefinition, profile: str | None, capabilities: Sequence[str] | None
) -> tuple[str | None, dict[str, str]]:
    """Turn a ``capabilities=`` request into ``(profile name, env layer)``: the
    narrowest shipped profile covering the translated set, else ``full`` plus a
    ``VENDORFAKE_CAPABILITIES`` layer. Refuses ``capabilities`` together with
    ``profile``, and an empty list. Shared by ``create_unit`` and ``served()``."""
    if capabilities is None:
        return profile, {}
    if profile is not None:
        raise ValueError(
            "capabilities=... and profile=... were both given; they are two different answers to which "
            "profile to start. Name the profile you want, or name the capabilities and let resolution "
            "choose one -- not both."
        )
    if len(capabilities) == 0:
        raise ValueError(
            "capabilities=[] is ambiguous: an empty set is a subset of every profile's capabilities. Pass "
            "capabilities=None to mean 'no capability request', profile=... to name a profile, or a "
            "non-empty list of capabilities or roles."
        )
    translated = _translate_capability_names(definition, tuple(capabilities))
    matched = _narrowest_profile_for(definition, translated)
    if matched is not None:
        return matched, {}
    return "full", {"VENDORFAKE_CAPABILITIES": ",".join(translated)}


def create_unit(
    *,
    vendor: str | VendorDefinition | None = None,
    profile: str | None = None,
    capabilities: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    sink: DeliverySink | None = None,
    logger: Logger | None = None,
) -> Unit:
    """Build and start a unit. The single constructor.

    The order is fixed: resolve the vendor, resolve ``capabilities`` into a
    profile name when given, load the profile with ``vendor.retry_defaults``
    merged under the document and the document under the environment layer,
    construct the unit with its control plane, and start it, which hydrates the
    store from the seed document.

    ``env`` is a plain mapping and defaults to empty. ``capabilities`` is resolved
    instead of ``profile``, and passing both is a ``ValueError``, as is an empty
    list, which is a subset of every profile and would pick the smallest. Each
    name is a role in :data:`ROLE_NAMES`, translated through ``vendor.roles``, or
    one of this vendor's own capability names; the set picks the narrowest shipped
    profile that is a superset of it, and ``full`` plus
    ``VENDORFAKE_CAPABILITIES`` when none qualifies. ``GET /__unit/info`` reports
    the original request under ``requested_capabilities``.
    """
    environ: Mapping[str, str] = {} if env is None else env
    definition = _pick(vendor, environ)

    requested = None if capabilities is None else tuple(capabilities)
    resolved_profile, capability_layer = resolve_capabilities(definition, profile, capabilities)
    environ = {**environ, **capability_layer}

    loaded = load_profile(
        profile_dir=definition.profile_dir,
        name=resolved_profile,
        base_dir=definition.base_dir,
        env=environ,
        defaults=definition.retry_defaults,
    )
    config = (
        loaded.config if requested is None else loaded.config.model_copy(update={"requested_capabilities": requested})
    )
    unit = Unit(
        vendor=definition,
        config=config,
        seed=loaded.seed,
        sink=sink,
        logger=logger,
        # Every unit built here has a control plane; the constructor keeps it
        # optional only so a kernel test can build a unit without one.
        control_routes=control_plane_routes,
    )
    unit.start()
    return unit
