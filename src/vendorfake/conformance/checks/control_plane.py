"""C01 -- the unit describes itself.

Every other contract reads the control plane to aim itself, so this one runs
first and states the minimum: the unit is alive, it says what it is, and it
publishes its own surface. A unit that fails C01 cannot be examined at all,
and every later failure would be a consequence rather than a finding.
"""

from __future__ import annotations

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import require

__all__ = ["control_plane_describes_the_unit"]

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
