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

import importlib
import importlib.util
from collections.abc import Mapping
from importlib.metadata import entry_points

from vendorfake.core.config.profile import load_profile
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.types import Logger, VendorDefinition
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.webhooks.sink import DeliverySink

__all__ = [
    "ENTRY_POINT_GROUP",
    "VENDOR_ENV_VAR",
    "available_vendors",
    "create_unit",
    "resolve_vendor",
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
    "square": "vendorfake.square:VENDOR",
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
    env: Mapping[str, str] | None = None,
    sink: DeliverySink | None = None,
    logger: Logger | None = None,
) -> Unit:
    """Build and start a unit. The single constructor.

    The order is fixed and is the reason this function exists rather than being
    inlined into every caller:

    1. resolve the vendor, because the profile directory and the retry defaults
       are properties of it;
    2. load the profile with ``vendor.retry_defaults`` as ``defaults`` -- i.e.
       merged **under** the profile document, which is itself under the
       environment layer -- so a profile can override a vendor default and an
       operator can override both;
    3. construct the unit -- with the control plane, which is where the
       capability-declaration, retry-schedule and dead-chaos-rule assertions
       live;
    4. start it, which hydrates the store from the seed document.

    ``env`` is a plain mapping and defaults to empty. Pass ``os.environ``
    explicitly if that is what you mean.
    """
    environ: Mapping[str, str] = {} if env is None else env
    definition = _pick(vendor, environ)
    loaded = load_profile(
        profile_dir=definition.profile_dir,
        name=profile,
        base_dir=definition.base_dir,
        env=environ,
        defaults=definition.retry_defaults,
    )
    unit = Unit(
        vendor=definition,
        config=loaded.config,
        seed=loaded.seed,
        sink=sink,
        logger=logger,
        # Every unit built through this function has a control plane. The
        # constructor keeps it optional so a kernel test can build a unit with
        # a vendor surface and nothing else -- but a unit a *consumer* is given
        # without `/__unit/*` is a unit they cannot drive, and there is exactly
        # one place to decide that.
        control_routes=control_plane_routes,
    )
    unit.start()
    return unit
