"""Which routes a request nearly asked for, when none of them answered it.

Turns a 404 into "you meant one of these three", scoring ``0.4`` for an exact method match plus ``0.6 *
SequenceMatcher(request segments, template segments).ratio()``, with each template parameter filled positionally
from the request before the ratio is taken. INVARIANT: **deterministic** -- ties break on a character-level ratio
and then on the route key, so ranking never depends on registration order. The caller filters candidates down to
non-internal routes with their capability enabled, so this module knows nothing of a registry.
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

#: Where the diagnosis rides on an unmatched response; documentation spells the
#: same header ``Vendorfake-Near-Miss``.
NEAR_MISS_HEADER = "vendorfake-near-miss"

#: How many candidates are reported.
NEAR_MISS_LIMIT = 3

METHOD_WEIGHT = 0.4
PATH_WEIGHT = 0.6


def _segments(path: str) -> list[str]:
    """``/v2/orders/x`` -> ``['v2', 'orders', 'x']``. Mirrors
    ``router.split_path``; matching and ranking may diverge."""
    return [segment for segment in path.split("/") if segment]


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}") and len(segment) > 2


def path_similarity(path: str, template: str) -> float:
    """0.0 to 1.0 over path segments, ``{x}`` standing in for the request's."""
    wanted = _segments(path)
    offered = _segments(template)
    filled = [
        wanted[index] if _is_param(segment) and index < len(wanted) else segment
        for index, segment in enumerate(offered)
    ]
    return SequenceMatcher(None, wanted, filled).ratio()


def score_route(method: str, path: str, route: Route) -> float:
    """How close ``route`` is to what was asked for."""
    method_match = 1.0 if route.method.upper() == method.upper() else 0.0
    return METHOD_WEIGHT * method_match + PATH_WEIGHT * path_similarity(path, route.path)


def near_misses(
    method: str,
    path: str,
    candidates: Iterable[Route],
    *,
    limit: int = NEAR_MISS_LIMIT,
) -> tuple[NearMiss, ...]:
    """The closest ``limit`` candidates, best first. Every candidate is scored,
    with no cut-off, so "nothing was even close" stays reportable."""
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
    """A compact JSON array, ``[]`` when there is nothing to say, so the header is valid on every unmatched response
    and its absence means a route answered. ASCII-escaped: a header value cannot carry UTF-8."""
    return _json.dumps([miss.as_json() for miss in misses], separators=(",", ":"), ensure_ascii=True)
