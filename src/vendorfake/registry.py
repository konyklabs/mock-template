"""Which vendor, and the one constructor that builds a unit from it.

FOR: turning a *name* -- from a CLI flag, an environment variable or a test --
into a running :class:`Unit`, and doing it in the one place that is allowed to
know both that vendors exist and how a profile is loaded.

INVARIANT: **a typo in a vendor name is a startup failure that lists the real
ones.** ``resolve_vendor("sqaure")`` raises ``ValueError`` naming every
available vendor; it never falls back to a default and never returns a unit
that quietly answers nothing. A fake whose vendor silently did not load would
present as "every endpoint 404s", which is indistinguishable from a consumer's
own misconfiguration.

SECOND INVARIANT: **``env`` defaults to ``{}``, never ``os.environ``.** Only
the CLI passes the real environment. The reference spread ``process.env`` into
every unit it built, which made a variable set by one test change the profile
of a unit built by another -- a whole class of order-dependent flakes that
simply cannot occur here. The rule is pinned by a test that sets real
environment variables and asserts they are ignored.

DISCOVERY. Vendors are found through the ``vendorfake.vendors`` entry-point
group, so a third-party distribution can add one without this file changing.
A built-in map covers the vendors shipped in this distribution, because a
source tree with no installation metadata has no entry points and "it works
from a checkout" is not a nicety -- it is how every test in this repository
runs. Both directions are filtered through an importability check, so
:func:`available_vendors` never advertises a name that would fail to load: an
error message listing a vendor that does not exist is worse than no message.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import json
from collections.abc import Callable, Mapping, Sequence
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
    "available_profiles",
    "available_vendors",
    "create_unit",
    "resolve_vendor",
    "routes",
]

ENTRY_POINT_GROUP = "vendorfake.vendors"
"""Group a distribution declares to publish a vendor, e.g.
``square = "vendorfake.square:VENDOR"``."""

VENDOR_ENV_VAR = "VENDORFAKE_VENDOR"
"""Selects the vendor when ``create_unit`` is given none.

Deliberately absent from the profile loader's environment table: it decides
which module to import, which happens before a profile exists, so it belongs to
the registry rather than to configuration."""

_BUILTIN: Mapping[str, str] = {
    "clover": "vendorfake.clover:VENDOR",
    "square": "vendorfake.square:VENDOR",
    "toast": "vendorfake.toast:VENDOR",
}
"""Vendors shipped in this distribution, as ``module:attribute`` targets.

The fallback for a source tree with no installation metadata. Entry points win
where both exist, so an installed override is never shadowed by this."""


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


# ---------------------------------------------------------------------------
# Discovery: profiles, routes, and the neutral capability-role vocabulary.
#
# FOR: finding a profile name or a route path by code, never by listing a
# vendor package in a scratch clone. Every function below reads through the
# same loader or the same route table ``create_unit`` and the control plane
# already use, so what this reports can never disagree with what a unit
# actually accepts or serves.
# ---------------------------------------------------------------------------

ROLE_NAMES: tuple[str, ...] = ("auth", "orders", "webhooks", "chaos")
"""The neutral capability roles every vendor's ``VendorDefinition.roles`` maps
-- the vocabulary ``capabilities=`` accepts alongside a vendor's own capability
names. Fixed at four: a fifth is added only together with a role in every
shipped vendor's ``roles`` mapping and the conformance clause that checks it."""


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """One profile a vendor ships, as :func:`available_profiles` publishes it."""

    vendor: str
    name: str
    summary: str
    capabilities: tuple[str, ...]
    #: The seed document path the profile names, relative to the vendor
    #: package -- ``None`` for a profile that loads no seed.
    seed: str | None


def _profiles_of(definition: VendorDefinition) -> tuple[ProfileInfo, ...]:
    """The scan :func:`available_profiles` and :func:`_narrowest_profile_for`
    both need, off an already-resolved :class:`VendorDefinition` rather than a
    name -- so a caller holding one directly (a test's fixture vendor, a
    capability request mid-resolution) never pays for a second, redundant
    trip through :func:`resolve_vendor`, and a fixture vendor whose name is
    not a registered entry point can be scanned at all."""
    out: list[ProfileInfo] = []
    for path in sorted(definition.profile_dir.glob("*.json"), key=lambda candidate: candidate.stem):
        document = parse_profile_document(json.loads(path.read_text(encoding="utf-8")), source=str(path))
        out.append(
            ProfileInfo(
                vendor=definition.name,
                # The file's stem, not `document.name` -- `load_profile` addresses a
                # profile by stem (`profile_path` below), so the name reported here
                # must be the name that then loads, not whatever a document's own
                # optional `name` field happens to say. All eighteen shipped
                # profiles agree today, which is exactly why a mismatch would go
                # unnoticed without this: `available_profiles` advertising a name
                # that `unit(vendor, that_name)` cannot load is precisely the class
                # of surprise this module exists to rule out.
                name=path.stem,
                summary=document.summary or "",
                capabilities=document.capabilities,
                seed=document.seed,
            )
        )
    return tuple(out)


def available_profiles(vendor: str) -> tuple[ProfileInfo, ...]:
    """Every profile ``vendor`` ships, sorted by name.

    Read from the packaged profile JSON through
    :func:`~vendorfake.core.config.models.parse_profile_document` -- the same
    schema :func:`~vendorfake.core.config.profile.load_profile` validates a
    profile against before ``create_unit`` will start on it -- so a name
    reported here can never be a name that then fails to parse. Each
    :class:`ProfileInfo`'s ``name`` is the file's *stem*, not its optional
    ``name`` field: :func:`~vendorfake.core.config.profile.load_profile`
    addresses a profile by stem, so a name this reports and the name that
    then loads it are guaranteed to be the same string, never merely the same
    string by every shipped profile happening to agree. Not the fully
    resolved config: no environment layer, no vendor defaults merged in, none
    of that is a property of the *profile document* this call describes.
    """
    return _profiles_of(resolve_vendor(vendor))


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """One row of a vendor's route table -- what a consumer discovers a route
    *by*, trimmed from everything ``GET /__unit/routes`` also publishes for
    the control plane's own reasons (scopes, idempotency, an example body).
    See :func:`routes`.

    This is a distinct, smaller type from
    :class:`vendorfake.core.kernel.unit.RouteInfo`, not a shadowing accident:
    the kernel's version is what the control plane actually publishes at
    ``GET /__unit/routes`` (with ``scopes``, ``idempotency``, ``example_body``
    and ``auth`` besides), and ``registry.RouteInfo`` is the six-field
    consumer-facing *projection* of one of its rows -- named ``RouteInfo`` in
    the spec this module implements, kept under that name here, and never
    re-exported next to the kernel's own so that an import naming
    ``vendorfake.registry.RouteInfo`` or
    ``vendorfake.core.kernel.unit.RouteInfo`` is always unambiguous about
    which shape it names.
    """

    method: str
    path: str
    operation_id: str | None
    capability: str
    summary: str | None
    internal: bool


def routes(vendor: str, profile: str = "full") -> tuple[RouteInfo, ...]:
    """Every route ``vendor``'s surface -- and its control plane -- serves.

    Built from the same table ``GET /__unit/routes`` answers, read through a
    real unit's :class:`~vendorfake.core.kernel.unit.ControlBinding` rather
    than reassembled by hand, so a row reported here is a row the unit will
    actually match. ``profile`` exists because building a unit needs one; the
    route table itself does not vary by profile -- every route the vendor
    declares is registered whether or not its capability is currently
    enabled, which is exactly what lets a disabled capability answer
    explicitly instead of 404 (see ``core/capability/registry.py``).
    """
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
    """A role name becomes this vendor's own capability name; anything else
    passes through, on the assumption that it is already one.

    ``roles`` is read with :func:`getattr` rather than as the attribute the
    ``VendorDefinition`` protocol declares, because a third-party vendor
    registered through the ``vendorfake.vendors`` entry-point group and built
    against v0.1.0 predates the property and simply does not have it. A bare
    ``definition.roles`` there is an ``AttributeError`` from inside a
    ``create_unit`` call the caller cannot connect to anything -- which is a
    worse failure than the one it is standing in for, and it fires even when
    the caller asked for no role at all. See ``CHANGELOG.md``'s **Breaking
    changes**: the fix is for the vendor to implement ``roles``, and this is
    what makes the intervening failure legible.

    A vendor that maps no roles still cannot answer a request *for* one, so
    that case is a ``ValueError`` naming the vendor and the role rather than a
    silent pass-through: ``capabilities=["auth"]`` against such a vendor would
    otherwise be read as a request for a capability literally called ``auth``
    and resolve to whatever profile happens to be a superset of it -- an
    answer that looks like it worked.
    """
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
    ``translated``, ties broken by name -- or ``None`` when no shipped
    profile qualifies, in which case the caller falls back to ``full`` plus
    an absolute capability list through the environment layer."""
    wanted = frozenset(translated)
    candidates = [profile for profile in _profiles_of(definition) if wanted <= frozenset(profile.capabilities)]
    if not candidates:
        return None
    chosen = min(candidates, key=lambda profile: (len(profile.capabilities), profile.name))
    return chosen.name


def resolve_vendor(name: str) -> VendorDefinition:
    """Load the vendor called ``name``.

    Raises ``ValueError`` -- not a ``UnitError`` -- because this happens before
    a unit exists and therefore before there is a vendor to shape an error
    with. A caller at the edge turns it into whatever its own surface needs.
    """
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
            # Exactly one vendor is installed, so there is no choice to make and
            # forcing the caller to name it would be ceremony. Two or more is a
            # genuine ambiguity and is refused.
            return resolve_vendor(offered[0])
        listing = ", ".join(offered) if offered else "(none installed)"
        raise ValueError(
            f"create_unit needs a vendor: pass vendor=..., or set {VENDOR_ENV_VAR} in the env mapping. "
            f"Available: {listing}"
        )
    if isinstance(vendor, str):
        return resolve_vendor(vendor)
    return vendor


def create_unit(
    *,
    vendor: str | VendorDefinition | None = None,
    profile: str | None = None,
    capabilities: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    sink: DeliverySink | None = None,
    logger: Logger | None = None,
    framework_answered: Callable[[], int] | None = None,
) -> Unit:
    """Build and start a unit. The single constructor.

    The order is fixed and is the reason this function exists rather than being
    inlined into every caller:

    1. resolve the vendor, because the profile directory and the retry defaults
       are properties of it;
    2. resolve ``capabilities`` into a profile name, when given (see below);
    3. load the profile with ``vendor.retry_defaults`` as ``defaults`` -- i.e.
       merged **under** the profile document, which is itself under the
       environment layer -- so a profile can override a vendor default and an
       operator can override both;
    4. construct the unit -- with the control plane, which is where the
       capability-declaration, retry-schedule and dead-chaos-rule assertions
       live;
    5. start it, which hydrates the store from the seed document.

    ``env`` is a plain mapping and defaults to empty. Pass ``os.environ``
    explicitly if that is what you mean.

    ``capabilities``, when given, is resolved instead of ``profile`` --
    passing both is a ``ValueError``, because the two are two different
    answers to "which profile" and a caller who supplied both cannot have
    meant for one to be ignored. An empty ``capabilities=[]`` is a
    ``ValueError`` too, rather than "no request": the empty set is a subset
    of every profile's capabilities, so resolving it the way any other
    request resolves would silently pick the smallest shipped profile, which
    is the one reading of an empty list a caller almost certainly did not
    intend. Pass ``capabilities=None`` (the default) to mean no request at
    all. Each name is either a role
    (:data:`ROLE_NAMES` -- ``auth``, ``orders``, ``webhooks``, ``chaos``),
    translated through ``vendor.roles`` into this vendor's own capability
    name, or already one of this vendor's own capability names, used as
    given. The translated set then picks the **narrowest shipped profile
    that is a superset of it** (fewest capabilities, ties broken by name);
    when no shipped profile qualifies, the unit starts on ``full`` with the
    set applied as an absolute list through the same ``VENDORFAKE_CAPABILITIES``
    layer an operator would use. Either way, ``GET /__unit/info`` reports
    the original request under ``requested_capabilities`` alongside whichever
    profile it resolved to, so a consumer can confirm what was asked for and
    not merely what was answered.

    ``framework_answered`` is the transport adapter's tripwire, reported by
    ``GET /__unit/health``: a count of requests a web framework answered by
    itself instead of handing to the unit. It is threaded through here rather
    than read from a global because the counter has to exist *before* the unit
    does -- the control plane closes over it at construction -- and because the
    only process that can read it is the one serving, which is why the number
    is on the wire at all. ``None``, the default, reports 0, which is the true
    answer for a unit with no framework in front of it rather than a stub.
    """
    environ: Mapping[str, str] = {} if env is None else env
    definition = _pick(vendor, environ)

    requested: tuple[str, ...] | None = None
    resolved_profile = profile
    if capabilities is not None:
        if profile is not None:
            raise ValueError(
                "create_unit(capabilities=..., profile=...) were both given; they are two different "
                "answers to which profile to start. Name the profile you want, or name the capabilities "
                "and let resolution choose one -- not both."
            )
        if len(capabilities) == 0:
            raise ValueError(
                "create_unit(capabilities=[]) is ambiguous. An empty set is a subset of every profile's "
                "capabilities, so resolving it the way a non-empty request resolves would silently start "
                "the smallest shipped profile -- almost certainly not what an empty list was meant to ask "
                "for. Pass capabilities=None (or omit the argument) to mean 'no capability request', "
                "profile=... to name a profile directly, or a non-empty list to request specific "
                "capabilities or roles."
            )
        requested = tuple(capabilities)
        translated = _translate_capability_names(definition, requested)
        matched = _narrowest_profile_for(definition, translated)
        if matched is not None:
            resolved_profile = matched
        else:
            resolved_profile = "full"
            environ = {**environ, "VENDORFAKE_CAPABILITIES": ",".join(translated)}

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
        # Every unit built through this function has a control plane. The
        # constructor keeps it optional so a kernel test can build a unit with
        # a vendor surface and nothing else -- but a unit a *consumer* is given
        # without `/__unit/*` is a unit they cannot drive, and there is exactly
        # one place to decide that.
        control_routes=functools.partial(control_plane_routes, framework_answered=framework_answered),
    )
    unit.start()
    return unit
