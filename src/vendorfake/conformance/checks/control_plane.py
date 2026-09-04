"""C01, C30, C31, C32, C33 -- the unit describes itself, describing is free, and
it says so when nothing answered.

Every other contract reads the control plane to aim itself, so C01 runs
first and states the minimum: the unit is alive, it says what it is, and it
publishes its own surface. A unit that fails C01 cannot be examined at all,
and every later failure would be a consequence rather than a finding.

C30 through C32 are the other half of that bargain (konyklabs/roadmap#42):
looking at the fake must not change what it says next. Toast's error
catalogue shipped drawing twenty request ids and a live rate-limit epoch per
GET, because the description was rendered by calling the real refusal path --
a read-only diagnostic call renumbered every id in the rest of a consumer's
scenario, which is the determinism this project sells. The instance was
fixed on konyklabs/vendorfake#31; these contracts close the class.

C33 is the other direction: what the unit says about a request it could not
answer. A vendor's own 404 names nothing -- it cannot, because the vendor has
no idea what surface this fake models -- so the diagnosis rides in a header and
the request itself is recorded as unmatched. Both halves are asserted here
rather than in ``transport.py`` because both are control-plane observations
about the unit, and neither is a property of the binding that carried them.
"""

from __future__ import annotations

import json

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceFailure, ConformanceSkip, Requires, require

__all__ = [
    "an_unmatched_request_is_named_and_recorded",
    "control_plane_describes_the_unit",
    "control_plane_reads_are_inert",
    "the_error_catalogue_does_not_move_with_the_clock",
    "the_error_catalogue_reads_the_same_twice",
]
_MUTATING_METHODS = frozenset({"POST", "PUT"})


#: A path no vendor can serve, deliberately deep enough that it cannot collide
#: with a template of the same shape and deliberately not a plausible resource.
UNMATCHED_PROBE = "/conformance-probe/no/such/route/here"

NEAR_MISS_HEADER = "vendorfake-near-miss"
"""Restated as a literal rather than imported from the kernel.

Every other constant a check needs comes from the core's own vocabulary, but
this one is a *wire* name: a foreign implementation reached over ``--base-url``
has no ``vendorfake.core`` to import it from, and a contract that asserted the
header the core happens to define would pass by construction for the Python
one and be unaskable for anyone else."""

#: The keys ``/__unit/info`` is documented to carry, all seven of them. Listed
#: literally rather than derived from the answer, because "every key it sends
#: is present" is not an assertion.
INFO_KEYS: tuple[str, ...] = (
    "vendor",
    "profile",
    "capabilities",
    "chaos",
    "webhooks",
    "clock",
    "state",
)


@check(
    id="C01",
    name="control plane: the unit describes itself",
    asserts=(
        "GET /__unit/health is 200 with status=ok and framework_answered=0; GET /__unit/info carries "
        "every documented key; GET /__unit/routes publishes a non-empty surface."
    ),
)
def control_plane_describes_the_unit(env: CheckEnv) -> str:
    health = env.client.call("GET", f"{CONTROL_PREFIX}health")
    require(
        health.status == 200,
        f"GET /__unit/health answered {health.status}, expected 200. The control plane is declared "
        f"internal=True in core/control/plane.py, so no capability and no auth adapter may stand in "
        f"front of it -- a unit that cannot answer its own liveness probe cannot be driven at all.",
    )
    body = health.json()
    require(
        body.get("status") == "ok",
        f"GET /__unit/health answered status={body.get('status')!r}, expected 'ok' (core/control/plane.py::health).",
    )
    require(
        body.get("framework_answered") == 0,
        f"GET /__unit/health reports framework_answered={body.get('framework_answered')!r}. Anything "
        f"but 0 means a web framework answered a request instead of handing it to the unit, so some "
        f"consumer received a document no vendor shaped. Route every path through the catch-all in "
        f"asgi/app.py and register handlers for the framework's own exceptions.",
    )

    info = env.info()
    missing = [key for key in INFO_KEYS if key not in info]
    require(
        not missing,
        f"GET /__unit/info omits {missing}. Every one of {list(INFO_KEYS)} must be present: this "
        f"document is how a consumer reproduces a run, and how every other conformance check aims "
        f"itself. Add the key in core/control/plane.py::info.",
    )

    table = env.routes()
    require(
        table,
        "GET /__unit/routes published no routes at all. The route table is built from the vendor's "
        "own Route tuples plus the control plane; an empty one means the vendor definition supplied "
        "no routes, or the unit was constructed without a control plane.",
    )
    surface = [row for row in table if not row.internal]
    require(
        surface,
        "GET /__unit/routes published only internal routes. A unit with no vendor surface is a "
        "control plane with nothing behind it -- check VendorDefinition.routes.",
    )
    return (
        f"health ok, framework_answered=0; info carries all {len(INFO_KEYS)} documented keys; "
        f"{len(table)} routes ({len(surface)} vendor, {len(table) - len(surface)} control)"
    )


@check(
    id="C30",
    name="control plane: reading the control plane changes nothing a vendor answer can see",
    asserts=(
        "After every GET under /__unit/ is read on one of two same-profile units, both units "
        "answer the same refusals byte for byte, and the same example mutation -- required to "
        "SUCCEED on both -- leaves both with the same state digest. A control read that consumes "
        "a deterministic stream some vendor answer draws from desynchronizes the pair and fails."
    ),
    requires=Requires(surface_route=True),
)
def control_plane_reads_are_inert(env: CheckEnv) -> str:
    """The consequence, asserted instead of the mechanism.

    Nothing on the control plane publishes a draw count, deliberately: a
    conformance check that reached for one would be asserting bookkeeping
    rather than behaviour, and would only ever catch the stream somebody
    remembered to count. Instead: read EVERYTHING readable on one of two
    same-profile units -- enumerated from ``GET /__unit/routes``, so a control
    route added later is covered without anyone remembering -- then do the
    same observable things on both and require the answers to agree. Whole
    bodies are compared for the refusals, which is what makes it
    vendor-neutral: the core does not need to know an envelope carries a
    request id, only that looking at the fake did not change what it says
    next.

    The mutation must SUCCEED on both units and is compared by state digest
    rather than bytes, both measured rather than assumed: a refused mutation
    commits nothing on either side, so 400==400 with matching digests would
    certify inertness having exercised no stream at all; and a 2xx mutation
    body embeds ``created_at`` from the live clock on all three built-in
    vendors, so two fresh units answer byte-identical refusals but not
    byte-identical successes. The digest excludes volatile fields and covers
    every minted id, which is exactly the stream a consuming read would
    shift.

    The coverage is exactly what the two probe families render, stated
    plainly (konyklabs/roadmap#42 review): the refusal comparison catches a
    consumed stream only where refusals embed drawn values (Toast's request
    ids -- the shipped defect's class); the digest comparison catches the
    entity/id stream wherever a mutating example exists. Where a profile has
    neither a mutating example nor a usable credential for one (oauth-only),
    the check degrades to the refusal comparison rather than skipping, and
    says so -- on a vendor whose refusals are static, that degraded run
    proves inertness of nothing but the refusal path. A read that consumes
    an UNOBSERVED stream (core rng feeding no answer) is out of reach by
    design: this check asserts consequences, not bookkeeping.

    Chaos is reset on both units first, symmetrically: a standing rule
    answering a probe with a rate-limit fault would compare a live reset
    epoch, and what this check asks is about reads, not faults. (That reset
    is also why chaos-stream consumption is not observable here.)
    """
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    reads = tuple(row for row in env.routes() if row.internal and row.method == "GET")
    require(
        reads,
        "GET /__unit/routes lists no internal GET route at all; the control plane in "
        "core/control/plane.py publishes over a dozen. A surface this check cannot enumerate is a "
        "surface whose reads nothing can vouch for.",
    )
    for row in reads:
        answered = env.client.call("GET", row.probe_path)
        require(
            answered.status == 200,
            f"GET {row.probe_path} answered {answered.status}: {answered.text[:200]}. Every control "
            f"GET must answer on every profile -- they are declared internal=True, so no capability "
            f"and no credential may stand in front of one.",
        )

    refusal_route = env.first_vendor_route()
    probes: tuple[tuple[str, str], ...] = (
        ("GET", "/definitely/not/a/real/path/conformance"),
        (refusal_route.method, refusal_route.probe_path),
    )
    mutations = env.example_routes(methods=_MUTATING_METHODS)
    mutation = mutations[0] if mutations else None
    mutation_headers: dict[str, str] = {}
    degraded_because = f"no enabled mutating route publishes an example on profile {env.profile!r}"
    if mutation is not None:
        try:
            # Resolved up front: a missing credential degrades this half the
            # same way a missing example does, instead of surfacing as an
            # undeclared skip from the middle of the check.
            mutation_headers = env.authorized(mutation)
        except ConformanceSkip as unusable:
            degraded_because = str(unusable)
            mutation = None

    compared = 0
    with env.fresh() as other:
        other.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
        for method, path in probes:
            mine = env.client.call(method, path, json_body={})
            theirs = other.client.call(method, path, json_body={})
            require(
                mine.status == theirs.status and mine.body == theirs.body,
                f"{method} {path}: a unit that had every /__unit/ GET read answers differently from "
                f"an untouched one -- {mine.status} vs {theirs.status}, "
                f"{len(mine.body)} vs {len(theirs.body)} bytes.\n"
                f"  read-first: {mine.body[:200]!r}\n"
                f"  untouched:  {theirs.body[:200]!r}\n"
                f"Some control read consumed a deterministic stream the vendor's answers draw from. "
                f"A control route that renders vendor-shaped output must mark the shape call as a "
                f"description (the CATALOGUE_PROBE_INFO_KEY idiom) rather than calling the live "
                f"refusal path; a consumer who reads the fake's diagnostics must not renumber the "
                f"rest of their scenario.",
            )
            compared += 1
        if mutation is not None:
            mine = env.client.call(
                mutation.method,
                mutation.example_path,
                json_body=dict(mutation.example_body or {}),
                headers=mutation_headers,
            )
            theirs = other.client.call(
                mutation.method,
                mutation.example_path,
                json_body=dict(mutation.example_body or {}),
                headers=mutation_headers,
            )
            require(
                200 <= mine.status < 300 and 200 <= theirs.status < 300,
                f"{mutation.key} refused its own published example_body "
                f"({mine.status} read-first, {theirs.status} untouched): {mine.text[:200]}. A "
                f"refused mutation commits nothing on either unit, so matching digests would "
                f"certify inertness having exercised no stream at all.",
            )
            require(
                mine.status == theirs.status,
                f"{mutation.key} with its own example_body answered {mine.status} on the unit whose "
                f"control plane was read and {theirs.status} on an untouched one; the reads changed "
                f"what the vendor does, not just what it says.",
            )
            digest_mine = str(env.state()["digest"])
            digest_theirs = str(other.state()["digest"])
            require(
                digest_mine == digest_theirs,
                f"after the same {mutation.key} mutation, the unit whose control plane was read "
                f"digests to {digest_mine} and an untouched one to {digest_theirs}. The digest "
                f"covers every minted id, so some /__unit/ GET consumed the id or rng stream and "
                f"shifted what the mutation minted -- the read renumbered the scenario.",
            )
    detail = f"{len(reads)} control GETs read on one unit; {compared} refusal probes byte-identical across the pair"
    if mutation is None:
        return (
            f"{detail}; {degraded_because}, so the entity-stream half degraded to the refusal "
            f"comparison, which only catches streams that refusals render"
        )
    return f"{detail}; state digests agree after the same successful {mutation.key} mutation on both"


@check(
    id="C31",
    name="control plane: the error catalogue reads the same twice",
    asserts=(
        "Two consecutive GETs of /__unit/errors answer identical bytes. The catalogue describes "
        "the error table; a description read twice is the same description."
    ),
)
def the_error_catalogue_reads_the_same_twice(env: CheckEnv) -> str:
    """The original repro, verbatim, as a standing contract.

    Toast's catalogue answered different bytes to two identical GETs because
    each rendering drew twenty fresh request ids (konyklabs/roadmap#42). C30
    catches a read that consumes a stream the VENDOR draws from; this one
    catches the smaller lie where the catalogue only desynchronizes itself --
    a per-render counter, a fresh uuid, anything minted at describe time.
    """
    first = env.client.call("GET", f"{CONTROL_PREFIX}errors")
    require(
        first.status == 200,
        f"GET /__unit/errors answered {first.status}: {first.text[:200]}. The catalogue is a "
        f"control route and must answer on every profile.",
    )
    second = env.client.call("GET", f"{CONTROL_PREFIX}errors")
    require(
        second.status == 200 and second.body == first.body,
        f"two consecutive GETs of /__unit/errors disagree "
        f"({first.status}/{len(first.body)} bytes, then {second.status}/{len(second.body)} bytes). "
        f"The catalogue is rendered by shaping every error kind; if that shaping draws a request "
        f"id, a timestamp or any other per-call value, the render must mark itself as a "
        f"description (the CATALOGUE_PROBE_INFO_KEY idiom) and freeze a synthetic one instead.",
    )
    return f"GET /__unit/errors twice: {len(first.body)} identical bytes"


@check(
    id="C32",
    name="control plane: the error catalogue does not move when the clock does",
    asserts=(
        "GET /__unit/errors, advance the virtual clock an hour, GET again: identical bytes. A "
        "description renders from the error table, not from the clock."
    ),
    requires=Requires(virtual_clock=True),
)
def the_error_catalogue_does_not_move_with_the_clock(env: CheckEnv) -> str:
    """The half of konyklabs/roadmap#42 that actually failed in CI.

    Toast's catalogue computed the 429 row's rate-limit reset as
    ``floor(now/1000) + retry_after`` from the live clock, so two renderings
    a second apart disagreed -- red on a loaded runner, green locally, and
    the "3.13 red, 3.11 green" it produced was timing luck wearing a version
    number. Crossing a second boundary reliably means moving the clock, and
    moving the clock without waiting needs the virtual one, so this runs
    where C21 runs and skips where it skips. An hour is used rather than a
    second to make the point unmissable in the diff of a failure.
    """
    before = env.client.call("GET", f"{CONTROL_PREFIX}errors")
    require(
        before.status == 200,
        f"GET /__unit/errors answered {before.status}: {before.text[:200]}. The catalogue is a "
        f"control route and must answer on every profile.",
    )
    advanced = env.client.call("POST", f"{CONTROL_PREFIX}clock/advance", json_body={"ms": 3_600_000, "drain": False})
    require(
        advanced.status == 200,
        f"POST /__unit/clock/advance answered {advanced.status}: {advanced.text[:200]}. The "
        f"virtual-clock precondition held, so the control plane must accept an advance.",
    )
    after = env.client.call("GET", f"{CONTROL_PREFIX}errors")
    require(
        after.status == 200 and after.body == before.body,
        f"GET /__unit/errors changed across a one-hour clock advance "
        f"({len(before.body)} then {len(after.body)} bytes). Some catalogue row is computed from "
        f"the live clock -- a rate-limit reset epoch is the shipped instance -- so the same "
        f"consumer sees a different 'documentation' depending on when they look. Freeze a "
        f"synthetic value when the shape call is marked as a description "
        f"(CATALOGUE_PROBE_INFO_KEY), and let only real refusals read the real clock.",
    )
    return f"catalogue identical across a 3600000ms advance: {len(before.body)} bytes"


@check(
    id="C33",
    name="control plane: an unmatched request is named and recorded",
    asserts=(
        "A request no route matches answers with a Vendorfake-Near-Miss header carrying a ranked, "
        "deterministic list of the unit's own routes, and is recorded at GET /__unit/requests with "
        "matched=false; control-plane requests are recorded nowhere."
    ),
)
def an_unmatched_request_is_named_and_recorded(env: CheckEnv) -> str:
    """The whole premise of a fake that tracks a vendor's surface.

    A vendor's 404 says "not found" and can say nothing else, because the
    vendor does not know what this unit models. The unit does, and a request
    aimed at a path it does not serve is, in a test, nearly always a mistake
    worth naming -- so it names the closest routes it has and records that
    nothing answered. Without the record, no consumer can ask "did my code
    call this at all?", because a 4xx leaves no journal entry by design.
    """
    answered = env.client.call("GET", UNMATCHED_PROBE)
    require(
        answered.status == 404,
        f"GET {UNMATCHED_PROBE} answered {answered.status}, expected the vendor's 404. A unit that "
        f"answers anything else for a path it does not serve has a catch-all route in its table.",
    )
    raw = answered.header(NEAR_MISS_HEADER)
    require(
        raw is not None,
        f"GET {UNMATCHED_PROBE} answered 404 with no {NEAR_MISS_HEADER!r} header. The vendor-shaped "
        f"body is a 404 and nothing else, so the header is the only thing that can tell a consumer "
        f"WHICH routes this unit does serve -- attach it on the no-route path in the kernel "
        f"(core/kernel/unit.py), not in a binding, or one transport will carry it and another will not.",
    )
    try:
        misses = json.loads(raw or "")
    except ValueError as exc:
        raise ConformanceFailure(
            f"{NEAR_MISS_HEADER} is not JSON ({exc}): {raw!r}. It carries a compact array so that a "
            f"consumer in any language can read it."
        ) from exc
    require(
        isinstance(misses, list),
        f"{NEAR_MISS_HEADER} carries {type(misses).__name__}, expected a JSON array -- an empty one "
        f"where the profile enables no route at all.",
    )
    internal_paths = {row.path for row in env.routes() if row.internal}
    scores: list[float] = []
    for entry in misses:
        require(
            isinstance(entry, dict) and "route" in entry and "score" in entry,
            f"{NEAR_MISS_HEADER} entry {entry!r} has no 'route' and 'score'. Each entry names one "
            f"route of this unit and how close it was.",
        )
        route_key = str(entry["route"])
        require(
            route_key.split(" ", 1)[-1] not in internal_paths,
            f"{NEAR_MISS_HEADER} suggests {route_key!r}, which is a control-plane route. /__unit/* is "
            f"the observer; a consumer who mistyped a vendor path is never looking for it. Filter "
            f"internal routes out of the candidates.",
        )
        scores.append(float(entry["score"]))
    require(
        scores == sorted(scores, reverse=True),
        f"{NEAR_MISS_HEADER} scores {scores} are not in descending order. The list is a ranking and "
        f"a consumer reads the first entry as the best guess.",
    )

    repeated = env.client.call("GET", UNMATCHED_PROBE)
    require(
        repeated.header(NEAR_MISS_HEADER) == raw,
        f"two identical unmatched requests produced different near misses:\n  {raw}\n  "
        f"{repeated.header(NEAR_MISS_HEADER)}\nThe ranking must be deterministic -- ties broken by "
        f"something total, never by iteration order -- or the same failing test prints a different "
        f"message each run.",
    )

    request_id = answered.header("x-unit-request-id")
    document = env.get_json(f"{CONTROL_PREFIX}requests?unmatched=true")
    rows = list(document["requests"])
    mine = [row for row in rows if row.get("id") == request_id]
    require(
        mine,
        f"GET /__unit/requests?unmatched=true does not carry the request just made (id {request_id!r}); "
        f"it reported {len(rows)} unmatched row(s). Every request the unit handles is recorded, "
        f"matched or not -- that is the half of the story the journal cannot tell, since no 4xx "
        f"leaves a journal entry.",
    )
    record = mine[0]
    require(
        record.get("matched") is False,
        f"the recorded request reports matched={record.get('matched')!r}; a request no route answered "
        f"is matched=false, and the flag is what makes 'what did my code call that landed nowhere' "
        f"answerable.",
    )
    require(
        [entry["route"] for entry in record.get("near_misses", [])] == [entry["route"] for entry in misses],
        f"the near misses on the record {record.get('near_misses')} differ from the ones on the "
        f"response {misses}. They are one computation reported twice; two would drift.",
    )

    everything = env.get_json(f"{CONTROL_PREFIX}requests")
    control_rows = [row for row in everything["requests"] if str(row["path"]).startswith(CONTROL_PREFIX)]
    require(
        not control_rows,
        f"the request log contains {len(control_rows)} control-plane request(s), e.g. "
        f"{control_rows[0]['path'] if control_rows else ''}. /__unit/* is the observer: a log that "
        f"recorded reads of itself would grow by a row for every question asked of it and bury the "
        f"consumer's own traffic underneath.",
    )
    return (
        f"unmatched probe answered 404 with {len(misses)} near miss(es) "
        f"{[entry['route'] for entry in misses]}, identical on a second identical request; recorded "
        f"with matched=false; {everything['count']} row(s) in the log, none of them control-plane"
    )
