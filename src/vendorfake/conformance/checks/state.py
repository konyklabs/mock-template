"""C06, C07, C13 -- state is reproducible, append-only, and honestly gated.

C06 is the property a consumer's CI depends on: two units built the same way
hold the same entities, so a test that passed this morning is not going to fail
this afternoon because an id was drawn from a system random. C07 is what makes
the journal usable as an event source rather than as decoration. C13 is the
lifecycle: a state machine that quietly permits a self-transition turns "pay
this order twice" into a success, which is a bug a consumer will only find in
production.
"""

from __future__ import annotations

from typing import Any

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import Requires, require

__all__ = [
    "journal_is_append_only",
    "seed_is_deterministic_across_units",
    "state_machines_are_honestly_gated",
]

_INVALID_TRANSITION = "invalid_transition"


@check(
    id="C06",
    name="state: the seed scenario is deterministic across two fresh units",
    asserts="Two independently constructed units report the same entity digest, over a non-empty seed.",
    requires=Requires(seed=True),
)
def seed_is_deterministic_across_units(env: CheckEnv) -> str:
    first = env.state()
    entities: dict[str, int] = {str(name): int(count) for name, count in first["entities"].items()}
    loaded = sum(entities.values())
    require(
        loaded > 0,
        "the seed scenario loaded no entities, so 'the digest matches' would be a statement about "
        "two empty stores. Point the profile at a seed document with content.",
    )
    with env.fresh() as other:
        second = other.state()
    require(
        first["digest"] == second["digest"],
        f"two freshly built units on profile {env.profile!r} report different entity digests:\n"
        f"  unit A: {first['digest']} over {entities}\n"
        f"  unit B: {second['digest']} over {second['entities']}\n"
        f"Something in hydration is drawn from a non-reproducible source. Every id comes from the "
        f"seeded RNG and every timestamp from the unit's clock; a uuid4() or a time.time() in the "
        f"vendor's hydrate() is the usual cause. Fields that are legitimately per-run belong in "
        f"VendorDefinition.volatile_fields, which the digest excludes.",
    )
    return f"{loaded} entities across {len(entities)} collections; both units digest to {first['digest']}"


@check(
    id="C07",
    name="state: the journal is append-only and versions never go backwards",
    asserts=(
        "Journal sequence numbers strictly increase, and for each entity the recorded version "
        "moves forward on every entry."
    ),
)
def journal_is_append_only(env: CheckEnv) -> str:
    document = env.get_json(f"{CONTROL_PREFIX}journal")
    entries: list[dict[str, Any]] = list(document["entries"])
    require(
        entries,
        "the journal is empty, so this check would assert nothing. The seed scenario journals its "
        "inserts; an empty journal after hydration means the vendor's hydrate() is writing into the "
        "store past the collection API that records entries.",
    )

    problems: list[str] = []
    previous_seq: int | None = None
    latest: dict[str, int] = {}
    for entry in entries:
        seq = int(entry["seq"])
        if previous_seq is not None and seq <= previous_seq:
            problems.append(
                f"journal seq went {previous_seq} -> {seq}. The sequence is the total order every "
                f"consumer of the journal relies on, webhook dispatch included; it is minted under "
                f"the store lock in core/state/journal.py and must only ever increase."
            )
        previous_seq = seq

        key = f"{entry['collection']}/{entry['id']}"
        to_version = entry.get("to_version")
        if to_version is None:
            # A delete has no resulting version. It ends the entity's history
            # rather than moving it, so it is not a regression.
            latest.pop(key, None)
            continue
        version = int(to_version)
        was = latest.get(key)
        if was is not None and version <= was:
            problems.append(
                f"{key}: version went {was} -> {version} at seq {seq} (op {entry['op']!r}). A "
                f"committed mutation always lands on a new version -- core/state/store.py must "
                f"journal to_version = from_version + 1, never the version it read."
            )
        latest[key] = version

    require(not problems, "\n".join(problems))
    return (
        f"{len(entries)} entries, seq 1..{document['seq']}, strictly increasing; "
        f"{len(latest)} live entities, every version monotonic"
    )


@check(
    id="C13",
    name="state: self-transitions are illegal unless declared, terminal states are immutable",
    asserts=(
        "For every declared state, terminal == (to == []); a self-transition is legal only where "
        "the machine allows it; a terminal state refuses any mutation, with invalid_transition."
    ),
    requires=Requires(machines=True),
)
def state_machines_are_honestly_gated(env: CheckEnv) -> str:
    machines = env.machines()
    problems: list[str] = []
    states_checked = 0
    transitions_probed = 0

    def probe(machine: str, from_state: str, to_state: str | None) -> tuple[bool, str | None]:
        body: dict[str, Any] = {"machine": machine, "from": from_state}
        if to_state is not None:
            body["to"] = to_state
        answered = env.client.call("POST", f"{CONTROL_PREFIX}machines/probe", json_body=body)
        return answered.status == 200, answered.error_kind

    for name, machine in machines.items():
        for state_name, state in machine["states"].items():
            states_checked += 1
            to: list[str] = list(state["to"])
            terminal = bool(state["terminal"])
            allow_self = bool(state["allow_self"])

            if terminal != (to == []):
                problems.append(
                    f"{name}.{state_name}: terminal={terminal} but to={to}. Terminality must be "
                    f"DERIVED from an empty transition list (core/state/machine.py::StateDef."
                    f"terminal), never stored beside it -- two fields cannot be kept in step."
                )

            legal, kind = probe(name, state_name, state_name)
            transitions_probed += 1
            if terminal:
                if legal:
                    problems.append(
                        f"{name}.{state_name} is terminal (to=[]) and a self-transition was "
                        f"accepted. A terminal state moves nowhere, itself included: "
                        f"`if from_state == to_state: return True` in "
                        f"core/state/machine.py::can_transition is the defect this contract "
                        f"exists for, and it is what lets a second payment on a completed order "
                        f"succeed."
                    )
            elif legal != allow_self:
                problems.append(
                    f"{name}: {state_name} -> {state_name} came back "
                    f"{'legal' if legal else 'illegal'}, but the machine declares "
                    f"allow_self={allow_self}. A self-transition is legal only where the vendor "
                    f"said so: `if from_state == to_state: return True` in "
                    f"core/state/machine.py::can_transition is the defect this contract exists "
                    f"for, and silence must mean no."
                )
            if not legal and kind != _INVALID_TRANSITION:
                problems.append(
                    f"{name}: a refused {state_name} -> {state_name} answered x-unit-error={kind!r}, "
                    f"expected {_INVALID_TRANSITION!r}. A sequencing error and a typo are different "
                    f"failures and a consumer routes on the kind."
                )

            if terminal:
                mutable, mutable_kind = probe(name, state_name, None)
                if mutable or mutable_kind != _INVALID_TRANSITION:
                    problems.append(
                        f"{name}.{state_name}: assert_mutable permits mutating a terminal state "
                        f"(answered legal={mutable}, kind={mutable_kind!r}). ANY mutation of a "
                        f"finished entity is refused, not just a state change -- a refunded order "
                        f"does not get new line items either."
                    )

            # The machine must enforce exactly what it declares, in both
            # directions. Asserting only the self-transition rule leaves a
            # permissive predicate undetectable on any vendor whose states all
            # happen to allow themselves: the vacuity is in the vendor's data,
            # so the contract has to cover every pair rather than one.
            for other in machine["states"]:
                if other == state_name:
                    continue
                declared_legal = other in to
                moved, moved_kind = probe(name, state_name, other)
                transitions_probed += 1
                if moved != declared_legal:
                    problems.append(
                        f"{name}: {state_name} -> {other} came back "
                        f"{'legal' if moved else 'illegal'}, but the machine declares to={to}. A "
                        f"lifecycle is enforced from its declaration and from nothing else -- any "
                        f"shortcut in core/state/machine.py::can_transition makes the published "
                        f"machine a description of something other than what runs."
                    )
                if not moved and moved_kind != _INVALID_TRANSITION:
                    problems.append(
                        f"{name}: a refused {state_name} -> {other} answered "
                        f"x-unit-error={moved_kind!r}, expected {_INVALID_TRANSITION!r}."
                    )

    require(not problems, "\n".join(problems))
    terminals = sum(
        1 for machine in machines.values() for state in machine["states"].values() if bool(state["terminal"])
    )
    return (
        f"{len(machines)} machine(s), {states_checked} states ({terminals} terminal), "
        f"{transitions_probed} transitions probed: every move legal exactly where declared, "
        f"self-transitions gated, terminals immutable"
    )
