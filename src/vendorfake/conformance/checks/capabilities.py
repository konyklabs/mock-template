"""C02, C03, C11 -- capabilities are real, explicit, and completely declared.

The three contracts are one idea seen from three sides. C02: the route table
and the capability table describe the same unit. C03: switching a capability
off changes what a consumer is *told*, not merely what happens. C11: the core
never gates on something the vendor was silent about, because silence and
"switched off" are indistinguishable to ``is_enabled`` and the difference is
the whole reason a consumer trusts a profile.
"""

from __future__ import annotations

from typing import Any

from vendorfake.conformance.env import CheckEnv, ancestors
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import Requires, require

__all__ = [
    "capability_declaration_is_complete",
    "disabled_capability_answers_explicitly",
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
        "With a capability off, its routes answer capability_disabled -- not 404 -- and the body "
        "names the capability; re-enabling it with its prerequisites restores the route."
    ),
    requires=Requires(surface_route=True),
)
def disabled_capability_answers_explicitly(env: CheckEnv) -> str:
    rows = env.capabilities()
    original = [row.name for row in rows if row.enabled]
    table = env.routes()
    probed: list[str] = []
    behavior_only: list[str] = []
    try:
        for row in rows:
            route = next(
                (item for item in table if not item.internal and item.capability == row.name),
                None,
            )
            if route is None:
                if row.kind == "behavior":
                    behavior_only.append(row.name)
                continue

            off = [
                name
                for name in original
                if name != row.name and not name.startswith(f"{row.name}.") and row.name not in ancestors(name)
            ]
            env.set_capabilities(off)
            refused = env.client.call(route.method, route.probe_path, json_body={})

            require(
                refused.status != 404,
                f"{row.name}: with the capability off, {route.key} answered 404. A consumer cannot "
                f"tell that apart from 'this vendor has no such endpoint', so a profile becomes "
                f"indistinguishable from a typo. The capability gate runs at step 2 of "
                f"core/kernel/unit.py::_run_pipeline, before anything can produce a not-found.",
            )
            require(
                refused.error_kind == _DISABLED,
                f"{row.name}: with the capability off, {route.key} answered "
                f"{refused.status} with x-unit-error={refused.error_kind!r}, expected "
                f"{_DISABLED!r}. Raise UnitErrorKind.CAPABILITY_DISABLED from "
                f"core/capability/registry.py::assert_enabled and let the vendor shaper turn it "
                f"into its own wire format.",
            )
            require(
                row.name in refused.text,
                f"{row.name}: the refusal body does not name the disabled capability, so the "
                f"message cannot tell a consumer what to switch on. The detail built in "
                f"core/capability/registry.py::assert_enabled names it; check the vendor's "
                f"ErrorShaper is not discarding the detail.",
            )

            back_on = sorted({*original, row.name, *row.requires, *ancestors(row.name)})
            env.set_capabilities(back_on)
            restored = env.client.call(route.method, route.probe_path, json_body={})
            require(
                restored.error_kind != _DISABLED,
                f"{row.name}: still reported {_DISABLED!r} after being enabled together with its "
                f"prerequisites {sorted(set(row.requires) | set(ancestors(row.name)))}. Either "
                f"`requires` is incomplete, or blocked_by is not following the dotted parent.",
            )
            probed.append(row.name)
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
    tail = f"; behavior-only (no surface, correctly): {', '.join(behavior_only)}" if behavior_only else ""
    return f"probed {len(probed)}: {', '.join(probed)}{tail}"


@check(
    id="C11",
    name="capabilities: every core-gated capability is declared or explicitly excused",
    asserts=(
        "Every capability the core itself gates on is either declared by the vendor or listed in "
        "not_supported with a prose reason -- never both, never neither, never a name nothing gates."
    ),
)
def capability_declaration_is_complete(env: CheckEnv) -> str:
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
