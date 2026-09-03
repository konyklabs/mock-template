"""C24, C25 -- the discovery surface stream C added is honest about itself.

C24: ``vendor.roles`` (published at ``GET /__unit/info``) is what
``registry.create_unit(capabilities=[...])`` translates a role name through
before it ever reaches a profile. A vendor that forgot to map one, or mapped
one to a capability it does not declare, would make that resolution raise or
silently pick nothing -- and nothing about *serving requests correctly* would
ever notice, because no route's capability is named ``auth`` or ``orders``;
those are the neutral vocabulary the mapping exists to translate.

C25: the profile-name contract is a promise about six specific, hand-written
JSON files that a consumer selects by *name* -- ``profile="oauth-only"``
should mean the same shape of thing whichever vendor answers it. Read the six
shipped profiles for all three vendors (the derivation this check's DESIGN
NOTES below record) and the contract that actually holds is narrower than the
one a first reading of the spec suggests:

* ``oauth-only`` enables role ``auth`` and role ``chaos`` (and nothing says it
  enables only those two -- a vendor whose OAuth surface needs a third
  capability to function is not thereby non-conformant).
* ``orders-only`` enables role ``orders`` and -- this is the part every
  shipped profile's own summary states outright ("No OAuth dance ... "
  authenticate with a seeded token") -- does NOT enable role ``auth``. All
  three vendors ship this profile with their login/token surface switched off
  on purpose, pinned by each vendor's own unit tests
  (``tests/unit/toast/test_profiles.py::test_orders_only_has_no_login_...``,
  Square's ``test_orders_only_serves_no_oauth_surface``); a contract that
  required ``orders-only`` to enable role ``auth`` would be asking every
  vendor to break a profile its own test suite already pins.
* ``no-chaos`` keeps ``chaos`` (role) enabled and switches off only
  ``webhooks.chaos``: what the name promises is *no delivery chaos*, and
  Square's own ``test_chaos_is_on_everywhere_except_the_profile_named_for_having_it_off``
  names this distinction directly -- ``no-faults`` is the profile that means
  what ``no-chaos`` sounds like it should.
* ``no-faults`` switches off both ``chaos`` (role) and ``webhooks.chaos``.

This is why the check is keyed on the profile's *name* (``env.profile``)
rather than on a formula applied uniformly to the whole matrix: the contract
is genuinely a naming convention, checkable only against what a name is
supposed to promise, not a set relation every profile satisfies alike.
"""

from __future__ import annotations

from typing import Any, cast

from vendorfake.conformance.env import CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import require
from vendorfake.core.capability.gates import CoreCapability

__all__ = ["capability_roles_are_completely_mapped", "the_profile_name_contract_holds"]

ROLE_NAMES: tuple[str, ...] = ("auth", "orders", "webhooks", "chaos")
"""The fixed, vendor-neutral role vocabulary. Not imported from
``vendorfake.registry`` -- the conformance package may import only the core
and itself (``tools/boundary.toml``), and this vocabulary is small and stable
enough to restate here the way ``CoreCapability`` already is."""


def _roles(env: CheckEnv) -> dict[str, Any]:
    vendor_block = env.info().get("vendor")
    ok = isinstance(vendor_block, dict) and isinstance(vendor_block.get("roles"), dict)
    require(
        ok,
        "GET /__unit/info does not publish vendor.roles as an object. Add VendorDefinition.roles and publish "
        "it under the 'vendor' block in core/control/plane.py::info.",
    )
    # `require` above is this package's assertion -- never a bare `assert`, which `python -O` would strip
    # (see conformance/types.py's module docstring). `cast` is a compile-time-only annotation and raises
    # nothing, so it narrows for the type checker without duplicating that enforcement.
    return cast("dict[str, Any]", cast("dict[str, Any]", vendor_block)["roles"])


@check(
    id="C24",
    name="discovery: every vendor maps all four capability roles to a declared capability",
    asserts=(
        "GET /__unit/info publishes vendor.roles with exactly the four role names auth, orders, webhooks "
        "and chaos as keys, each mapped to a capability this vendor actually declares at "
        "GET /__unit/capabilities."
    ),
)
def capability_roles_are_completely_mapped(env: CheckEnv) -> str:
    roles = _roles(env)
    missing = [name for name in ROLE_NAMES if name not in roles]
    require(
        not missing,
        f"vendor.roles is missing {missing}; every vendor must map all four roles "
        f"({', '.join(ROLE_NAMES)}) so registry.create_unit(capabilities=[...]) can translate any of them.",
    )
    extra = sorted(set(roles) - set(ROLE_NAMES))
    require(
        not extra,
        f"vendor.roles names {extra}, outside the declared role vocabulary ({', '.join(ROLE_NAMES)}). A "
        f"fifth role is added to the vocabulary everywhere at once, not invented by one vendor.",
    )
    declared = {row.name for row in env.capabilities()}
    undeclared = {role: str(roles[role]) for role in ROLE_NAMES if str(roles[role]) not in declared}
    require(
        not undeclared,
        f"vendor.roles maps {undeclared} to a capability this vendor never declares (declared: "
        f"{sorted(declared)}). A role must resolve to a name GET /__unit/capabilities actually lists.",
    )
    return f"roles mapped: {roles}"


@check(
    id="C25",
    name="discovery: the profile-name contract holds for the profile this unit was built on",
    asserts=(
        "oauth-only enables role auth and role chaos; orders-only enables role orders and does NOT enable "
        "role auth; no-chaos keeps role chaos enabled and switches off only webhooks.chaos; no-faults "
        "switches off both role chaos and webhooks.chaos. See this module's docstring for the derivation."
    ),
)
def the_profile_name_contract_holds(env: CheckEnv) -> str:
    roles = _roles(env)
    for role in ROLE_NAMES:
        require(role in roles, f"vendor.roles has no entry for role {role!r}; C24 names the same gap.")
    declared = {row.name for row in env.capabilities()}
    enabled = env.enabled_capability_names()

    def capability_of(role: str) -> str:
        name = str(roles[role])
        require(name in declared, f"vendor.roles maps role {role!r} to {name!r}, which this vendor never declares.")
        return name

    profile = env.profile
    webhooks_chaos = CoreCapability.WEBHOOKS_CHAOS.value

    if profile == "oauth-only":
        wanted = {capability_of("auth"), capability_of("chaos")}
        require(
            wanted <= enabled,
            f"profile 'oauth-only' must enable {sorted(wanted)} (role auth, role chaos); enabled={sorted(enabled)}.",
        )
    elif profile == "orders-only":
        orders_cap = capability_of("orders")
        require(
            orders_cap in enabled,
            f"profile 'orders-only' must enable {orders_cap!r} (role orders); enabled={sorted(enabled)}.",
        )
        auth_cap = capability_of("auth")
        require(
            auth_cap not in enabled,
            f"profile 'orders-only' enables {auth_cap!r} (role auth). Every shipped profile of this name "
            f"promises 'no OAuth dance -- authenticate with a seeded token'; it must not also register a "
            f"live login/token surface.",
        )
    elif profile in ("no-chaos", "no-faults"):
        chaos_cap = capability_of("chaos")
        if webhooks_chaos in declared:
            require(
                webhooks_chaos not in enabled,
                f"profile {profile!r} enables {webhooks_chaos!r}; both 'no-chaos' and 'no-faults' switch "
                f"delivery-scope chaos off by name.",
            )
        if profile == "no-chaos":
            require(
                chaos_cap in enabled,
                f"profile 'no-chaos' must keep {chaos_cap!r} (role chaos) enabled -- the name switches off "
                f"only delivery chaos; 'no-faults' is the profile that switches off request-scope faults too.",
            )
        else:
            require(
                chaos_cap not in enabled,
                f"profile 'no-faults' must switch off {chaos_cap!r} (role chaos) as well as delivery chaos.",
            )
    return f"profile {profile!r}: enabled={sorted(enabled)}, roles={roles}"
