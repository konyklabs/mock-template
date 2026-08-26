"""What the core itself gates on, published as data.

FOR: closing the hole that "capabilities are just names" leaves open. The
registry answers ``is_enabled(name)`` for any string; when the *core* asks
about a capability the vendor never declared, the honest answer -- "you never
told me whether you have this" -- is indistinguishable from "it is switched
off", so the behaviour is silently absent and nothing anywhere says so.

INVARIANT: **silence is a failure.** Every member of :class:`CoreCapability`
must be either declared by the vendor or listed in
``VendorDefinition.not_supported`` with a prose reason. Not both. And
``not_supported`` may not name a capability the core does not gate on, so the
map cannot quietly accumulate names that mean nothing.

Prose reasons rather than a bare set, deliberately: an expected absence is
recorded *with its justification*, which is the same discipline a spec-drift
report uses for a documented endpoint it does not implement. "This vendor has
no webhook mechanism at all" and "webhooks are on the roadmap" are different
facts, and a set of names loses both.

``gated_at`` names the exact call site that performs each gate. Two reasons it
is here and not a comment: the boundary checker can reconcile this tuple
against every literal handed to ``is_enabled`` / ``assert_enabled`` inside the
core, so the tuple cannot become a second list to forget; and a reader asking
"what actually stops when I turn this off" gets a file and a function rather
than a search.

The three gates, and why the split matters
------------------------------------------
``chaos`` gates request-scope faults from **every** source -- standing rules,
in-band magic values, forced headers -- at one choke point. It is a
``behavior`` capability: it owns no routes, because there is no endpoint whose
absence would represent it.

``webhooks`` gates delivery existing at all: the dispatcher never attaches.

``webhooks.chaos`` gates delivery-scope faults only. It is a separate gate
rather than a second caller of the first one because collapsing them would
change *which* capability disables delivery faults -- a profile that wants
request faults but honest delivery, or the reverse, is a real configuration
and one gate cannot express it.

``webhooks.chaos`` must therefore require ``chaos`` as well as ``webhooks``:
delivery faults are still faults, and a unit with fault injection switched off
that nonetheless drops webhooks would be lying about itself. That requirement
is checked here rather than trusted to each vendor's declaration.
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
    """Every capability the core itself gates on. Exactly these three.

    A fourth is added only together with its :data:`CORE_GATED_CAPABILITIES`
    entry and a conformance check, because an ungated member of this enum would
    make the completeness rule assert something the core does not actually do.
    """

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
    #: The declaration kind this capability must carry, when the core requires
    #: one. ``None`` leaves the choice to the vendor.
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
        gated_at="vendorfake.core.chaos.selector.select_request",
        effect="Request-scope faults are never armed, from any source: standing rules, in-band values, forced headers.",
        expected_kind="behavior",
    ),
    CoreGate(
        capability=CoreCapability.WEBHOOKS,
        gated_at="vendorfake.core.webhooks.dispatcher.WebhookDispatcher.attach",
        effect="The dispatcher never attaches to the journal, so no event is ever prepared or delivered.",
    ),
    CoreGate(
        capability=CoreCapability.WEBHOOKS_CHAOS,
        gated_at="vendorfake.core.chaos.selector.select_webhook",
        effect="Delivery-scope faults are never armed: no duplication, reordering, dropped acknowledgement or delay.",
        expected_kind="behavior",
        required_prerequisites=(CoreCapability.WEBHOOKS.value, CoreCapability.CHAOS.value),
    ),
)
"""The gates, in the order a reader should meet them. Published so the
conformance suite asserts against data rather than a list it keeps in step by
hand, and so the boundary checker can reconcile it against the literals the
core actually passes to the registry."""


def core_gated_names() -> tuple[str, ...]:
    """The gated capability names, in declaration order."""
    return tuple(gate.capability.value for gate in CORE_GATED_CAPABILITIES)


@dataclass(frozen=True, slots=True)
class CapabilityDeclarationReport:
    """The result of the completeness check, as data a check can assert on.

    Every field is a tuple of capability names except :attr:`problems`, which
    is the human-readable rendering. A conformance check reads the categories;
    a startup failure prints the problems.
    """

    #: Gated, but neither declared nor recorded as unsupported.
    undeclared: tuple[str, ...] = ()
    #: Both declared and recorded as unsupported -- the vendor contradicts itself.
    contradictory: tuple[str, ...] = ()
    #: Recorded as unsupported, but the core does not gate on it.
    ungated: tuple[str, ...] = ()
    #: Recorded as unsupported with a blank or whitespace-only reason.
    unreasoned: tuple[str, ...] = ()
    #: Declared with the wrong kind for what the core does with it.
    wrong_kind: tuple[str, ...] = ()
    #: Declared without a prerequisite the core requires it to carry.
    missing_prerequisite: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def check_capability_declarations(
    declared: Iterable[CapabilityDecl],
    not_supported: Mapping[str, str],
) -> CapabilityDeclarationReport:
    """Reconcile a vendor's declarations against what the core gates on.

    Pure: it returns a report and raises nothing, so the same code serves both
    the startup assertion and the conformance check without one of them having
    to catch an exception to find out what happened.
    """
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
    """Raise ``invalid_value`` when the declarations are incomplete.

    Called from unit construction, so an incomplete vendor is a startup failure
    naming every problem at once rather than a behaviour that is quietly off.
    """
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
