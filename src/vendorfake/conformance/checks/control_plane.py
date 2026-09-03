"""C01 and C24 -- the unit describes itself, and says so when nothing answered.

Every other contract reads the control plane to aim itself, so C01 runs first
and states the minimum: the unit is alive, it says what it is, and it publishes
its own surface. A unit that fails C01 cannot be examined at all, and every
later failure would be a consequence rather than a finding.

C24 is the other direction: what the unit says about a request it could not
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
from vendorfake.conformance.types import ConformanceFailure, require

__all__ = ["an_unmatched_request_is_named_and_recorded", "control_plane_describes_the_unit"]

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
    id="C24",
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
