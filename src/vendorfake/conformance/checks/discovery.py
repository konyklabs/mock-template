"""C34, C35 -- the discovery surface is honest about itself.

C34: ``vendor.roles`` (at ``GET /__unit/info``) is what ``registry.create_unit(capabilities=[...])`` translates a role name through before it reaches a profile. A vendor that forgot to map one, or mapped it to an undeclared capability, would not be caught by anything about serving requests correctly, since no route names a capability ``auth`` or ``orders`` directly.

C35 is keyed on the profile's name (``env.profile``), not a formula applied uniformly to the matrix, because what each name promises differs:

* ``oauth-only`` enables role ``auth`` and role ``chaos`` (not necessarily only those two).
* ``orders-only`` enables role ``orders`` and does NOT enable role ``auth`` -- every shipped profile of this name authenticates with a seeded token instead of an OAuth dance.
* ``no-chaos`` keeps role ``chaos`` enabled and switches off only ``webhooks.chaos``.
* ``no-faults`` switches off both role ``chaos`` and ``webhooks.chaos``.

Separately, every vendor must ship all six profile names (``full``, ``oauth-only``, ``orders-only``, ``no-chaos``, ``no-faults``, ``chaos-demo``); ``GET /__unit/info`` publishes ``vendor.profiles`` (the file stems) so a check running against a single profile can still see the whole roster. Additional vendor-specific profiles are allowed.
"""

from __future__ import annotations

from typing import Any, cast

from vendorfake.conformance.env import CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import require
from vendorfake.core.capability.gates import CoreCapability

__all__ = ["capability_roles_are_completely_mapped", "the_profile_name_contract_holds"]

ROLE_NAMES: tuple[str, ...] = ("auth", "orders", "webhooks", "chaos")
"""The fixed, vendor-neutral role vocabulary, restated rather than imported since this package may import only the core and itself (``tools/boundary.toml``)."""

REQUIRED_PROFILE_NAMES: frozenset[str] = frozenset(
    {"full", "oauth-only", "orders-only", "no-chaos", "no-faults", "chaos-demo"}
)
"""The six profile names every vendor must ship -- a subset requirement, not an exact one."""


def _roles(env: CheckEnv) -> dict[str, Any]:
    vendor_block = env.info().get("vendor")
    ok = isinstance(vendor_block, dict) and isinstance(vendor_block.get("roles"), dict)
    require(
        ok,
        "GET /__unit/info does not publish vendor.roles as an object. Add VendorDefinition.roles and publish "
        "it under the 'vendor' block in core/control/plane.py::info.",
    )
    return cast("dict[str, Any]", cast("dict[str, Any]", vendor_block)["roles"])


def _shipped_profile_names(env: CheckEnv) -> list[str]:
    vendor_block = env.info().get("vendor")
    profiles = vendor_block.get("profiles") if isinstance(vendor_block, dict) else None
    ok = isinstance(profiles, list) and all(isinstance(name, str) for name in profiles)
    require(
        ok,
        "GET /__unit/info does not publish vendor.profiles as a list of strings. Add it beside vendor.roles "
        "in core/control/plane.py::info, reading ctx.vendor.profile_dir the same way registry._profiles_of "
        "does.",
    )
    return cast("list[str]", profiles)


@check(
    id="C34",
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
    id="C35",
    name="discovery: the profile-name contract holds for the profile this unit was built on",
    asserts=(
        "every vendor ships all six of full, oauth-only, orders-only, no-chaos, no-faults and chaos-demo; "
        "oauth-only enables role auth and role chaos; orders-only enables role orders and does NOT enable "
        "role auth; no-chaos keeps role chaos enabled and switches off only webhooks.chaos; no-faults "
        "switches off both role chaos and webhooks.chaos. See this module's docstring for the derivation."
    ),
)
def the_profile_name_contract_holds(env: CheckEnv) -> str:
    shipped = _shipped_profile_names(env)
    missing_profiles = sorted(REQUIRED_PROFILE_NAMES - set(shipped))
    require(
        not missing_profiles,
        f"this vendor's profile_dir is missing {missing_profiles} -- the profile-name contract requires "
        f"every vendor to ship all six of {sorted(REQUIRED_PROFILE_NAMES)} (see this module's docstring); "
        f"shipped: {sorted(shipped)}.",
    )
    roles = _roles(env)
    for role in ROLE_NAMES:
        require(role in roles, f"vendor.roles has no entry for role {role!r}; C34 names the same gap.")
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
