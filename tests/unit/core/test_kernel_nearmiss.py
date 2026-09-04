"""The near-miss scorer: what it ranks, and that it ranks the same way twice.

The scorer is a heuristic, so what is worth pinning is not "0.83 is the right
number" but the properties a consumer's error message depends on: a template
parameter stands in for a concrete segment, the method contributes but does not
dominate, the order is total and deterministic, and a disabled or internal
route is never suggested (that last one is the kernel's filtering, tested in
``test_kernel_request_log.py`` where a unit exists to filter with).
"""

from __future__ import annotations

from tests.fakes import route
from vendorfake.core.kernel.nearmiss import (
    METHOD_WEIGHT,
    NEAR_MISS_LIMIT,
    PATH_WEIGHT,
    near_miss_header,
    near_misses,
    path_similarity,
    score_route,
)


def _noop(args: object) -> object:  # pragma: no cover - never invoked
    return {}


def _route(method: str, path: str, operation_id: str | None = None) -> object:
    return route(method, path, _noop, operation_id=operation_id)


# ---------------------------------------------------------------------------
# path similarity over templates
# ---------------------------------------------------------------------------


def test_a_template_parameter_stands_in_for_the_segment_that_was_sent() -> None:
    """The case the whole scorer exists for: a real id against the template it
    would have filled. Compared literally, 'abc' and '{order_id}' are two
    unequal segments and the closest route to a request for one order would
    score no better than an unrelated path of the same depth."""
    assert path_similarity("/v2/orders/abc", "/v2/orders/{order_id}") == 1.0


def test_a_template_parameter_earns_nothing_at_a_different_depth() -> None:
    """Substitution is positional, so it cannot rescue a path of another
    length -- and should not: /v2/orders and /v2/orders/{id} really are
    different routes, and scoring them identical would hide the distinction
    between 'list' and 'get one' in exactly the message meant to explain it."""
    assert path_similarity("/v2/orders", "/v2/orders/{order_id}") < 1.0


def test_similarity_is_zero_when_nothing_is_shared() -> None:
    assert path_similarity("/a/b", "/c/d") == 0.0


def test_a_trailing_slash_and_a_doubled_slash_change_nothing() -> None:
    """Empty segments are dropped, as the router drops them: a consumer whose
    base URL ended in a slash must not be told their path is unlike itself."""
    assert path_similarity("/v2/orders/", "/v2/orders") == 1.0
    assert path_similarity("/v2//orders", "/v2/orders") == 1.0


# ---------------------------------------------------------------------------
# the composed score
# ---------------------------------------------------------------------------


def test_the_method_is_worth_its_declared_weight_and_no_more() -> None:
    """An exact path with the wrong verb scores exactly the path weight, and
    the same path with the right verb scores 1.0. Asserting the arithmetic
    rather than an inequality is what makes the weights a decision a reviewer
    can disagree with rather than a pair of magic numbers."""
    exact = _route("POST", "/v2/orders")
    assert score_route("POST", "/v2/orders", exact) == METHOD_WEIGHT + PATH_WEIGHT == 1.0
    assert score_route("GET", "/v2/orders", exact) == PATH_WEIGHT


def test_a_matching_path_beats_a_matching_method() -> None:
    """The path carries more weight, deliberately: a wrong verb on a path that
    exists is already answered by the router's own 405, so a near miss is only
    ever computed where the path itself was wrong."""
    right_path = _route("GET", "/v2/orders")
    right_method_only = _route("POST", "/v1/nothing/alike")
    assert score_route("POST", "/v2/orders", right_path) > score_route("POST", "/v2/orders", right_method_only)


def test_the_method_comparison_ignores_case() -> None:
    assert score_route("post", "/v2/orders", _route("POST", "/v2/orders")) == 1.0


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------


def test_the_closest_routes_come_back_best_first_and_capped() -> None:
    candidates = [
        _route("POST", "/oauth2/revoke", "RevokeToken"),
        _route("POST", "/oauth2/token", "ObtainToken"),
        _route("POST", "/oauth2/token/status", "TokenStatus"),
        _route("GET", "/v2/locations", "ListLocations"),
    ]
    ranked = near_misses("POST", "/oauth2/token/refresh", candidates)
    assert len(ranked) == NEAR_MISS_LIMIT
    assert [miss.operation_id for miss in ranked] == ["ObtainToken", "TokenStatus", "RevokeToken"]
    assert [miss.score for miss in ranked] == sorted((miss.score for miss in ranked), reverse=True)


def test_everything_is_scored_however_badly_it_does() -> None:
    """No threshold. 'nothing was even close' is the answer a consumer who has
    pointed a test at the wrong vendor needs, and a cut-off would have replaced
    it with an empty list that says nothing at all."""
    ranked = near_misses("GET", "/completely/unrelated", [_route("POST", "/v2/orders", "CreateOrder")])
    assert [miss.operation_id for miss in ranked] == ["CreateOrder"]
    assert ranked[0].score == 0.0


def test_a_one_character_typo_is_settled_by_characters_not_by_the_alphabet() -> None:
    """The score compares whole segments, so a typo in the last one ties with
    every sibling of that path: '/order' is exactly as far from '/orders' as
    from '/items'. Alphabetical order would then name 'customers' at the top of
    the message for the commonest mistake there is."""
    candidates = [
        _route("POST", "/v3/merchants/{mId}/orders", "CreateOrder"),
        _route("POST", "/v3/merchants/{mId}/items", "CreateItem"),
        _route("POST", "/v3/merchants/{mId}/customers", "CreateCustomer"),
    ]
    ranked = near_misses("POST", "/v3/merchants/M1/order", candidates)
    # The fixture is only interesting while the three genuinely tie on score.
    assert len({miss.score for miss in ranked}) == 1
    assert ranked[0].operation_id == "CreateOrder"
    assert near_misses("POST", "/v3/merchants/M1/order", list(reversed(candidates))) == ranked


def test_a_total_tie_is_broken_by_route_key_and_is_therefore_stable() -> None:
    """Two candidates the same distance away by both measures must still come
    back in one order, or the same failing test prints a different message each
    time it runs."""
    candidates = [_route("GET", "/b", "Bee"), _route("GET", "/a", "Ay")]
    ranked = near_misses("GET", "/x", candidates)
    assert [miss.route for miss in ranked] == ["GET /a", "GET /b"]
    assert near_misses("GET", "/x", list(reversed(candidates))) == ranked


def test_the_ranking_does_not_depend_on_the_order_it_was_given() -> None:
    candidates = [
        _route("GET", "/v2/orders/{order_id}", "GetOrder"),
        _route("POST", "/v2/orders", "CreateOrder"),
        _route("GET", "/v2/locations", "ListLocations"),
    ]
    first = near_misses("GET", "/v2/orders/abc/x", candidates)
    second = near_misses("GET", "/v2/orders/abc/x", list(reversed(candidates)))
    assert first == second


def test_no_candidates_is_an_empty_ranking_and_a_valid_header() -> None:
    """A profile can enable nothing at all. The header is still sent, because
    its absence is what means 'a route answered'."""
    assert near_misses("GET", "/x", []) == ()
    assert near_miss_header(()) == "[]"


# ---------------------------------------------------------------------------
# the header
# ---------------------------------------------------------------------------


def test_the_header_is_compact_json_rounded_for_a_reader() -> None:
    ranked = near_misses("POST", "/oauth2/token/refresh", [_route("POST", "/oauth2/token", "ObtainToken")])
    assert near_miss_header(ranked) == '[{"route":"POST /oauth2/token","score":0.88,"operation_id":"ObtainToken"}]'


def test_the_header_omits_an_operation_id_the_route_does_not_publish() -> None:
    """Absent rather than null, as everywhere else on this wire."""
    ranked = near_misses("GET", "/x", [_route("GET", "/y")])
    assert near_miss_header(ranked) == '[{"route":"GET /y","score":0.4}]'


def test_the_header_is_ascii_whatever_the_route_is_called() -> None:
    """A header field value with a raw non-ASCII byte in it is unrepresentable
    on some servers and mangled on others, so this one escapes -- unlike the
    body encoder, which must emit UTF-8 because a signature covers its bytes."""
    ranked = near_misses("GET", "/x", [_route("GET", "/café")])
    header = near_miss_header(ranked)
    assert header.isascii()
    assert "caf\\u00e9" in header
