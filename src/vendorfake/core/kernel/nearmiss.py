"""Which routes a request nearly asked for, when none of them answered it.

FOR: turning "404" into "you meant one of these three". A fake whose whole
premise is that it tracks a vendor's surface knows something a vendor's own
404 cannot say: the caller aimed at a path this unit does not serve, which in a
test is almost always a typo, a wrong API version, or a profile with the
capability switched off. Every other test double in this space -- respx,
pytest-httpx, MSW, WireMock -- fails an unmatched request loudly and prints
what it *does* know about; this is that diagnosis, computed once in the core so
that both bindings and the control plane report the same thing.

INVARIANT: **deterministic, and free of the request's own values.** The score
compares path *templates*, so ``/v2/orders/abc`` and ``/v2/orders/{order_id}``
are as close as two spellings of the same route can be, and two runs of the
same suite produce the same three candidates in the same order. Ties break on
a character-level ratio and then on the route key (see :func:`near_misses`), so
a scorer that scored everything equally would still be stable rather than
dependent on registration order.

THE SCORE, and why it is shaped this way::

    score = 0.4 * (1.0 if the method matches exactly else 0.0)
          + 0.6 * SequenceMatcher(request segments, template segments).ratio()

The path carries more weight than the method because a wrong path is the
mistake this exists to name and a wrong method is already answered by the
router's own 405 -- a near miss is only ever computed where nothing matched at
all. The method still counts for something, because ``GET /v2/orders`` and
``POST /v2/orders`` are different operations and a consumer who sent the wrong
verb to a path that does not exist has two things wrong.

HOW ``{x}`` MATCHES A CONCRETE SEGMENT. A template parameter is substituted
with whatever segment the request had in the same position before the ratio is
taken, so ``/v2/orders/{order_id}`` versus ``/v2/orders/abc`` scores 1.0 on the
path rather than being penalised for the one segment it could never have
matched literally. Positional, deliberately: alignment-aware substitution would
need the ratio in order to compute the alignment and the alignment in order to
compute the ratio. The cost is that a template parameter contributes nothing on
a request of a different length -- ``/v2/orders`` against
``/v2/orders/{order_id}`` -- which is the right answer anyway, since those
really are different paths.

WHAT IS A CANDIDATE. Non-internal routes whose capability is enabled. Internal
routes are excluded because ``/__unit/*`` is the observer and suggesting it to
a consumer who mistyped a vendor path would be noise; a route behind a disabled
capability is excluded because it is not part of this unit's surface right now,
and pointing at it would send a reader hunting for a typo that is not there.
The caller does the filtering and passes what is left, so this module needs to
know nothing about a registry.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher

from vendorfake.core.kernel.types import NearMiss, Route

__all__ = [
    "METHOD_WEIGHT",
    "NEAR_MISS_HEADER",
    "NEAR_MISS_LIMIT",
    "PATH_WEIGHT",
    "near_miss_header",
    "near_misses",
    "score_route",
]

NEAR_MISS_HEADER = "vendorfake-near-miss"
"""Where the diagnosis rides on an unmatched response.

Lower-cased, as every header this core sets is: HTTP field names are
case-insensitive and both bindings normalise them, so one spelling in the
source is one fewer thing for a check to try twice. Documentation spells it
``Vendorfake-Near-Miss``, which is the same header.

Named for the product rather than prefixed ``x-unit-`` like the request id and
the error kind: those two are part of the unit's own protocol and predate this,
while this header is a message to a person debugging a test.
"""

NEAR_MISS_LIMIT = 3
"""How many candidates are reported. Three fits on a screen, and a list long
enough to contain the answer by accident is not a diagnosis."""

METHOD_WEIGHT = 0.4
PATH_WEIGHT = 0.6


def _segments(path: str) -> list[str]:
    """``/v2/orders/x`` -> ``['v2', 'orders', 'x']``.

    Mirrors ``router.split_path`` rather than importing it, because the router
    drops empty segments in order to *match* and this compares in order to
    *rank*: if the router's rule ever changed, a near miss computed under the
    other rule would still be a sensible near miss, and a ranking that silently
    followed a routing change would be harder to reason about than one that did
    not.
    """
    return [segment for segment in path.split("/") if segment]


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}") and len(segment) > 2


def path_similarity(path: str, template: str) -> float:
    """0.0 to 1.0 over path segments, with ``{x}`` standing in for whatever
    the request had in that position. See the module docstring."""
    wanted = _segments(path)
    offered = _segments(template)
    filled = [
        wanted[index] if _is_param(segment) and index < len(wanted) else segment
        for index, segment in enumerate(offered)
    ]
    return SequenceMatcher(None, wanted, filled).ratio()


def score_route(method: str, path: str, route: Route) -> float:
    """How close ``route`` is to what was asked for. See the module docstring."""
    method_match = 1.0 if route.method.upper() == method.upper() else 0.0
    return METHOD_WEIGHT * method_match + PATH_WEIGHT * path_similarity(path, route.path)


def near_misses(
    method: str,
    path: str,
    candidates: Iterable[Route],
    *,
    limit: int = NEAR_MISS_LIMIT,
) -> tuple[NearMiss, ...]:
    """The closest ``limit`` candidates, best first.

    Every candidate is scored -- there is no cut-off. A unit with three routes
    reports all three however badly they score, because "nothing was even
    close" is itself the answer to a consumer who has pointed a test at the
    wrong vendor, and a threshold would have replaced it with silence.

    TIES BREAK ON CHARACTERS FIRST, then on the route key. The score compares
    whole segments, so every one-character typo in the last segment of a path
    ties with every sibling of that path: ``/oauth2/tokens`` is exactly as far
    from ``/oauth2/token`` as it is from ``/oauth2/revoke``, and a tie broken
    alphabetically would have named the wrong one at the top of the message
    for the single commonest mistake this feature exists to diagnose. A
    character-level ratio over the two paths settles it, and settles it the
    same way every run.

    It is a *sort key and not part of the score*, deliberately: the published
    number stays the one the scoring rule defines, so a consumer comparing two
    reported scores is comparing what they were told they were comparing, and
    the tiebreak cannot quietly reorder anything that was not already equal.
    """
    scored = [
        (
            NearMiss(route=route.key, operation_id=route.operation_id, score=score_route(method, path, route)),
            SequenceMatcher(None, path, route.path).ratio(),
        )
        for route in candidates
    ]
    scored.sort(key=lambda row: (-row[0].score, -row[1], row[0].route))
    return tuple(miss for miss, _ in scored[:limit])


def near_miss_header(misses: Sequence[NearMiss]) -> str:
    """The header value: a compact JSON array, ``[]`` when there is nothing to say.

    Always a valid document, even when empty, so a consumer parses one thing
    rather than branching on whether the header is worth parsing. The header is
    set on every unmatched response for the same reason -- its *absence* then
    means "a route answered", which is a fact worth being able to read.

    Encoded here rather than through ``util/json.dump_json``, which is the wire
    encoder for *bodies* and deliberately emits UTF-8 (a webhook signature
    covers those bytes). A header field value is not a body: a non-ASCII byte
    in one is unrepresentable on some servers and mangled on others, so this
    one escapes instead. Same compact separators, so the two forms differ in
    nothing else.
    """
    return _json.dumps([miss.as_json() for miss in misses], separators=(",", ":"), ensure_ascii=True)
