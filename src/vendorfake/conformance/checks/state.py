"""C06, C07, C13, C19, C20, C22, C24, C25, C26, C36 -- state is reproducible,
append-only, honestly gated, deduplicated per operation, paged without overlap,
and seeded from a document an overlay may narrow but not invent keys in.

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
    "a_seed_overlay_cannot_invent_a_collection",
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
_PROOF_THE_LOOKUP_MISSED = frozenset(
    {
        "bad_request",
        "missing_field",
        "invalid_value",
        "invalid_transition",
        "version_conflict",
        "conflict",
        "invalid_cursor",
    }
)
"""Kinds only the handler -- step 8 of core/kernel/unit.py::_run_pipeline,
after the step-7 key lookup -- can produce for a probe carrying its key, so a
partner refusal in this set genuinely proves the lookup missed. Everything
else proves nothing: the router's 404 and 405, the capability gate, the auth
step and the pre-auth faults all fire before the lookup, and ``internal`` can
fire anywhere, a crashed hook included. This used to be a refuse-list of the
pre-handler kinds; it is an allow-list now so that a kind it has never heard
of -- every 5xx today, any kind added later -- defaults to "not proof"
instead of silently becoming proof (konyklabs/roadmap#46). ``missing_field``
qualifies because every probe carries its key at the published path --
written along a dotted ``key_path`` the same way step 7's ``dot_get`` reads
it -- so the step-7 raise for an absent required key cannot be the one
answering; a partner complaining of a missing field is complaining about some
other field, from inside the handler. ``bad_request`` qualifies because on a
vendor route only handlers raise it (request validation -- a probe path's
placeholder segment failing a handler's guid check is the shipped instance;
the core's one raise site is an internal control route the router
short-circuits before the pipeline). ``not_found`` stays out even though a handler can
raise it too, because from outside it is one kind with the router's: a
partner that would 404 its probe entity must publish example_params naming a
seeded one (Route.example_params)."""

_ABSENT_COLLECTION = "conformance-absent-collection"
"""A seed-overlay key no vendor's seed document can have.

Not a plausible near-miss of a real collection name, deliberately, and for the
same reason ``PROBE_SEGMENT`` is not a plausible id: a probe that could
accidentally be right proves nothing when it is refused. It carries a hyphen,
which no shipped seed document uses in a top-level key, so it stays wrong for a
vendor this suite has never seen.
"""

_VALID_COLLECTIONS_MARKER = "Valid collections:"
"""The phrase the refusal carries before its listing.

Part of the contract rather than of one implementation's prose: the clause is
"the message names the offending collection AND the vendor's valid ones", and
a check with no agreed marker could only assert the first half. The Python
implementation writes it in ``core/config/overlay.py``.
"""

_DIGEST_PREFIX = "sha256:"
"""What ``seed_overlay.digest`` is prefixed with, named on the wire so a
consumer comparing two runs can tell a changed algorithm from a changed
overlay. Spelled here rather than imported from ``core.util`` because it is a
*wire* value, the same reason ``control_plane.py``'s own constants are."""


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
    # _keyed writes along a dotted key_path the way step 7 reads it; a flat
    # write would miss and draw a step-7 missing_field instead of executing.
    body = dict(route.example_body or {}) if route.idempotency is None else _keyed(route, "conformance-journal-probe")
    created = env.client.call(
        route.method,
        route.example_path,
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
    body = _keyed(route, "conformance-idempotency-probe")
    headers = env.authorized(route)

    first = env.client.call(route.method, route.example_path, json_body=body, headers=headers)
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

    second = env.client.call(route.method, route.example_path, json_body=body, headers=headers)
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
    """The route's example body (or nothing) carrying ``key`` at its declared path.

    Written ALONG the declared ``key_path``, not as one flat dict key: step 7
    reads it back with ``dot_get``, which splits on dots, so a vendor
    declaring ``order.idempotency_key`` must find the key nested. A flat write
    would make step 7 miss it and raise its own ``missing_field`` -- which the
    allow-list counts as proof the lookup missed, re-opening the vacuity this
    check exists to close (konyklabs/roadmap#46 review). Each level along the
    path is copied before descent so the memoised example body is never
    mutated.
    """
    body = dict(route.example_body or {})
    node = body
    steps = str(dict(route.idempotency or {})["key_path"]).split(".")
    for step in steps[:-1]:
        prev = node.get(step)
        node[step] = dict(prev) if isinstance(prev, dict) else {}
        node = node[step]
    node[steps[-1]] = key
    if extra:
        body.update(extra)
    return body


@check(
    id="C24",
    name="state: an idempotency key is scoped to its operation",
    asserts=(
        "For EVERY idempotent route that publishes an example, a key spent there is invisible from "
        "every other declared scope (no conflict, no replay marker, its own answer) and visible "
        "from every route sharing its scope, in the declared on_mismatch direction; and the "
        "declarations themselves hold -- scopes not all one string, no shared scope spanning "
        "capabilities, mixing on_mismatch, or verifiable by nothing. A partner's answer counts "
        "only when it proves the lookup missed -- a fresh success, or a post-lookup refusal "
        "that leaves the journal unmoved; anything else, a 5xx included, is evidence of "
        "nothing and fails."
    ),
    requires=Requires(two_idempotent_routes=True, credentials=True),
)
def an_idempotency_key_is_scoped_to_its_operation(env: CheckEnv) -> str:
    """The ``scope`` field of every IdempotencySpec, asked rather than read.

    Collapsing the store's key from ``f"{scope} {key}"`` to ``key`` made a
    PayOrder sent under a key a CreateOrder had used answer with the
    CreateOrder body and a 200, and the matrix stayed green
    (konyklabs/roadmap#10, N-3c; tracked as konyklabs/roadmap#15): C19 sends
    its key to one route and never to a second.

    This is a CLASS check, for the same reason C17 is (and after the same
    mistake was made here first: the initial version selected one route pair,
    and collapsing every scope except that pair's stayed green -- N-3b's
    shape, found by review). Every example-bearing idempotent route spends a
    key; every declared scope is then probed against every spent key it must
    not see, and every route sharing a spent key's scope is probed for the
    visibility the shared declaration promises. Declaration rules run first,
    because behaviour probes over incoherent declarations prove nothing:

    * **Not all one string.** N operations declaring a single scope have
      removed the namespace the field exists for.
    * **A shared scope stays inside one capability.** A namespace spanning
      capabilities means switching one capability off half-disables another's
      replay space; an alias pair -- the legitimate share -- lives where its
      operation lives.
    * **A shared scope declares one on_mismatch.** Two routes sharing a
      namespace but promising different mismatch answers is a promise that
      depends on which alias the retry happens to hit.
    * **A shared scope has a drivable member.** A share none of whose routes
      publishes an example is a share nothing can verify, and an unverifiable
      declaration is exactly where the collapse hid from the paired version
      of this check.

    Two rules from earlier review rounds are kept, the second inverted and
    widened by konyklabs/roadmap#46: a partner answer of
    ``idempotency_conflict`` is AFFIRMATIVE evidence the key was found in
    that scope (the mismatch branch only runs on a stored record), and a
    partner answer counts as "the route answered for itself" only when it
    proves the request got past the key lookup -- a 2xx, or a kind in
    :data:`_PROOF_THE_LOOKUP_MISSED`. The journal is read around every probe:
    neither a post-lookup refusal nor a shared-scope declared-direction
    answer may append an entry -- a partner that refuses a request and
    journals it anyway executed what it refused, a pass condition weaker
    than its description (N-3's shape). The positive direction, "a fresh
    success journals", is deliberately not asserted: a no-op 2xx is real
    vendor behaviour (Square's batch-create drops an unchanged count under
    ignore_unchanged_counts and commits nothing).
    Keys are spent first and probed after, because one route (UpdateOrder)
    pins its example to the seed's entity version and can succeed only once
    per unit; the probes are read-mostly and order-independent.

    The journal brackets are race-free by construction, and the argument is
    load-bearing: the seq only moves in ``Store.append_journal``, which is
    called solely from Collection mutations under the store lock; webhook
    dispatch is a listener invoked from INSIDE that call and keeps its
    delivery records outside the store, and this check drives one probe at a
    time on serialized routes. A vendor whose retry machinery wrote entities
    from a background thread would break the bracket, and should fail loudly
    here rather than quietly widening it.
    """
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    routes = env.idempotent_routes()
    groups: dict[str, list[RouteRow]] = {}
    for row in routes:
        groups.setdefault(str(dict(row.idempotency or {})["scope"]), []).append(row)

    problems: list[str] = []
    if len(routes) > 1 and len(groups) == 1:
        only = next(iter(groups))
        problems.append(
            f"every enabled idempotent route declares the single scope {only!r} "
            f"({', '.join(sorted(row.key for row in routes))}). The scope is the namespace that "
            f"keeps one operation's stored answers away from another's; N operations declaring one "
            f"string have removed it."
        )
    for scope, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        capabilities = sorted({member.capability for member in members})
        if len(capabilities) > 1:
            problems.append(
                f"scope {scope!r} is shared by routes across capabilities {capabilities} "
                f"({', '.join(sorted(member.key for member in members))}). A replay namespace that "
                f"spans capabilities is half-disabled whenever a profile switches one of them off; "
                f"an alias pair shares a scope inside the capability its operation lives in."
            )
        directions = sorted({str(dict(member.idempotency or {}).get("on_mismatch", "conflict")) for member in members})
        if len(directions) > 1:
            problems.append(
                f"scope {scope!r} is shared by routes declaring different on_mismatch answers "
                f"{directions}; which answer a reused key gets would depend on which alias the "
                f"retry hits."
            )
        if not any(member.example_body is not None for member in members):
            problems.append(
                f"scope {scope!r} is shared by {len(members)} routes "
                f"({', '.join(sorted(member.key for member in members))}) and none publishes an "
                f"example, so nothing can spend a key there and the declared share can never be "
                f"verified -- which is exactly where a collapsed store hides. Publish example_body "
                f"(and example_params if the path names an entity) on one of them."
            )
    require(not problems, "\n".join(problems))

    sources = [row for row in env.example_routes(methods=_MUTATING_METHODS, idempotent=True)]
    require(sources, "no idempotent route publishes an example, so no key can be spent anywhere.")

    spent: list[tuple[RouteRow, str, Any]] = []
    for index, source in enumerate(sources):
        key = f"conformance-scope-probe-{index}"
        first = env.client.call(
            source.method, source.example_path, json_body=_keyed(source, key), headers=env.authorized(source)
        )
        if not 200 <= first.status < 300:
            problems.append(
                f"{source.key} refused its own published example_body: {first.status} "
                f"{first.error_kind!r} {first.text[:200]}. No key can be spent in scope "
                f"{dict(source.idempotency or {})['scope']!r}, so nothing about it was asked."
            )
            continue
        spent.append((source, key, first))

    isolation_probes = 0
    shares_verified = 0
    for index, (source, key, first) in enumerate(spent):
        scope = str(dict(source.idempotency or {})["scope"])
        for member in groups[scope]:
            if member.key == source.key:
                continue
            seq_before = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])
            seen = env.client.call(
                member.method, member.example_path, json_body=_keyed(member, key), headers=env.authorized(member)
            )
            seq_after = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])
            direction = str(dict(member.idempotency or {}).get("on_mismatch", "conflict"))
            visible = (
                seen.error_kind == _IDEMPOTENCY_CONFLICT
                if direction == "conflict"
                else bool(seen.headers.get(_REPLAY_HEADER)) and seen.body == first.body
            )
            if not visible:
                problems.append(
                    f"{member.key} declares the same scope {scope!r} as {source.key} and answered "
                    f"{seen.status} x-unit-error={seen.error_kind!r} to that route's key with its own "
                    f"body -- not the {direction!r} answer a shared namespace promises. Either the "
                    f"share is declared and not real (two stores behind one declaration) or the "
                    f"aliases disagree; a consumer retrying on the other path is told nothing was "
                    f"ever sent."
                )
            else:
                shares_verified += 1
                if seq_after != seq_before:
                    problems.append(
                        f"{member.key} answered scope {scope!r}'s key in its declared {direction!r} "
                        f"direction and still moved the journal from seq {seq_before} to {seq_after}. "
                        f"A conflict executes nothing and a replay is the stored answer returned, so "
                        f"whichever was declared, the handler must not have run for it."
                    )
        for other_scope, members in sorted(groups.items()):
            if other_scope == scope:
                continue
            target = members[index % len(members)]
            seq_before = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])
            answer = env.client.call(
                target.method, target.example_path, json_body=_keyed(target, key), headers=env.authorized(target)
            )
            seq_after = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])
            isolation_probes += 1
            if answer.error_kind == _IDEMPOTENCY_CONFLICT:
                problems.append(
                    f"{target.key} (scope {other_scope!r}) answered {_IDEMPOTENCY_CONFLICT!r} to a key "
                    f"it had never seen -- the key was spent on {source.key} (scope {scope!r}) with a "
                    f"different body. A conflict is the mismatch branch of core/kernel/unit.py::_replay, "
                    f"which only runs on a record FOUND in this route's scope: the store found the "
                    f"other operation's record, which is the collapse this contract exists to catch."
                )
                continue
            if _REPLAY_HEADER in answer.headers or _IGNORED_BODY_HEADER in answer.headers:
                problems.append(
                    f"{target.key} (scope {other_scope!r}) answered {answer.status} carrying an "
                    f"idempotent-replay marker for a key spent on {source.key} (scope {scope!r}): one "
                    f"operation's stored answer served for another."
                )
                continue
            if answer.body == first.body:
                problems.append(
                    f"{target.key} (scope {other_scope!r}) answered the exact bytes {source.key} "
                    f"(scope {scope!r}) stored under the same key: a consumer reusing a key across "
                    f"operations receives a body from the wrong endpoint with nothing to notice it by."
                )
                continue
            if 200 <= answer.status < 300:
                # A fresh success is the route answering for itself; the leak
                # clauses above already refused a marker or the stored bytes.
                # Nothing is asserted of the journal here because a no-op 2xx
                # is real vendor behaviour (Square's batch-create with
                # ignore_unchanged_counts drops a matching count and commits
                # nothing), so "a success journals" does not hold in general.
                continue
            if answer.error_kind not in _PROOF_THE_LOOKUP_MISSED:
                problems.append(
                    f"{target.key} (scope {other_scope!r}) answered {answer.status} with "
                    f"x-unit-error={answer.error_kind!r}, which is not one of the kinds only the "
                    f"post-lookup handler can produce for a keyed probe -- a routing, capability, "
                    f"auth or fault refusal fires before step 7 of "
                    f"core/kernel/unit.py::_run_pipeline, and a 5xx can fire anywhere -- before the "
                    f"lookup or from a crash inside the handler. Nothing about key scoping was asked "
                    f"of it. Publish example_params naming a seeded entity for it, fix whatever "
                    f"refused the request upstream of the lookup, or fix the crash."
                )
                continue
            if seq_after != seq_before:
                problems.append(
                    f"{target.key} (scope {other_scope!r}) refused another operation's key with "
                    f"x-unit-error={answer.error_kind!r} and still moved the journal from seq "
                    f"{seq_before} to {seq_after}. No refusal commits a mutation: whatever was "
                    f"journalled ran for a request the route says it refused."
                )
        again = env.client.call(
            source.method, source.example_path, json_body=_keyed(source, key), headers=env.authorized(source)
        )
        if not (again.headers.get(_REPLAY_HEADER) and again.body == first.body):
            problems.append(
                f"after its key was probed against every other scope, {source.key} no longer replays "
                f"it ({again.status}, {_REPLAY_HEADER}={again.headers.get(_REPLAY_HEADER)!r}): some "
                f"probe overwrote or evicted the record, so scopes do not isolate in both directions."
            )
    require(not problems, "\n".join(problems))
    return (
        f"{len(spent)} keys spent across {len(groups)} declared scopes; {isolation_probes} "
        f"cross-scope probes saw no conflict, no marker and none of the stored bytes, each answered "
        f"past the lookup with the journal holding on every refusal; "
        f"{shares_verified} shared-scope alias(es) saw the record in the declared direction without "
        f"journalling; every spent key still replays on its own route"
    )


@check(
    id="C25",
    name="state: a reused idempotency key with a different body answers as the route declares",
    asserts=(
        "On every idempotent route publishing an example: a key reused with a different body is "
        "refused with idempotency_conflict where the route declares on_mismatch=conflict, and "
        "replays the stored answer marked as ignoring the body where it declares replay -- "
        "executing nothing either way. Every on_mismatch value any enabled route declares must be "
        "drivable through some example, or the declared direction was asserted by nothing."
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
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    driven: list[str] = []
    driven_directions: set[str] = set()
    for index, route in enumerate(env.example_routes(methods=_MUTATING_METHODS, idempotent=True)):
        spec = dict(route.idempotency or {})
        declared = str(spec.get("on_mismatch", "conflict"))
        key = f"conformance-mismatch-probe-{index}"
        headers = env.authorized(route)
        first = env.client.call(route.method, route.example_path, json_body=_keyed(route, key), headers=headers)
        require(
            200 <= first.status < 300,
            f"{route.key} refused its own published example_body: {first.status} "
            f"{first.error_kind!r} {first.text[:300]}. Nothing is stored under a key until something "
            f"has succeeded, so the mismatch contract cannot be asked of this route.",
        )
        seq_after_first = int(env.get_json(f"{CONTROL_PREFIX}journal")["seq"])

        changed = _keyed(route, key, {"conformance_mismatch": "a field the first request did not carry"})
        second = env.client.call(route.method, route.example_path, json_body=changed, headers=headers)
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
        driven_directions.add(declared)
    require(driven, "no idempotent route publishing an example_body was enabled, so nothing was driven.")
    undriven = sorted(
        {str(dict(row.idempotency or {}).get("on_mismatch", "conflict")) for row in env.idempotent_routes()}
        - driven_directions
    )
    require(
        not undriven,
        f"some enabled route declares on_mismatch={undriven} and no route declaring it publishes an "
        f"example this check can drive, so the declared direction was asserted by nothing -- exactly "
        f"how the replay branch went unexercised until the review of konyklabs/roadmap#15. Publish "
        f"example_body (and example_params, if the path names an entity) on one route per declared "
        f"direction.",
    )
    return (
        "; ".join(driven)
        + "; journal unmoved by every mismatch; directions driven: "
        + ", ".join(sorted(driven_directions))
    )


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
        "Every enabled route declaring a walkable PaginationSpec is walked one row per page: each "
        "id appears exactly once and the union of the pages equals the unpaged listing. A walkable "
        "route with fewer than two rows is a failure, not a smaller walk; walkable=false routes "
        "are excused only by their written reason."
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
    excused: list[str] = []
    problems: list[str] = []
    for route in env.paginated_routes():
        spec = dict(route.pagination or {})
        if not spec.get("walkable", True):
            reason = str(spec.get("unwalkable_reason", "")).strip()
            if reason:
                excused.append(f"{route.key} -- {reason}")
            else:
                problems.append(
                    f"{route.key} declares walkable=false with no unwalkable_reason. The opt-out "
                    f"exists so a paginating route is excused on the record, never silently; an "
                    f"empty reason is silence with a flag on it."
                )
            continue
        id_path = str(spec["id_path"])
        headers = env.authorized(route)

        whole = _row_ids(_dig(_fetch_page(env, route, headers, {}), str(spec["items_path"])), route, id_path)
        duplicates = sorted({value for value in whole if whole.count(value) > 1})
        if duplicates:
            problems.append(f"{route.key}: the unpaged listing itself repeats {duplicates}.")
            continue
        if len(whole) < 2:
            problems.append(
                f"{route.key} declares a walkable PaginationSpec and lists {len(whole)} row(s) on "
                f"profile {env.profile!r}, so no page boundary exists and the declaration was never "
                f"exercised. A route the walk cannot walk is a route this contract silently excludes "
                f"-- the same subset flaw C17 had. Seed a second row for it, or declare "
                f"walkable=false with the reason."
            )
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

        if pages < 2:
            problems.append(
                f"{route.key}: {len(whole)} rows at a declared page size of {_WALK_PAGE_SIZE} came "
                f"back in {pages} page(s), so no page boundary was ever crossed and nothing about "
                f"pagination was asked. A route that ignores {spec['limit_param']!r} serves "
                f"everything on page one with no repeat and no loss -- the one shape the other "
                f"clauses cannot see; with two or more rows and a one-row page, a second page is "
                f"the least the declaration promises."
            )
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
    tail = f"; excused by declaration: {'; '.join(excused)}" if excused else ""
    if not walked:
        # A SKIP, not a pass: every declared route opted out, so the contract
        # was never asked and a pass would certify a walk that walked nothing
        # (review round 2 of konyklabs/roadmap#15). Under --strict the skip is
        # then held against the target's matrix -- a vendor whose every list
        # is excused declares that, per profile in expected_skips or wholesale
        # in ConformanceTarget.inapplicable, and the inapplicable guard fails
        # the day a walkable list appears and the declaration goes stale.
        raise ConformanceSkip(
            f"every paginated route this profile declares opts out of the walk{tail or '; none declares one at all'}"
        )
    return f"walked {len(walked)} route(s) one row per page with no repeat and no loss: {'; '.join(walked)}{tail}"


@check(
    id="C36",
    name="state: a seed overlay is applied, and may not invent a collection",
    asserts=(
        "A unit APPLIES a seed overlay -- a non-empty overlay emptying a collection the profile seeds "
        "leaves that collection's entities gone from GET /__unit/state -- reports it at GET /__unit/info "
        "as active with a digest, reports no overlay when none was given, and REFUSES one whose top-level "
        "key is not a collection of the seed document, while the unit is being built and before any "
        "request, with a message naming the offending key and listing the collections that do exist."
    ),
    requires=Requires(seed=True, seed_overlay=True),
)
def a_seed_overlay_cannot_invent_a_collection(env: CheckEnv) -> str:
    """The one contract about a *partial* document, and why it needs one.

    An overlay is the only input to a unit that has nothing to be wrong
    against. A whole seed document is validated by the vendor's hydration --
    a missing field or a wrong shape shows up as an entity that is not there.
    A partial document naming ``order`` for a vendor whose collection is
    ``orders`` merges cleanly, hydrates nothing, and presents an hour later
    as "the fake ignored my scenario", with no message anywhere. So the
    refusal is a *contract*, not an implementation detail, and it is asserted
    at the moment it has to happen: while the unit is built.

    Four claims. The refusal comes SECOND rather than last, because the
    positive control is built from what the refusal's own message reports:
    the collections this vendor's seed carries. That is the only
    vendor-independent way for a clause in this package to name one, and
    naming one is what the last claim needs.

    THE POSITIVE CONTROL IS NON-EMPTY AND OBSERVABLE, and review is why. It
    used to be ``{}``, which asserted only that an overlay *arrived*:
    ``active`` and a digest are computed from the document the unit was
    handed, not from anything it did with it. An implementation that reported
    both faithfully and then dropped the overlay on the floor passed this
    clause, which is the one failure the whole feature exists to prevent --
    measured, by replacing the merge with ``return document`` and watching
    every profile still pass. So the overlay now empties a collection the
    profile actually seeds and the check reads ``GET /__unit/state`` back:
    the count has to move, and a merge that no-ops fails.

    The collection is chosen, never named: the candidates are the names the
    refusal listed that also appear in this profile's own entity counts, and
    the first that hydrates is used. ``[]`` rather than an added entity
    because "replace the array whole" is the merge rule an empty array
    exercises with no knowledge of what an entity of that collection looks
    like -- which is knowledge no vendor-independent clause can have.

    The refusal message is parsed for its ``Valid collections:`` listing
    rather than merely searched for the offending key, because "names the
    vendor's valid collections" is half the clause: a message that says only
    "unknown collection 'ordrs'" leaves the reader to go and find the seed
    document, which is exactly the trip the message exists to save. The
    phrase is part of the contract and ``core/config/overlay.py`` writes it.
    """
    baseline = env.state()["entities"]
    plain = env.info().get("seed_overlay")
    plain_block: Mapping[str, Any] = plain if isinstance(plain, Mapping) else {}
    require(
        plain_block.get("active") is False and plain_block.get("digest") is None,
        f"a unit built with NO seed overlay reports seed_overlay={plain!r}; it must report "
        f"active=false and digest=null, or 'active' says nothing about this run.",
    )

    try:
        with env.seed_overlay_unit({_ABSENT_COLLECTION: {}}) as started:
            state = started.state()
    except ConformanceSkip:
        raise
    except Exception as refusal:
        message = str(refusal)
    else:
        require(
            False,
            f"a unit STARTED on a seed overlay naming {_ABSENT_COLLECTION!r}, which is not one of the "
            f"seed document's collections (it hydrated {state['entities']}). A partial document has "
            f"nothing else to be wrong against: a mistyped collection merges cleanly and hydrates "
            f"nothing, so it must be refused where the overlay is applied -- "
            f"core/config/overlay.py::apply_seed_overlay.",
        )
        raise AssertionError("unreachable: require(False) raises")  # pragma: no cover

    require(
        _ABSENT_COLLECTION in message,
        f"the refusal for an unknown overlay collection does not name it: {message!r}. The message is "
        f"the whole value of the refusal -- name the offending key "
        f"(core/config/overlay.py::apply_seed_overlay).",
    )
    _, marker, listing = message.partition(_VALID_COLLECTIONS_MARKER)
    require(
        marker != "",
        f"the refusal does not carry {_VALID_COLLECTIONS_MARKER!r}: {message!r}. Naming the offending "
        f"key without listing the ones that exist sends the reader to the seed document, which is the "
        f"trip the message exists to save.",
    )
    named = tuple(part.strip().rstrip(".") for part in listing.strip().rstrip(".").split(",") if part.strip())
    require(
        named,
        f"the refusal lists no valid collections: {message!r}. A seed document that hydrated entities "
        f"has collections to name.",
    )
    require(
        _ABSENT_COLLECTION not in named,
        f"the refusal lists {_ABSENT_COLLECTION!r} among the valid collections: {message!r}. The key it "
        f"refused cannot also be one it accepts.",
    )

    # The positive control, built from the listing the refusal just gave: a
    # collection this vendor's seed carries AND whose entities this profile
    # counts, so that emptying it is visible without knowing which vendor
    # this is or what one of its entities looks like.
    candidates = tuple(name for name in named if isinstance(baseline.get(name), int) and baseline[name] > 0)
    require(
        candidates,
        f"none of the {len(named)} collection(s) the refusal listed ({', '.join(named)}) appears in this "
        f"profile's own entity counts ({sorted(baseline)}), so no overlay can be shown to have been "
        f"APPLIED rather than merely accepted. A seed collection that hydrates entities must be counted "
        f"at GET /__unit/state under its own name for that to be checkable.",
    )
    refused_to_build: list[str] = []
    applied: tuple[str, Mapping[str, Any], Mapping[str, Any]] | None = None
    for candidate in candidates:
        try:
            with env.seed_overlay_unit({candidate: []}) as emptied:
                entities = emptied.state()["entities"]
                overlaid_info = emptied.info()
        except ConformanceSkip:
            raise
        except Exception as refusal:
            refused_to_build.append(f"{candidate} ({type(refusal).__name__}: {refusal})")
            continue
        applied = (candidate, entities, overlaid_info)
        break
    if applied is None:
        # Not a failure: a seed whose every countable collection is referred
        # to by another cannot have one emptied without breaking the
        # document's own integrity, and hydration refusing that is correct
        # behaviour rather than a broken merge. Under --strict the skip is
        # then held against the target's matrix, so it cannot go unnoticed.
        raise ConformanceSkip(
            "no seeded collection could be emptied by an overlay without the seed failing to hydrate, so "
            "there is no vendor-independent way to observe the merge here: " + "; ".join(refused_to_build)
        )
    emptied_name, emptied_entities, emptied_info = applied

    reported = emptied_info.get("seed_overlay")
    require(
        isinstance(reported, Mapping),
        f"GET /__unit/info published seed_overlay={reported!r} on a unit built with an overlay. "
        f"It must be an object with 'active' and 'digest'; add it in core/control/plane.py::info.",
    )
    overlay_block: Mapping[str, Any] = reported if isinstance(reported, Mapping) else {}
    require(
        overlay_block.get("active") is True,
        f"a unit built with a seed overlay reports seed_overlay.active={overlay_block.get('active')!r} at "
        f"GET /__unit/info. A consumer reading a report cannot otherwise tell a run on the shipped "
        f"scenario from a run on an overridden one.",
    )
    digest = overlay_block.get("digest")
    require(
        isinstance(digest, str) and digest.startswith(_DIGEST_PREFIX),
        f"seed_overlay.digest is {digest!r}; it must be {_DIGEST_PREFIX!r} followed by the hex "
        f"SHA-256 of the overlay's canonical JSON, so that two runs can be compared without the "
        f"overlay's contents ever being published.",
    )
    require(
        emptied_entities.get(emptied_name, 0) == 0,
        f"an overlay of {{{emptied_name!r}: []}} was accepted -- reported active with a digest -- and the "
        f"unit still hydrated {emptied_entities.get(emptied_name)!r} {emptied_name} (it had "
        f"{baseline[emptied_name]!r} without the overlay). The overlay was reported and not APPLIED, "
        f"which is the failure the whole feature exists to prevent: a consumer's scenario is accepted, "
        f"fingerprinted in every report, and ignored. Merge it into the seed document before the store is "
        f"hydrated -- core/config/overlay.py::merge_seed, called from load_profile.",
    )

    return (
        f"an overlay emptying {emptied_name!r} left 0 of the {baseline[emptied_name]} the profile seeds, "
        f"reported active with a {_DIGEST_PREFIX} digest, a unit with none reports active=false, and "
        f"{_ABSENT_COLLECTION!r} is refused before the unit starts, "
        f"naming {len(named)} valid collection(s): {', '.join(named)}"
    )
