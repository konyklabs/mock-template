"""C06, C07, C13, C19, C20, C22, C24, C25, C26 -- state is reproducible,
append-only, honestly gated, deduplicated per operation, and paged without overlap.

C06 is the property a consumer's CI depends on: two units built the same way
hold the same entities, so a test that passed this morning is not going to fail
this afternoon because an id was drawn from a system random. C07 is what makes
the journal usable as an event source rather than as decoration. C13 is the
lifecycle: a state machine that quietly permits a self-transition turns "pay
this order twice" into a success, which is a bug a consumer will only find in
production.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.conformance.client import MISSING
from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv, RouteRow
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceSkip, Requires, require

__all__ = [
    "a_cursor_belongs_to_the_query_that_issued_it",
    "a_replayed_idempotency_key_does_not_run_twice",
    "a_reused_key_with_a_different_body_answers_as_declared",
    "an_idempotency_key_is_scoped_to_its_operation",
    "declared_pages_never_overlap_and_lose_nothing",
    "journal_is_append_only",
    "seed_is_deterministic_across_processes",
    "seed_is_deterministic_across_units",
    "state_machines_are_honestly_gated",
]

_INVALID_TRANSITION = "invalid_transition"
_VERSION_CONFLICT = "version_conflict"
_INVALID_CURSOR = "invalid_cursor"
_MUTATING_METHODS = frozenset({"POST", "PUT"})
_REPLAY_HEADER = "x-unit-idempotent-replay"
_IGNORED_BODY_HEADER = "x-unit-idempotent-ignored-body"
_IDEMPOTENCY_CONFLICT = "idempotency_conflict"

_QUERY_A: dict[str, str] = {"conformance": "query-a"}
_QUERY_B: dict[str, str] = {"conformance": "query-b"}
"""Two queries that differ only in a value, deliberately.

The fingerprint is a digest of whatever the caller called the query, so two
that differ in one character are the strongest form of the test: a comparison
that had degraded to "same length" or "both truthy" would still pass for two
queries that differed structurally.
"""


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
        "A committed mutation driven through the vendor's own surface appends a strictly later "
        "seq at from_version + 1; repeating it at the version already spent is REFUSED with "
        "version_conflict and appends nothing; and every entry in the journal is monotonic."
    ),
    requires=Requires(mutating_example=True, credentials=True),
)
def journal_is_append_only(env: CheckEnv) -> str:
    """Cause a mutation, then read the journal -- in that order.

    This contract used to read ``/__unit/journal`` and nothing else, over a
    journal containing only the thirteen seed inserts. Its own failure text
    named the defect it could not see: ``core/state/store.py`` journalling "the
    version it read" rather than ``from_version + 1`` left the suite entirely
    green, because two ``update`` entries in a row are the one shape a journal
    of pure inserts never contains. Optimistic concurrency was gone, a stale
    version was accepted twice, and sixteen contracts noticed nothing.

    So the mutation comes first, and it goes through the vendor's own surface
    using the body the route publishes -- a check cannot invent a body the
    vendor's validation will accept, which is what ``Route.example_body`` is
    for. The concurrency half is then driven through
    ``POST /__unit/state/update`` against the entity that create just made:
    "the version I was given is stale" is a rule of the CORE's store, and
    asking it only through whichever endpoint a particular vendor exposes for
    updating whichever entity it owns would make it a contract about that
    vendor instead.
    """
    before = env.get_json(f"{CONTROL_PREFIX}journal")
    seq_before = int(before["seq"])

    route = env.first_example_route(methods=_MUTATING_METHODS)
    body = dict(route.example_body or {})
    idem = route.idempotency
    if idem is not None:
        body[str(idem["key_path"])] = "conformance-journal-probe"
    created = env.client.call(
        route.method,
        route.probe_path,
        json_body=body,
        headers=env.authorized(route),
    )
    require(
        200 <= created.status < 300,
        f"{route.key} refused the body it publishes as its own example_body: {created.status} "
        f"{created.error_kind!r} {created.text[:300]}. The example is what makes a committed "
        f"mutation reachable from a check that knows no vendor; an example the route will not "
        f"accept is worse than none, because it turns this contract into a test of the example. "
        f"Fix Route.example_body, in the same file as the route.",
    )

    caused = [entry for entry in env.get_json(f"{CONTROL_PREFIX}journal")["entries"] if int(entry["seq"]) > seq_before]
    require(
        caused,
        f"{route.key} answered {created.status} and appended nothing to the journal. A committed "
        f"mutation is journalled by core/state/store.py's collection API; a handler that wrote "
        f"straight into the map behind it has produced a change no event source can see, so every "
        f"webhook and every consumer polling the journal misses it.",
    )
    written = caused[-1]
    collection, entity_id = str(written["collection"]), str(written["id"])
    committed = int(written["to_version"])
    require(
        int(written["seq"]) > seq_before,
        f"the mutation was journalled at seq {written['seq']}, not after {seq_before}.",
    )

    stale = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}state/update",
        json_body={"collection": collection, "id": entity_id, "version": committed},
    )
    require(
        200 <= stale.status < 300,
        f"POST /__unit/state/update refused version {committed} of {collection}/{entity_id}, which "
        f"is the version the journal just recorded: {stale.status} {stale.error_kind!r} "
        f"{stale.text[:200]}. The journal's to_version and the store's current version are the "
        f"same number or the journal is not a record of this store.",
    )
    moved = int(stale.json()["version"])
    require(
        moved == committed + 1,
        f"an update at version {committed} landed on version {moved}, expected {committed + 1}. "
        f"core/state/store.py must journal to_version = from_version + 1, never the version it "
        f"read -- a flat version line means two writers can each believe they won.",
    )

    seq_before_conflict = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])
    replayed = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}state/update",
        json_body={"collection": collection, "id": entity_id, "version": committed},
    )
    require(
        replayed.error_kind == _VERSION_CONFLICT,
        f"the SAME version {committed} was accepted a second time: {collection}/{entity_id} answered "
        f"{replayed.status} with x-unit-error={replayed.error_kind!r}, expected "
        f"{_VERSION_CONFLICT!r}. Optimistic concurrency is the whole reason a version exists; a "
        f"store that accepts a spent one lets a consumer overwrite a change they never read. The "
        f"comparison is in core/state/store.py::Collection.update and it must be against the "
        f"CURRENT version, not against nothing.",
    )
    require(
        int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"]) == seq_before_conflict,
        "the refused update still appended to the journal. A mutation that did not commit is not a "
        "mutation: core/state/store.py mutates a private copy and journals only after the version "
        "check, so a journal entry here means the write happened and the refusal is cosmetic.",
    )

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
        f"{route.key} committed {collection}/{entity_id} at version {committed} (seq "
        f"{written['seq']}); a second write moved it to {moved}; version {committed} was then refused "
        f"with {_VERSION_CONFLICT} and journalled nothing; {len(entries)} entries, seq "
        f"1..{document['seq']}, strictly increasing; {len(latest)} live entities, every version "
        f"monotonic"
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


@check(
    id="C19",
    name="state: a replayed idempotency key returns the stored answer and runs nothing",
    asserts=(
        "The same request sent twice under one idempotency key commits once: the second answer is "
        "the stored bytes, marked as a replay, and the journal does not move."
    ),
    requires=Requires(idempotent_example=True, credentials=True),
)
def a_replayed_idempotency_key_does_not_run_twice(env: CheckEnv) -> str:
    """The contract a route's ``idempotency`` declaration is a promise of.

    Nothing asked it before. Deleting the lookup at step 7 of
    ``core/kernel/unit.py::_run_pipeline`` -- one line, ``stored = None`` -- made
    every replayed key re-execute its handler, and the suite stayed green:
    replay is only observable once a request has actually *succeeded*, and no
    contract had ever driven one.

    Two observations, because either alone is satisfiable by a broken unit. The
    stored answer alone would pass for a handler that ran again and happened to
    be deterministic; an unmoved journal alone would pass for a unit that
    refused the second request outright. Together they are "it ran once and you
    were told what happened the first time", which is the whole of what an
    idempotency key buys a consumer whose network dropped an acknowledgement.
    """
    route = env.first_example_route(methods=_MUTATING_METHODS, idempotent=True)
    spec = dict(route.idempotency or {})
    key_path = str(spec["key_path"])
    body = dict(route.example_body or {})
    body[key_path] = "conformance-idempotency-probe"
    headers = env.authorized(route)

    first = env.client.call(route.method, route.probe_path, json_body=body, headers=headers)
    require(
        200 <= first.status < 300,
        f"{route.key} refused its own published example_body: {first.status} "
        f"{first.error_kind!r} {first.text[:300]}. A replay contract cannot be asked until "
        f"something has succeeded once.",
    )
    # The other direction, which nothing asserted until the third adversarial
    # round stamped the marker on every response and the suite stayed green
    # (konyklabs/roadmap#10, N-6; konyklabs/roadmap#15). A consumer routes on
    # this header to tell "this executed" from "this was deduplicated"; a
    # first execution that claims to be a replay misleads them on every call.
    require(
        _REPLAY_HEADER not in first.headers,
        f"the FIRST execution under {key_path!r} answered with {_REPLAY_HEADER}="
        f"{first.headers.get(_REPLAY_HEADER)!r}. Nothing was replayed: the key had never been "
        f"seen. The marker is stamped in core/kernel/unit.py::_replay and nowhere else; a handler "
        f"or a decorator adding it to a fresh response tells a consumer their request was "
        f"deduplicated when it was executed.",
    )
    seq_after_first = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])

    second = env.client.call(route.method, route.probe_path, json_body=body, headers=headers)
    require(
        second.status == first.status,
        f"the same request under one {key_path!r} answered {first.status} then {second.status}. A "
        f"replay is the stored response, status included: core/kernel/unit.py::_replay returns "
        f"what was recorded rather than re-deriving it.",
    )
    require(
        second.body == first.body,
        f"the same request under one {key_path!r} answered different bytes the second time "
        f"({len(first.body)} then {len(second.body)}). The handler ran again. Step 7 of "
        f"core/kernel/unit.py::_run_pipeline must consult store.get_idempotent BEFORE step 8 and "
        f"return the record; a consumer retrying a request whose acknowledgement was lost would "
        f"otherwise create a second entity every time.\n"
        f"  first:  {first.text[:200]}\n"
        f"  second: {second.text[:200]}",
    )
    replay_header = second.headers.get(_REPLAY_HEADER)
    require(
        replay_header,
        f"the replayed answer carries no {_REPLAY_HEADER!r} header, so a consumer cannot tell a "
        f"replay from a fresh success and cannot test the branch of their own code that handles "
        f"one. It is stamped in core/kernel/unit.py::_replay.",
    )
    seq_after_second = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])
    require(
        seq_after_second == seq_after_first,
        f"the journal moved from seq {seq_after_first} to {seq_after_second} across a replayed "
        f"{key_path!r}. The second request committed a second mutation: the key was not consulted, "
        f"or it was consulted and the handler ran anyway. This is the observation the stored-bytes "
        f"comparison cannot make on its own, because a deterministic handler run twice produces "
        f"identical bytes and two entities.",
    )
    return (
        f"{route.key} under one {key_path!r}: {first.status} then {second.status} with identical "
        f"bytes and {_REPLAY_HEADER}={replay_header!r}; journal held at seq {seq_after_first}"
    )


@check(
    id="C20",
    name="state: a cursor belongs to the query that issued it",
    asserts=(
        "A cursor pages the query it was issued for, is refused with invalid_cursor when replayed "
        "against a different query, and an unparseable cursor is refused the same way."
    ),
    requires=Requires(seed=True),
)
def a_cursor_belongs_to_the_query_that_issued_it(env: CheckEnv) -> str:
    """The fingerprint, asked of the core rather than of a vendor's search route.

    ``if decoded.q != fp`` in ``core/state/store.py`` could be replaced with
    ``if False`` and nothing went red: a cursor issued for one query silently
    paged another, which is the failure mode that gives a consumer a page of
    the wrong rows and no error to notice it by.

    Asked through ``POST /__unit/state/page`` because the rule is the store's.
    A vendor's own paginated endpoint reaches the same code, but a check
    driving it would have to know that vendor's spelling for "cursor" and for
    "filter" -- and a vendor with no paginated endpoint at all would then get
    no contract about cursors, though its core enforces exactly these rules.
    """
    collections = {str(name): int(count) for name, count in env.state()["entities"].items()}
    pageable = sorted(name for name, count in collections.items() if count >= 2)
    if not pageable:
        raise ConformanceSkip(
            f"no seeded collection holds two entities on profile {env.profile!r}, so no query can "
            f"produce a next-page cursor: {collections}"
        )
    collection = pageable[0]

    def page(query: object, cursor: str | None = None) -> Any:
        return env.client.call(
            "POST",
            f"{CONTROL_PREFIX}state/page",
            json_body={"collection": collection, "query": query, "limit": 1, "cursor": cursor},
        )

    issued = page(_QUERY_A)
    require(
        issued.status == 200,
        f"POST /__unit/state/page over {collection!r} answered {issued.status}: {issued.text[:200]}.",
    )
    cursor = issued.json()["cursor"]
    require(
        cursor,
        f"paging {collection!r} one row at a time over {collections[collection]} rows emitted no "
        f"next cursor, so there is no cursor to test. core/state/store.py::Collection.paginate "
        f"emits one exactly when there is genuinely a next page.",
    )

    continued = page(_QUERY_A, cursor)
    require(
        continued.status == 200,
        f"the cursor {collection!r} issued was refused by the query that issued it: "
        f"{continued.status} {continued.error_kind!r} {continued.text[:200]}. A fingerprint that "
        f"rejects its own query makes pagination unusable.",
    )
    require(
        continued.json()["ids"] != issued.json()["ids"],
        f"paging forward returned the same ids ({issued.json()['ids']}). The offset in the cursor "
        f"is not being honoured, so a consumer looping until the cursor is absent never terminates.",
    )

    foreign = page(_QUERY_B, cursor)
    require(
        foreign.error_kind == _INVALID_CURSOR,
        f"a cursor issued for query {_QUERY_A} was accepted by query {_QUERY_B}: "
        f"{foreign.status} with x-unit-error={foreign.error_kind!r}, expected "
        f"{_INVALID_CURSOR!r}. The cursor carries a digest of the query it was issued for and "
        f"core/state/store.py::Collection.paginate must compare it -- paging with a changed filter "
        f"is refused rather than silently answered with rows from the wrong query, which is a "
        f"wrong answer a consumer has no way at all to detect.",
    )

    garbage = page(_QUERY_A, "not-a-cursor-at-all")
    require(
        garbage.error_kind == _INVALID_CURSOR,
        f"an unparseable cursor answered {garbage.status} with "
        f"x-unit-error={garbage.error_kind!r}, expected {_INVALID_CURSOR!r}. A cursor is opaque, so "
        f"a consumer who truncated or hand-edited one must be told that and not handed page one.",
    )
    return (
        f"{collection!r} ({collections[collection]} rows) paged one at a time: cursor honoured by "
        f"its own query, refused with {_INVALID_CURSOR} for a different query and for an "
        f"unparseable value"
    )


@check(
    id="C22",
    name="state: the seed scenario is deterministic across two OPERATING-SYSTEM PROCESSES",
    asserts=(
        "A unit built in this process and a unit built in a separate process report the same "
        "entity digest over the same profile."
    ),
    requires=Requires(seed=True, out_of_process=True),
)
def seed_is_deterministic_across_processes(env: CheckEnv) -> str:
    """C06's claim, made about the thing C06's claim is about.

    C06 compares two units, and both of them are built in this interpreter --
    ``uvicorn`` on a background thread is a different *binding*, not a
    different process. So C06 cannot witness any source of variation the
    process itself supplies: the pid, ``PYTHONHASHSEED``, an import-time
    counter, an environment value read at module scope. A hydrate drawing an id
    from ``os.getpid()`` gives both of C06's units the same wrong answer and
    C06 goes green.

    But "a test that passed this morning is not going to fail this afternoon"
    is a claim about *runs*, and runs are processes. This contract is the same
    comparison across that boundary, and it is a separate contract rather than
    a clause of C06 because it costs a process to ask and because a target that
    cannot offer one should skip exactly this and still be held to the rest.
    """
    here = env.state()
    entities: dict[str, int] = {str(name): int(count) for name, count in here["entities"].items()}
    transport = env.target.out_of_process[0]
    with env.fresh(transport=transport) as elsewhere:
        there = elsewhere.state()
    require(
        here["digest"] == there["digest"],
        f"a unit built in this process and one built in a separate process report different entity "
        f"digests on profile {env.profile!r}:\n"
        f"  this process:  {here['digest']} over {entities}\n"
        f"  other process: {there['digest']} over {there['entities']}\n"
        f"Something in hydration is drawn from the PROCESS rather than from the scenario -- a pid, "
        f"a start time, an import-time counter, PYTHONHASHSEED, an environment variable read at "
        f"module scope. Two units in one interpreter share all of those and cannot see this, which "
        f"is why this contract costs a process. Every id comes from the seeded RNG and every "
        f"timestamp from the unit's clock; genuinely per-run fields belong in "
        f"VendorDefinition.volatile_fields, which the digest excludes.",
    )
    return (
        f"{sum(entities.values())} entities across {len(entities)} collections; this process and a "
        f"unit built over the {transport!r} transport both digest to {here['digest']}"
    )


# ---------------------------------------------------------------------------
# C24, C25 -- what an idempotency declaration promises beyond "twice is once".
# ---------------------------------------------------------------------------


def _keyed(route: RouteRow, key: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The route's example body (or nothing) carrying ``key`` at its declared path."""
    body = dict(route.example_body or {})
    body[str(dict(route.idempotency or {})["key_path"])] = key
    if extra:
        body.update(extra)
    return body


@check(
    id="C24",
    name="state: an idempotency key is scoped to its operation",
    asserts=(
        "A key that succeeded on one route, sent to a route declaring a different idempotency "
        "scope, is not replayed: the second route answers for itself, with no replay marker and "
        "different bytes, while the first route still replays the same key."
    ),
    requires=Requires(idempotency_scopes=True, credentials=True),
)
def an_idempotency_key_is_scoped_to_its_operation(env: CheckEnv) -> str:
    """The ``scope`` field of every IdempotencySpec, asked rather than read.

    Collapsing the store's key from ``f"{scope} {key}"`` to ``key`` made a
    PayOrder sent under a key a CreateOrder had used answer with the
    CreateOrder body and a 200, and the matrix stayed green (the third
    adversarial round, konyklabs/roadmap#10, N-3c; tracked as
    konyklabs/roadmap#15): C19 sends its key to one route and never to a
    second. A consumer who reuses a key across operations -- which real
    clients do, because "idempotency key" reads as "request id" -- would be
    handed another operation's answer with nothing to notice it by.

    The second route is driven with its own example body where it has one, so
    the request is one the route would accept; otherwise with the key alone.
    Either is enough: a replay is decided at step 7 of the pipeline, before
    the handler, so the collapsed scope answers the stored response whether or
    not the second request would have validated. The two things a scoped key
    must produce are a second answer that is the second route's own -- no
    marker, different bytes -- and a first route that still replays.
    """
    first_route = next(
        row
        for row in env.example_routes(methods=_MUTATING_METHODS, idempotent=True)
        if env.other_idempotency_scope(row) is not None
    )
    second_route = env.other_idempotency_scope(first_route)
    if second_route is None:  # pragma: no cover - the precondition already refused this
        raise ConformanceSkip("no second idempotency scope")
    first_scope = str(dict(first_route.idempotency or {})["scope"])
    second_scope = str(dict(second_route.idempotency or {})["scope"])
    key = "conformance-idempotency-scope-probe"

    first = env.client.call(
        first_route.method,
        first_route.probe_path,
        json_body=_keyed(first_route, key),
        headers=env.authorized(first_route),
    )
    require(
        200 <= first.status < 300,
        f"{first_route.key} refused its own published example_body: {first.status} "
        f"{first.error_kind!r} {first.text[:300]}. Nothing is stored under a key until something "
        f"has succeeded, so the scope contract cannot be asked.",
    )
    seq_after_first = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])

    second = env.client.call(
        second_route.method,
        second_route.probe_path,
        json_body=_keyed(second_route, key),
        headers=env.authorized(second_route),
    )
    require(
        _REPLAY_HEADER not in second.headers,
        f"{second_route.key} (scope {second_scope!r}) answered {second.status} carrying "
        f"{_REPLAY_HEADER}={second.headers.get(_REPLAY_HEADER)!r} to a key it had never seen -- the "
        f"key was spent on {first_route.key} (scope {first_scope!r}). The idempotency table in "
        f"core/state/store.py is keyed on (scope, key) and the scope is the route's own declaration; "
        f"a table keyed on the key alone hands one operation another operation's answer.",
    )
    require(
        second.body != first.body,
        f"{second_route.key} (scope {second_scope!r}) answered the exact bytes {first_route.key} "
        f"(scope {first_scope!r}) had answered under the same key. That is the first operation's "
        f"stored response served for the second: a consumer reusing a key across operations "
        f"receives a body from the wrong endpoint with a 200 attached.",
    )
    seq_after_second = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])
    if 200 <= second.status < 300:
        require(
            seq_after_second > seq_after_first,
            f"{second_route.key} answered {second.status} and the journal did not move from seq "
            f"{seq_after_first}: nothing executed, so the answer was not the route's own.",
        )

    again = env.client.call(
        first_route.method,
        first_route.probe_path,
        json_body=_keyed(first_route, key),
        headers=env.authorized(first_route),
    )
    require(
        again.headers.get(_REPLAY_HEADER) and again.body == first.body,
        f"after the key was sent to {second_route.key}, {first_route.key} no longer replays it "
        f"({again.status}, {_REPLAY_HEADER}={again.headers.get(_REPLAY_HEADER)!r}). The second "
        f"operation overwrote or evicted the first's record; scopes must isolate in both directions.",
    )
    executed = "executed" if 200 <= second.status < 300 else f"refused as {second.error_kind}"
    return (
        f"key {key!r}: {first_route.key} [{first_scope}] -> {first.status}; the same key on "
        f"{second_route.key} [{second_scope}] -> {second.status} ({executed}, no replay marker, "
        f"different bytes); {first_route.key} still replays it"
    )


@check(
    id="C25",
    name="state: a reused idempotency key with a different body answers as the route declares",
    asserts=(
        "On every driveable idempotent route: a key reused with a different body is refused with "
        "idempotency_conflict where the route declares on_mismatch=conflict, and replays the stored "
        "answer marked as ignoring the body where it declares replay -- and executes nothing either way."
    ),
    requires=Requires(idempotent_example=True, credentials=True),
)
def a_reused_key_with_a_different_body_answers_as_declared(env: CheckEnv) -> str:
    """``on_mismatch``, in the direction each route declares.

    The ``conflict`` branch of the kernel's ``_replay`` was deleted outright
    and the matrix stayed green (konyklabs/roadmap#10, N-3d; tracked as
    konyklabs/roadmap#15): a reused key with new data was handed the old
    answer and a 200, which is precisely the silent wrong answer the 409
    exists to prevent. C19 sends the same body twice and cannot see it.

    Asked in the declared direction, like C09's signer bindings, because
    ``replay`` is real documented vendor behaviour and not a defect -- but a
    route that declares one and does the other has published a lie. The
    differing body is the example plus one extra field: the digest is taken
    at step 7 of the pipeline, before any handler could refuse the field, so
    the comparison sees a different request without the vendor's validation
    ever being involved.
    """
    driven: list[str] = []
    for index, route in enumerate(env.example_routes(methods=_MUTATING_METHODS, idempotent=True)):
        spec = dict(route.idempotency or {})
        declared = str(spec.get("on_mismatch", "conflict"))
        key = f"conformance-mismatch-probe-{index}"
        headers = env.authorized(route)
        first = env.client.call(route.method, route.probe_path, json_body=_keyed(route, key), headers=headers)
        require(
            200 <= first.status < 300,
            f"{route.key} refused its own published example_body: {first.status} "
            f"{first.error_kind!r} {first.text[:300]}. Nothing is stored under a key until something "
            f"has succeeded, so the mismatch contract cannot be asked of this route.",
        )
        seq_after_first = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])

        changed = _keyed(route, key, {"conformance_mismatch": "a field the first request did not carry"})
        second = env.client.call(route.method, route.probe_path, json_body=changed, headers=headers)
        if declared == "conflict":
            require(
                second.error_kind == _IDEMPOTENCY_CONFLICT,
                f"{route.key} declares on_mismatch='conflict' and answered {second.status} with "
                f"x-unit-error={second.error_kind!r} to its key reused with a DIFFERENT body, expected "
                f"{_IDEMPOTENCY_CONFLICT!r}. core/kernel/unit.py::_replay compares the stored request "
                f"digest and must refuse a mismatch on a conflict route; replaying instead hands a "
                f"consumer who changed their request the answer to the one they did not send.",
            )
            require(
                _REPLAY_HEADER not in second.headers,
                f"{route.key} refused the mismatched body and still stamped {_REPLAY_HEADER}: a "
                f"refusal replays nothing.",
            )
        elif declared == "replay":
            require(
                second.status == first.status and second.body == first.body,
                f"{route.key} declares on_mismatch='replay' and answered {second.status} with different "
                f"bytes to its key reused with a different body; the declaration promises the stored "
                f"answer, status and body, with the new request dropped.",
            )
            require(
                second.headers.get(_REPLAY_HEADER) and second.headers.get(_IGNORED_BODY_HEADER),
                f"{route.key} replayed a mismatched body without both {_REPLAY_HEADER!r} and "
                f"{_IGNORED_BODY_HEADER!r}. 'You got a 200 and your update was discarded' is documented "
                f"vendor behaviour a consumer has no other way to observe; both are stamped in "
                f"core/kernel/unit.py::_replay.",
            )
        else:
            require(False, f"{route.key} publishes on_mismatch={declared!r}, which is neither 'conflict' nor 'replay'.")
        require(
            int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"]) == seq_after_first,
            f"{route.key}: the mismatched reuse moved the journal past seq {seq_after_first}. Whatever "
            f"the declared answer to a mismatch is, the handler must not run for it.",
        )
        driven.append(f"{route.key} [{declared}] -> {second.status}:{second.error_kind or 'replay'}")
    require(driven, "no idempotent route publishing an example_body was enabled, so nothing was driven.")
    return "; ".join(driven) + "; journal unmoved by every mismatch"


# ---------------------------------------------------------------------------
# C26 -- a declared page walk repeats nothing and loses nothing.
# ---------------------------------------------------------------------------

_WALK_PAGE_SIZE = 1
"""One row per page: the smallest page is the one where an overlap or a lost
row is most visible, and the one a broken offset is least able to hide in."""


def _dig(document: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(document, Mapping):
            return None
        document = document.get(part)
    return document


def _row_ids(rows: Any, route: RouteRow, id_path: str) -> list[str]:
    require(
        isinstance(rows, list),
        f"{route.key} declares its rows at {dict(route.pagination or {})['items_path']!r} and the "
        f"response holds {type(rows).__name__} there, not a list. Fix PaginationSpec.items_path in "
        f"the same file as the route.",
    )
    ids: list[str] = []
    for row in rows:
        value = _dig(row, id_path)
        require(
            value is not None,
            f"{route.key}: a row carries no {id_path!r} ({str(row)[:120]}). Rows are compared by "
            f"the id PaginationSpec.id_path names; fix the path or the projection.",
        )
        ids.append(str(value))
    return ids


def _fetch_page(env: CheckEnv, route: RouteRow, headers: dict[str, str], params: dict[str, Any]) -> Any:
    spec = dict(route.pagination or {})
    if spec["where"] == "body":
        body = dict(route.example_body or {})
        body.update(params)
        answered = env.client.call(route.method, route.probe_path, json_body=body, headers=headers)
    else:
        query = {name: str(value) for name, value in params.items()}
        body = {} if route.method in _MUTATING_METHODS else MISSING
        answered = env.client.call(route.method, route.probe_path, json_body=body, query=query, headers=headers)
    require(
        answered.status == 200,
        f"{route.key} answered {answered.status} {answered.error_kind!r} to a page request "
        f"{params}: {answered.text[:200]}. A route declaring a PaginationSpec must answer its own "
        f"page parameters; if it also needs a body, publish one as Route.example_body.",
    )
    return answered.json()


@check(
    id="C26",
    name="state: a declared page walk repeats no row and loses none",
    asserts=(
        "For every enabled route declaring a PaginationSpec that holds two or more rows: walking it "
        "one row per page yields each id exactly once, and the union of the pages equals the "
        "unpaged listing."
    ),
    requires=Requires(paginated_route=True, credentials=True),
)
def declared_pages_never_overlap_and_lose_nothing(env: CheckEnv) -> str:
    """Pagination as a consumer meets it: through the vendor's own list route.

    Repeating the last row of each page as the first of the next left the
    matrix green (konyklabs/roadmap#10, N-3e; tracked as konyklabs/roadmap#15)
    while five unit tests went red. C20 pages the store through the control
    plane and proves the cursor's fingerprint; it cannot see a vendor's own
    list handler slicing wrongly, and a vendor whose lists use offsets never
    touches the store's cursor at all. So this walks whatever the route table
    declares, in the style each route declares, and compares the walk against
    the same route asked once for everything.

    The reference listing is the route's own unpaged answer rather than a
    count from ``/__unit/state``: a list route legitimately filters -- a
    merchant's orders, one location's search -- and only the route knows what
    it should list. What it must not do is disagree with itself.
    """
    walked: list[str] = []
    too_small: list[str] = []
    problems: list[str] = []
    for route in env.paginated_routes():
        spec = dict(route.pagination or {})
        id_path = str(spec["id_path"])
        headers = env.authorized(route)

        whole = _row_ids(_dig(_fetch_page(env, route, headers, {}), str(spec["items_path"])), route, id_path)
        duplicates = sorted({value for value in whole if whole.count(value) > 1})
        if duplicates:
            problems.append(f"{route.key}: the unpaged listing itself repeats {duplicates}.")
            continue
        if len(whole) < 2:
            too_small.append(f"{route.key} ({len(whole)} row)")
            continue

        seen: list[str] = []
        pages = 0
        limit = str(spec["limit_param"])
        if spec["style"] == "cursor":
            cursor: Any = None
            while pages <= len(whole) + 1:
                params: dict[str, Any] = {limit: _WALK_PAGE_SIZE}
                if cursor is not None:
                    params[str(spec["cursor_param"])] = cursor
                document = _fetch_page(env, route, headers, params)
                pages += 1
                seen.extend(_row_ids(_dig(document, str(spec["items_path"])), route, id_path))
                cursor = _dig(document, str(spec["next_cursor_path"]))
                if not cursor:
                    break
        else:
            offset = 0
            while pages <= len(whole) + 1:
                params = {limit: _WALK_PAGE_SIZE, str(spec["offset_param"]): offset}
                pages += 1
                got = _row_ids(_dig(_fetch_page(env, route, headers, params), str(spec["items_path"])), route, id_path)
                if not got:
                    break
                seen.extend(got)
                offset += len(got)

        repeated = sorted({value for value in seen if seen.count(value) > 1})
        if repeated:
            problems.append(
                f"{route.key}: walking {spec['style']}-paginated pages of {_WALK_PAGE_SIZE} served "
                f"{repeated} more than once across {pages} pages ({seen}). A consumer looping until the "
                f"cursor is absent receives those rows twice with a 200 and no error anywhere; the next "
                f"page must start at the row AFTER the last one served."
            )
        missing = sorted(set(whole) - set(seen))
        extra = sorted(set(seen) - set(whole))
        if missing or extra:
            problems.append(
                f"{route.key}: the union of {pages} pages is not the unpaged listing -- missing "
                f"{missing}, unexpected {extra}. Pages must partition exactly the rows a single "
                f"request lists."
            )
        if pages > len(whole) + 1:
            problems.append(
                f"{route.key}: the walk did not terminate within {len(whole) + 1} pages over {len(whole)} "
                f"rows. A next cursor is emitted only when there is genuinely a next page, and an "
                f"offset past the end answers an empty page."
            )
        walked.append(f"{route.key} ({len(whole)} rows, {pages} pages, {spec['style']})")

    require(not problems, "\n".join(problems))
    if not walked:
        raise ConformanceSkip(
            f"every declared paginated route holds fewer than two rows on profile {env.profile!r} "
            f"({', '.join(too_small)}), so no page boundary exists to walk across; seed more rows"
        )
    tail = f"; too small to walk: {', '.join(too_small)}" if too_small else ""
    return f"walked {len(walked)} route(s) one row per page with no repeat and no loss: {'; '.join(walked)}{tail}"
