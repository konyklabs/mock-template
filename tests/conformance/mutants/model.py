"""What a mutant is, and how one is turned into a conformance target.

FOR: making "this suite can fail" a checkable statement. A conformance suite
that has only ever been run against a correct unit has demonstrated that it
terminates, not that it discriminates. A mutant is a unit broken in exactly
one way, shipped with the check ids it must turn red -- so the suite is tested
the way any other predicate is tested: with inputs on both sides of it.

INVARIANT: **a mutant declares its collateral.** Trips are what must go red;
:attr:`Mutant.also_trips` is what is *allowed* to go red as well, and it comes
with a written reason. Anything else going red fails the meta-test. Without
that rule a mutant that broke the unit outright would "catch" every check and
prove nothing about the one it names -- which is the failure mode the whole
exercise is guarding against, so it is not left to judgement.

SECOND INVARIANT: **the null mutant exists.** :data:`NULL_MUTANT` mutates
nothing and must be entirely green. It is what makes a red check under any
other mutant attributable to that mutant rather than to this harness -- which
builds its units a little differently from ``registry.create_unit`` in order to
reach the control-plane and selector seams, and would otherwise be an
unexamined variable sitting under every result.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from vendorfake.conformance import ConformanceClient, ConformanceTarget
from vendorfake.conformance.client import InProcessConformanceClient
from vendorfake.core.capability.registry import CapabilityRegistry
from vendorfake.core.chaos.engine import ChaosEngine
from vendorfake.core.chaos.selector import FaultSelector
from vendorfake.core.config.profile import load_profile
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.types import Route, VendorDefinition
from vendorfake.core.kernel.unit import ControlBinding, DispatcherFactory, Unit
from vendorfake.core.transport.inprocess import in_process
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.square.vendor import create_square_vendor

__all__ = ["MUTANTS", "Mutant", "Provenance", "build_unit", "mutant_target", "register"]

DEFAULT_PROFILE = "full"
"""Where a mutant is judged unless it says otherwise.

``full`` enables every capability, so every contract is askable and a check
that stays green is green because it was asked and answered -- not because a
precondition was missing. A mutant that needs the whole matrix (the
skip-everywhere one) declares it.
"""


class Provenance(StrEnum):
    """Where the defect a mutant reproduces actually comes from.

    Labelled rather than implied. "The reference has this bug" and "this is the
    bug the fix invites" are different claims and only one of them can be
    checked against a codebase; writing them down keeps a plausible story from
    hardening into a citation.
    """

    #: Verbatim from the TypeScript reference implementation, which still has it.
    REFERENCE = "reference"
    #: Verbatim from a losing bake-off entry.
    LOSING_ENTRY = "losing-entry"
    #: A plausible defect nobody has shipped here. Invented, and labelled so.
    HYPOTHETICAL = "hypothetical"


@dataclass(frozen=True)
class Mutant:
    """One unit, broken in one way, with the checks that must notice."""

    #: Stable id, ``M01`` upward. Never reused.
    id: str
    #: Slug naming the defect, not the fix.
    name: str
    #: One sentence: what is wrong with this unit.
    defect: str
    provenance: Provenance
    #: Check ids that MUST report FAIL. Empty only for the null mutant.
    trips: frozenset[str] = frozenset()
    #: Check ids allowed to fail as well, because the defect genuinely
    #: violates them too. Every entry needs a line in :attr:`cascade`.
    also_trips: frozenset[str] = frozenset()
    cascade: str = ""
    #: Check ids that must SKIP on every profile -- the anti-vacuity path.
    skips_everywhere: frozenset[str] = frozenset()
    profiles: tuple[str, ...] = (DEFAULT_PROFILE,)
    transports: tuple[str, ...] = ("inprocess",)
    #: Transports on which this mutant's unit is built in a SEPARATE PROCESS.
    #:
    #: Empty for all but one. Spawning a process per contract would multiply
    #: the meta-suite's cost by the number of mutants, and only the mutant
    #: whose defect is *per-process* has anything to gain by it -- which is
    #: itself the reason the cross-process contract exists.
    out_of_process: tuple[str, ...] = ()
    #: This unit cannot be CONSTRUCTED at all.
    #:
    #: Not a contract violation and deliberately not modelled as one: every
    #: check errors, none is asked, and the meta-test asserts exactly that. It
    #: is how the FAIL/ERROR split is held down, because the whole point of the
    #: split is that a unit which refuses to start used to be indistinguishable
    #: from sixteen violated contracts.
    fails_to_construct: bool = False

    # -- the three seams, all optional -------------------------------------

    #: Rewrites the vendor definition.
    vendor: Callable[[VendorDefinition], VendorDefinition] | None = None
    #: Rewrites the control-plane route table.
    control: Callable[[Sequence[Route]], Sequence[Route]] | None = None
    #: Replaces the fault selector, through ``Unit(fault_selector=...)``.
    selector: Callable[[ChaosEngine, CapabilityRegistry], FaultSelector] | None = None
    #: Wraps the client for one transport, modelling a defective binding.
    client: Callable[[str, ConformanceClient], ConformanceClient] | None = None
    #: Replaces the webhook dispatcher, through ``Unit(dispatcher=...)``.
    dispatcher: DispatcherFactory | None = None

    @property
    def expected_red(self) -> frozenset[str]:
        """Every check this mutant is permitted to turn red."""
        return self.trips | self.also_trips

    @property
    def label(self) -> str:
        return f"{self.id}-{self.name}"


MUTANTS: list[Mutant] = []
"""Every registered mutant, in id order."""


def register(mutant: Mutant) -> Mutant:
    """Add one mutant to the registry, refusing a duplicate id or name."""
    for existing in MUTANTS:
        if existing.id == mutant.id:
            raise RuntimeError(f"duplicate mutant id {mutant.id!r}: already used by {existing.name!r}")
        if existing.name == mutant.name:
            raise RuntimeError(f"duplicate mutant name {mutant.name!r}: already used by {existing.id!r}")
    if mutant.also_trips and not mutant.cascade.strip():
        raise RuntimeError(
            f"mutant {mutant.id} declares also_trips={sorted(mutant.also_trips)} with no `cascade` reason. "
            f"Tolerated collateral is written down or it is not tolerated."
        )
    MUTANTS.append(mutant)
    # Kept in id order, as the check registry is: a report and a coverage
    # matrix read in id order regardless of which file registered what.
    MUTANTS.sort(key=lambda entry: entry.id)
    return mutant


# ---------------------------------------------------------------------------
# Building a unit with the seams open.
# ---------------------------------------------------------------------------


def build_unit(mutant: Mutant, profile: str) -> Unit:
    """The four steps ``registry.create_unit`` performs, with two seams open.

    Spelled out rather than delegated because ``create_unit`` deliberately
    exposes neither the control-plane factory nor the fault selector -- a
    consumer has no business replacing either. The mutants do, and they are the
    only caller here. If this drifts from ``create_unit``, the null mutant goes
    red, which is what it is for.
    """
    definition: VendorDefinition = create_square_vendor()
    if mutant.vendor is not None:
        definition = mutant.vendor(definition)
    loaded = load_profile(
        profile_dir=definition.profile_dir,
        name=profile,
        base_dir=definition.base_dir,
        env={},
        defaults=definition.retry_defaults,
    )

    def control(binding: ControlBinding) -> Sequence[Route]:
        routes = control_plane_routes(binding, framework_answered=None)
        return routes if mutant.control is None else mutant.control(routes)

    unit = Unit(
        vendor=definition,
        config=loaded.config,
        seed=loaded.seed,
        sink=MemorySink(),
        control_routes=control,
        fault_selector=mutant.selector,
        dispatcher=mutant.dispatcher,
    )
    unit.start()
    return unit


@contextmanager
def _open_client(mutant: Mutant, profile: str, transport: str) -> Iterator[ConformanceClient]:
    """A client onto a freshly built mutated unit, over the named transport.

    Both transports build the unit here, through :func:`_build_unit`. That is
    the load-bearing part: a transport harness that served a *fresh, correct*
    unit for the out-of-process binding would make every cross-binding mutant
    result a statement about a unit that was never mutated.
    """
    if transport in mutant.out_of_process:
        yield from _served_by_a_child(mutant, profile)
        return
    unit = build_unit(mutant, profile)
    try:
        if transport == "inprocess":
            client: ConformanceClient = InProcessConformanceClient(in_process(unit))
            yield client if mutant.client is None else mutant.client(transport, client)
            return
        if transport == "http":
            # Imported here rather than at module scope so that uvicorn stays
            # off the import path of a purely in-process mutant run.
            from tests.conformance.harness import serve

            with serve(unit) as served:
                yield served if mutant.client is None else mutant.client(transport, served)
            return
        raise ValueError(f"unknown transport {transport!r}; the mutant harness offers 'inprocess' and 'http'")
    finally:
        unit.stop()


def _served_by_a_child(mutant: Mutant, profile: str) -> Iterator[ConformanceClient]:
    """The mutated unit, rebuilt and served by a separate operating-system process.

    Imported here rather than at module scope so that a purely in-process
    mutant run never pays for the harness's subprocess machinery.
    """
    import subprocess
    import sys

    from tests.conformance.harness import REPO_ROOT, _client_onto, _stop

    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.conformance.unit_child",
            "--profile",
            profile,
            "--mutant",
            mutant.id,
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        yield from _client_onto(child)
    finally:
        _stop(child)


def mutant_target(mutant: Mutant) -> ConformanceTarget:
    """The mutant, as something ``run_conformance`` can be pointed at."""
    return ConformanceTarget(
        name=f"square+{mutant.name}",
        open_client=functools.partial(_open_client, mutant),
        profiles=mutant.profiles,
        transports=mutant.transports,
        out_of_process=mutant.out_of_process,
    )


NULL_MUTANT = Mutant(
    id="M00",
    name="the-control",
    defect="Nothing. The unmutated unit, built through the mutant harness.",
    provenance=Provenance.HYPOTHETICAL,
    trips=frozenset(),
)
"""Not registered in :data:`MUTANTS`: it is the control, not a mutant.

It exists so that "C13 went red under M13" means "the permissive predicate did
that". Without it, every mutant result would also carry the unexamined claim
that :func:`_build_unit` produces a conformant unit in the first place.
"""
