"""C01, C30, C31, C32 -- the unit describes itself, and describing is free.

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
"""

from __future__ import annotations

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceSkip, Requires, require

__all__ = [
    "control_plane_describes_the_unit",
    "control_plane_reads_are_inert",
    "the_error_catalogue_does_not_move_with_the_clock",
    "the_error_catalogue_reads_the_same_twice",
]

_MUTATING_METHODS = frozenset({"POST", "PUT"})

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
