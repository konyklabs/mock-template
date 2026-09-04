"""What the core itself gates on, published as data. INVARIANT: silence is a
failure -- every :class:`CoreCapability` member is either declared by the
vendor or listed in ``VendorDefinition.not_supported`` with a reason, not
both. Three gates: ``chaos`` gates request-scope faults at one choke point;
``webhooks`` gates delivery existing at all, checked inside the listener
``attach`` registers; ``webhooks.chaos`` gates delivery-scope faults only,
and requires both ``webhooks`` and ``chaos``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from vendorfake.core.kernel.types import CapabilityDecl, UnitError, UnitErrorKind

__all__ = [
    "CORE_GATED_CAPABILITIES",
    "CapabilityDeclarationReport",
    "CoreCapability",
    "CoreGate",
    "assert_capability_declarations",
    "check_capability_declarations",
    "core_gated_names",
]


class CoreCapability(StrEnum):
    """Every capability the core itself gates on. Exactly these three."""

    CHAOS = "chaos"
    WEBHOOKS = "webhooks"
    WEBHOOKS_CHAOS = "webhooks.chaos"


@dataclass(frozen=True, slots=True)
class CoreGate:
    """One capability the core gates on, and where the gate is performed."""

    capability: CoreCapability
    #: Dotted path of the call site that performs the gate.
    gated_at: str
    #: What stops happening when it is off, in one sentence.
    effect: str
    #: Declaration kind this capability must carry; ``None`` leaves it to the vendor.
    expected_kind: str | None = None
    #: Capabilities a declaration of this one must list in ``requires``.
    required_prerequisites: tuple[str, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability.value,
            "gated_at": self.gated_at,
            "effect": self.effect,
        }


CORE_GATED_CAPABILITIES: tuple[CoreGate, ...] = (
    CoreGate(
        capability=CoreCapability.CHAOS,
        gated_at="vendorfake.core.chaos.selector.FaultSelector.select_request",
        effect="Request-scope faults are never armed, from any source: standing rules, in-band values, forced headers.",
        expected_kind="behavior",
    ),
    CoreGate(
        capability=CoreCapability.WEBHOOKS,
        gated_at="vendorfake.core.webhooks.dispatcher.WebhookDispatcher.attach",
        effect=(
            "The dispatcher's journal listener returns at once, so no event is ever mapped, prepared or delivered."
        ),
    ),
    CoreGate(
        capability=CoreCapability.WEBHOOKS_CHAOS,
        gated_at="vendorfake.core.chaos.selector.FaultSelector.select_webhook",
        effect="Delivery-scope faults are never armed: no duplication, reordering, dropped acknowledgement or delay.",
        expected_kind="behavior",
        required_prerequisites=(CoreCapability.WEBHOOKS.value, CoreCapability.CHAOS.value),
    ),
)
"""The gates, in reading order. Published as data, not a hand-kept list."""


def core_gated_names() -> tuple[str, ...]:
    """The gated capability names, in declaration order."""
    return tuple(gate.capability.value for gate in CORE_GATED_CAPABILITIES)


@dataclass(frozen=True, slots=True)
class CapabilityDeclarationReport:
    """The completeness check's result. Every field is a tuple of capability
    names except :attr:`problems`, the human-readable rendering."""

    #: Gated, but neither declared nor recorded as unsupported.
    undeclared: tuple[str, ...] = ()
    #: Both declared and recorded as unsupported.
    contradictory: tuple[str, ...] = ()
    ungated: tuple[str, ...] = ()
    unreasoned: tuple[str, ...] = ()
    wrong_kind: tuple[str, ...] = ()
    missing_prerequisite: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def check_capability_declarations(
    declared: Iterable[CapabilityDecl],
    not_supported: Mapping[str, str],
) -> CapabilityDeclarationReport:
    """Reconcile a vendor's declarations against what the core gates on. Pure:
    returns a report and raises nothing, so both the startup assertion and
    the conformance check can use it without catching an exception."""
    by_name = {decl.name: decl for decl in declared}
    gates = {gate.capability.value: gate for gate in CORE_GATED_CAPABILITIES}

    undeclared: list[str] = []
    contradictory: list[str] = []
    ungated: list[str] = []
    unreasoned: list[str] = []
    wrong_kind: list[str] = []
    missing_prerequisite: list[str] = []
    problems: list[str] = []

    for name, gate in gates.items():
        is_declared = name in by_name
        is_excused = name in not_supported
        if is_declared and is_excused:
            contradictory.append(name)
            problems.append(
                f"{name!r} is both declared and listed in not_supported; a vendor either has it or does not."
            )
        elif not is_declared and not is_excused:
            undeclared.append(name)
            problems.append(
                f"{name!r} is gated by the core at {gate.gated_at} but this vendor neither declares it nor "
                f"lists it in not_supported. Declare it, or record why it does not apply."
            )
        if not is_declared:
            continue
        decl = by_name[name]
        if gate.expected_kind is not None and decl.kind != gate.expected_kind:
            wrong_kind.append(name)
            problems.append(f"{name!r} must be declared with kind {gate.expected_kind!r}, not {decl.kind!r}.")
        for prerequisite in gate.required_prerequisites:
            if prerequisite not in decl.requires:
                missing_prerequisite.append(name)
                problems.append(
                    f"{name!r} must list {prerequisite!r} in requires; the core will not gate it a second time."
                )

    for name, reason in not_supported.items():
        if name not in gates:
            ungated.append(name)
            problems.append(
                f"not_supported names {name!r}, which the core does not gate on. "
                f"Gated capabilities: {', '.join(core_gated_names())}."
            )
        elif not reason.strip():
            unreasoned.append(name)
            problems.append(f"not_supported[{name!r}] has no reason; an expected absence is recorded with its why.")

    return CapabilityDeclarationReport(
        undeclared=tuple(undeclared),
        contradictory=tuple(contradictory),
        ungated=tuple(ungated),
        unreasoned=tuple(unreasoned),
        wrong_kind=tuple(wrong_kind),
        missing_prerequisite=tuple(missing_prerequisite),
        problems=tuple(problems),
    )


def assert_capability_declarations(
    declared: Iterable[CapabilityDecl],
    not_supported: Mapping[str, str],
) -> None:
    """Raise ``invalid_value`` when the declarations are incomplete. Called
    from unit construction, so an incomplete vendor is a startup failure
    naming every problem at once."""
    report = check_capability_declarations(declared, not_supported)
    if report.ok:
        return
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail="Incomplete capability declarations: " + " ".join(report.problems),
        field="capabilities",
        info={
            "problems": list(report.problems),
            "core_gated": [gate.as_json() for gate in CORE_GATED_CAPABILITIES],
        },
    )
