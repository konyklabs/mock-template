"""C02, C03, C11, C28 -- capabilities are real, explicit, completely declared, and switchable.

The three contracts are one idea seen from three sides. C02: the route table
and the capability table describe the same unit. C03: switching a capability
off changes what a consumer is *told*, not merely what happens. C11: the core
never gates on something the vendor was silent about, because silence and
"switched off" are indistinguishable to ``is_enabled`` and the difference is
the whole reason a consumer trusts a profile.
"""

from __future__ import annotations

from typing import Any

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv, ancestors
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceSkip, Requires, require

__all__ = [
    "capability_declaration_is_complete",
    "disabled_capability_answers_explicitly",
    "every_capability_verb_has_its_effect",
    "routes_and_capabilities_describe_one_unit",
]

CONTROL_CAPABILITY = "__control"
"""The control plane's own capability. Filtered out of every listing by the
registry -- a consumer can never switch off the endpoint they would use to
switch things back on -- so a route naming it is not an orphan."""

_DISABLED = "capability_disabled"


@check(
    id="C02",
    name="capabilities: every route is owned, every surface capability is used",
    asserts=(
        "Every non-internal route names a declared capability; no surface capability owns zero "
        "routes; no behavior capability owns any."
    ),
)
def routes_and_capabilities_describe_one_unit(env: CheckEnv) -> str:
    rows = env.capabilities()
    declared = {row.name for row in rows}
    table = env.routes()
    problems: list[str] = []

    for route in table:
        if route.internal or route.capability == CONTROL_CAPABILITY:
            continue
        if route.capability not in declared:
            problems.append(
                f"route {route.key} names capability {route.capability!r}, which the vendor never "
                f"declares. Add a CapabilityDecl for it to VendorDefinition.capabilities, or point "
                f"the route at an existing one -- an undeclared name can never be enabled, so the "
                f"route is unreachable on every profile."
            )

    owned: dict[str, list[str]] = {}
    for route in table:
        if route.internal:
            continue
        owned.setdefault(route.capability, []).append(route.key)

    for row in rows:
        routes_here = owned.get(row.name, [])
        if row.kind == "surface" and not routes_here:
            problems.append(
                f"capability {row.name!r} is declared kind='surface' and owns no route. A surface "
                f"capability is one a consumer meets as endpoints; one with none has no observable "
                f"meaning. Give it routes, or declare it kind='behavior'."
            )
        if row.kind == "behavior" and routes_here:
            problems.append(
                f"capability {row.name!r} is declared kind='behavior' and owns {routes_here}. A "
                f"behavior capability gates conduct and has no surface of its own; a consumer "
                f"switching it off would silently lose endpoints. Declare it kind='surface'."
            )
        published = set(row.routes)
        if published != set(routes_here):
            problems.append(
                f"capability {row.name!r} publishes routes {sorted(published)} at "
                f"/__unit/capabilities but owns {sorted(routes_here)} in the route table. The two "
                f"views are built from one Route tuple in core/capability/registry.py::view; they "
                f"cannot legitimately disagree."
            )

    require(not problems, "\n".join(problems))
    surface = sum(1 for row in rows if row.kind == "surface")
    behavior = sum(1 for row in rows if row.kind == "behavior")
    return (
        f"{len(table)} routes over {len(rows)} capabilities ({surface} surface, {behavior} behavior); "
        f"every vendor route owned, every surface capability used"
    )


@check(
    id="C03",
    name="capabilities: a disabled capability answers explicitly, never 404",
    asserts=(
        "With a capability off, EVERY route it owns answers capability_disabled -- not 404 -- and "
        "the body names the capability; re-enabling it with its prerequisites restores them; and "
        "no vendor route escapes the gate by being internal or by naming the control capability."
    ),
    requires=Requires(surface_route=True),
)
def disabled_capability_answers_explicitly(env: CheckEnv) -> str:
    """EVERY route of every capability, and then every route once more.

    It used to be the *first* route of each capability -- ``next(item for item
    in table if item.capability == row.name)`` -- which on the shipped vendor
    is four routes out of eighteen. Skipping the gate for a single operation
    left the suite entirely green: the token endpoint answered on a profile
    that declares OAuth off, and the only reason the coarser version of that
    mutation was caught at all is that the first route of one capability
    happened to be the one it hit. That is luck in a vendor's route ordering,
    not coverage.

    The second half is the complement, and it catches what per-capability
    iteration structurally cannot: a route that belongs to no capability a
    consumer can switch off. A vendor route marked ``internal=True`` skips the
    gate at step 1 of the pipeline, and one naming the control plane's own
    capability is never listed at all -- both are invisible to a loop over
    declared capabilities, and both mean an endpoint no profile can remove.
    """
    rows = env.capabilities()
    original = [row.name for row in rows if row.enabled]
    table = env.routes()
    probed: list[str] = []
    probed_routes: set[str] = set()
    behavior_only: list[str] = []
    try:
        for row in rows:
            owned = [item for item in table if not item.internal and item.capability == row.name]
            if not owned:
                if row.kind == "behavior":
                    behavior_only.append(row.name)
                continue

            off = [
                name
                for name in original
                if name != row.name and not name.startswith(f"{row.name}.") and row.name not in ancestors(name)
            ]
            env.set_capabilities(off)
            for route in owned:
                refused = env.client.call(route.method, route.probe_path, json_body={})

                require(
                    refused.status != 404,
                    f"{row.name}: with the capability off, {route.key} answered 404. A consumer "
                    f"cannot tell that apart from 'this vendor has no such endpoint', so a profile "
                    f"becomes indistinguishable from a typo. The capability gate runs at step 2 of "
                    f"core/kernel/unit.py::_run_pipeline, before anything can produce a not-found.",
                )
                require(
                    refused.error_kind == _DISABLED,
                    f"{row.name}: with the capability off, {route.key} answered "
                    f"{refused.status} with x-unit-error={refused.error_kind!r}, expected "
                    f"{_DISABLED!r}. Every route the capability owns passes the SAME gate; one that "
                    f"reaches its handler is an endpoint the profile cannot switch off. Raise "
                    f"UnitErrorKind.CAPABILITY_DISABLED from core/capability/registry.py::"
                    f"assert_enabled and let the vendor shaper turn it into its own wire format.",
                )
                require(
                    row.name in refused.text,
                    f"{row.name}: the refusal body for {route.key} does not name the disabled "
                    f"capability, so the message cannot tell a consumer what to switch on. The "
                    f"detail built in core/capability/registry.py::assert_enabled names it; check "
                    f"the vendor's ErrorShaper is not discarding the detail.",
                )

            back_on = sorted({*original, row.name, *row.requires, *ancestors(row.name)})
            env.set_capabilities(back_on)
            for route in owned:
                restored = env.client.call(route.method, route.probe_path, json_body={})
                require(
                    restored.error_kind != _DISABLED,
                    f"{row.name}: {route.key} still reported {_DISABLED!r} after the capability was "
                    f"enabled together with its prerequisites "
                    f"{sorted(set(row.requires) | set(ancestors(row.name)))}. Either `requires` is "
                    f"incomplete, or blocked_by is not following the dotted parent.",
                )
                probed_routes.add(route.key)
            probed.append(f"{row.name}({len(owned)})")
    finally:
        # The reference has no restore, so one failed assertion leaves the unit
        # with capabilities off and poisons every later check that shares it.
        # Each check here owns its unit, but a restore is still right: a check
        # that leaves a unit in a state it did not announce is a check whose
        # own evidence cannot be trusted.
        env.set_capabilities(original)

    require(
        probed,
        "no capability owned a route to probe, so this check proved nothing. Either the vendor "
        "declares no surface capability, or every route is internal.",
    )

    ungated = sorted(
        route.key for route in table if not route.path.startswith(CONTROL_PREFIX) and route.key not in probed_routes
    )
    require(
        not ungated,
        f"{ungated} are vendor routes that no capability switched off. A route is exempt from the "
        f"gate in exactly two ways, and both are defects outside the control plane: "
        f"``internal=True`` short-circuits the whole pipeline at step 1 of "
        f"core/kernel/unit.py::_run_pipeline, and naming {CONTROL_CAPABILITY!r} points the route at "
        f"the one capability a consumer can never disable. Either way the endpoint is live on every "
        f"profile and nothing in the capability table says so. Give it a declared vendor "
        f"capability. (Probed: {sorted(probed_routes)}.)",
    )

    tail = f"; behavior-only (no surface, correctly): {', '.join(behavior_only)}" if behavior_only else ""
    return (
        f"probed {len(probed_routes)} routes across {len(probed)} capabilities "
        f"({', '.join(probed)}); every vendor route gated{tail}"
    )


@check(
    id="C11",
    name="capabilities: every core-gated capability is declared or explicitly excused",
    asserts=(
        "Every capability the core itself gates on is either declared by the vendor or listed in "
        "not_supported with a prose reason -- never both, never neither, never a name nothing "
        "gates. Discriminating over --base-url; a Python unit that violates it does not start."
    ),
)
def capability_declaration_is_complete(env: CheckEnv) -> str:
    """WHAT THIS CONTRACT DISCRIMINATES, stated rather than implied.

    Against a unit built by *this* core it discriminates a **document**, and no
    more. ``core/capability/gates.py::assert_capability_declarations`` raises on
    exactly this predicate at construction, so a Python unit that omits a
    declaration never starts: every contract reports ERROR, this one included,
    and its body never runs. A unit that starts always passes it.

    That is not a reason to delete it. Its value is over ``--base-url``, against
    an implementation in another language with no such startup assertion in
    front of it -- which is the mode a second vendor, or a container somebody
    else built, will actually be checked in. There the predicate is the only
    thing standing between a silently-off behaviour and a green report, because
    ``is_enabled`` cannot tell "you never told me you have this" from "it is
    switched off".

    Written down here because the alternative is a reader concluding from a
    green line that something was proved about this unit's declarations, when
    what was proved is that the unit started.
    """
    document = env.capabilities_document()
    require(
        "not_supported" in document,
        "GET /__unit/capabilities does not publish the vendor's not_supported map. Silence about a "
        "capability the core gates on is what lets a behaviour switch itself off invisibly: "
        "is_enabled() on an undeclared name returns False, which is also what 'switched off' looks "
        "like. Publish VendorDefinition.not_supported from core/control/plane.py::capabilities_get.",
    )
    require(
        "core_gates" in document,
        "GET /__unit/capabilities does not publish core_gates. The list of capabilities the CORE "
        "gates on is data in core/capability/gates.py::CORE_GATED_CAPABILITIES precisely so that "
        "this check asserts against the core's own declaration rather than against a copy kept in "
        "step by hand. Publish it.",
    )

    gates: list[dict[str, Any]] = list(document["core_gates"])
    gated = {str(gate["capability"]): str(gate.get("gated_at", "(unstated)")) for gate in gates}
    declared = {str(row["name"]) for row in document["capabilities"]}
    excused = {str(name): str(reason) for name, reason in dict(document["not_supported"]).items()}
    problems: list[str] = []

    for name, gated_at in sorted(gated.items()):
        if name in declared and name in excused:
            problems.append(
                f"{name!r} is both declared and listed in not_supported. A vendor either has a "
                f"capability or does not; remove one of the two in the vendor definition."
            )
        elif name not in declared and name not in excused:
            problems.append(
                f"the core gates on {name!r} at {gated_at}, and this vendor neither declares it nor "
                f"lists it in not_supported. Add a CapabilityDecl for it, or add it to "
                f"VendorDefinition.not_supported with a one-line reason -- silence makes the "
                f"behaviour permanently off with nothing anywhere saying so."
            )

    for name, reason in sorted(excused.items()):
        if name not in gated:
            problems.append(
                f"not_supported names {name!r}, which the core does not gate on, so excusing it "
                f"changes nothing and a typo here would be silent. Core-gated: {sorted(gated)}."
            )
        elif not reason.strip():
            problems.append(
                f"not_supported[{name!r}] carries no reason. An expected absence is recorded with "
                f"its justification: 'this vendor has no such mechanism' and 'it is on the roadmap' "
                f"are different facts and a bare name loses both."
            )

    require(not problems, "\n".join(problems))
    covered = sorted(gated.keys() & declared)
    return (
        f"the core gates on {len(gated)} ({', '.join(sorted(gated))}): "
        f"{len(covered)} declared, {len(excused)} excused with reasons"
    )


# ---------------------------------------------------------------------------
# C28 -- all four verbs of POST /__unit/capabilities, on every capability.
# ---------------------------------------------------------------------------


def _singly_toggleable(env: CheckEnv) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Every capability this check may switch off alone, each with one owned
    route -- plus the ones it may not, with the reason, for the evidence line.

    Enabled, surface, owning an enabled route, with no enabled dotted child
    and no enabled capability requiring it: switching such a capability off
    changes exactly one thing, so every verb's effect is attributable to that
    verb. The excluded ones are named rather than dropped, because a reader of
    a green line is owed the list of what was not asked and why.
    """
    rows = env.capabilities()
    enabled = [row for row in rows if row.enabled]
    table = [route for route in env.routes() if not route.internal]
    eligible: list[tuple[str, str, str]] = []
    excluded: list[str] = []
    for row in enabled:
        if row.kind != "surface":
            continue
        blockers = [
            other.name for other in enabled if other.name.startswith(f"{row.name}.") or row.name in other.requires
        ]
        if blockers:
            excluded.append(f"{row.name} (entangled with enabled {', '.join(sorted(blockers))})")
            continue
        owned = next((route for route in table if route.capability == row.name), None)
        if owned is None:
            excluded.append(f"{row.name} (owns no enabled route)")
            continue
        eligible.append((row.name, owned.method, owned.probe_path))
    return eligible, excluded


@check(
    id="C28",
    name="control plane: set, delta, enable and disable each change what the unit does",
    asserts=(
        "On EVERY singly-toggleable capability: each of the four verbs of POST /__unit/capabilities "
        "has its declared effect, observed both in the capability table and at a route the "
        "capability owns; and the verbs compose in the declared order, set before enable."
    ),
    requires=Requires(surface_route=True),
)
def every_capability_verb_has_its_effect(env: CheckEnv) -> str:
    """Three verbs nothing exercised, and the one axis the first fix sampled.

    ``POST /__unit/capabilities`` takes ``set``, ``delta``, ``enable`` and
    ``disable``. Every contract that toggled a capability -- C03, C14, C18 --
    restored it with ``set``, so making ``enable`` a no-op left the matrix
    green and a foreign implementation could stub three of four verbs and be
    certified (konyklabs/roadmap#10, N-5; tracked as konyklabs/roadmap#15).

    The first version of this check asked one capability -- the first
    eligible -- which is the same defect this branch's review named in C17,
    C24 and C26: sampling where the contract quantifies. The registry is
    shared, but ``requires`` and dotted children are per-capability data, so a
    registry special-casing one NAME escapes a one-capability probe entirely.
    Every singly-toggleable capability is asked now, and a failure names the
    capability and the verb.

    Every step observes two things: what the table says and what a route
    does. A verb that updated the table and not the gate, or the gate and not
    the table, is a verb whose answer cannot be trusted either way.
    """
    eligible, excluded = _singly_toggleable(env)
    if not eligible:
        raise ConformanceSkip(
            f"profile {env.profile!r} enables no surface capability that can be switched off on its "
            f"own (one with routes, no enabled dotted child and no enabled dependent): "
            f"{'; '.join(excluded) or 'nothing is enabled at all'}"
        )
    original = [row.name for row in env.capabilities() if row.enabled]
    problems: list[str] = []
    exercised: list[str] = []

    def observe(name: str, method: str, path: str, verb: str, body: dict[str, Any], expect_on: bool) -> None:
        answered = env.client.call("POST", f"{CONTROL_PREFIX}capabilities", json_body=body)
        if answered.status != 200:
            problems.append(
                f"{name}: POST /__unit/capabilities {body} answered {answered.status}: "
                f"{answered.text[:200]}. Every one of the four verbs is contract; a unit that "
                f"refuses one has three."
            )
            return
        reported = {str(row["name"]): bool(row["enabled"]) for row in answered.json()["capabilities"]}
        listed = env.capability_enabled(name)
        if not (reported.get(name) is expect_on and listed is expect_on):
            problems.append(
                f"{name}: after {verb} {body}, enabled={reported.get(name)} in the POST's own answer "
                f"and enabled={listed} at GET /__unit/capabilities, expected {expect_on}. The verb is "
                f"applied in core/control/plane.py::capabilities_post against the live registry; both "
                f"views are built from it and cannot disagree with it or with each other. A verb that "
                f"works for some capabilities and not this one is a registry special-casing a name -- "
                f"the defect a one-capability probe of this contract could never see."
            )
            return
        probe = env.client.call(method, path, json_body={})
        gated = probe.error_kind == _DISABLED
        if gated is expect_on:
            problems.append(
                f"{name}: after {verb} {body}, {method} {path} answered {probe.status} "
                f"x-unit-error={probe.error_kind!r}: the gate at step 2 of "
                f"core/kernel/unit.py::_run_pipeline {'still refuses' if gated else 'no longer refuses'} "
                f"the route while the table says enabled={expect_on}. A verb that moves the table and "
                f"not the gate has changed a document, not the unit."
            )

    try:
        for name, method, path in eligible:
            without = [item for item in original if item != name]
            observe(name, method, path, "disable", {"disable": [name]}, False)
            observe(name, method, path, "enable", {"enable": [name]}, True)
            observe(name, method, path, "delta", {"delta": f"-{name}"}, False)
            observe(name, method, path, "delta", {"delta": f"+{name}"}, True)
            observe(name, method, path, "set", {"set": without}, False)
            observe(name, method, path, "set", {"set": original}, True)
            # Order is contract: set replaces, THEN enable adds. Read the other
            # way round the same body would leave the capability off.
            observe(name, method, path, "set+enable", {"set": without, "enable": [name]}, True)
            exercised.append(name)
    finally:
        env.set_capabilities(original)
    require(not problems, "\n".join(problems))
    tail = f"; not singly toggleable: {'; '.join(excluded)}" if excluded else ""
    return (
        f"all four verbs plus the set+enable ordering exercised on {len(exercised)} "
        f"capabilit{'y' if len(exercised) == 1 else 'ies'} ({', '.join(exercised)}); table and gate "
        f"agreed at every step{tail}"
    )
