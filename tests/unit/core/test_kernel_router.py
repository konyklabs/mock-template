"""What the router promises, including the two things the reference does not."""

from __future__ import annotations

import pytest

from tests.fakes import route
from vendorfake.core.kernel.router import (
    INTERNAL_PATH_PREFIX,
    Match,
    MethodNotAllowed,
    NoRoute,
    Router,
    assert_no_reserved_paths,
    split_path,
)
from vendorfake.core.kernel.types import UnitError, UnitErrorKind


def _ok(args: object) -> object:
    return None


def test_a_brace_segment_becomes_a_parameter() -> None:
    router = Router([route("GET", "/v2/orders/{order_id}", _ok)])
    outcome = router.match("GET", "/v2/orders/abc")
    assert isinstance(outcome, Match)
    assert outcome.params == {"order_id": "abc"}


def test_two_parameters_in_one_path_keep_their_names() -> None:
    router = Router([route("GET", "/v2/{a}/x/{b}", _ok)])
    outcome = router.match("GET", "/v2/one/x/two")
    assert isinstance(outcome, Match)
    assert outcome.params == {"a": "one", "b": "two"}


def test_a_colon_template_is_not_a_parameter() -> None:
    """Braces are canonical in all four places a template is written. A colon
    path is a literal segment, which is what makes a stale ``:order_id`` in a
    profile or a chaos rule fail loudly rather than match everything."""
    router = Router([route("GET", "/v2/orders/:order_id", _ok)])
    assert isinstance(router.match("GET", "/v2/orders/abc"), NoRoute)
    assert isinstance(router.match("GET", "/v2/orders/:order_id"), Match)


def test_a_percent_escape_is_decoded() -> None:
    router = Router([route("GET", "/v2/orders/{order_id}", _ok)])
    outcome = router.match("GET", "/v2/orders/a%20b%2Fc")
    assert isinstance(outcome, Match)
    assert outcome.params == {"order_id": "a b/c"}


def test_a_malformed_percent_escape_is_a_400_and_not_a_500() -> None:
    """``decodeURIComponent`` throws and the reference answers 500;
    ``unquote`` is silent and would answer 200 with a garbage parameter.
    Neither is right: this is the caller's mistake."""
    router = Router([route("GET", "/v2/orders/{order_id}", _ok)])
    with pytest.raises(UnitError) as caught:
        router.match("GET", "/v2/orders/%zz")
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "path"


@pytest.mark.parametrize("bad", ["%", "%4", "abc%", "a%2"])
def test_every_incomplete_escape_is_rejected(bad: str) -> None:
    router = Router([route("GET", "/v2/orders/{order_id}", _ok)])
    with pytest.raises(UnitError):
        router.match("GET", f"/v2/orders/{bad}")


def test_an_escape_that_decodes_to_invalid_utf8_is_rejected() -> None:
    router = Router([route("GET", "/v2/orders/{order_id}", _ok)])
    with pytest.raises(UnitError) as caught:
        router.match("GET", "/v2/orders/%C3%28")
    assert caught.value.field == "path"


def test_a_malformed_escape_in_a_literal_segment_is_not_decoded() -> None:
    """Only parameter segments are decoded, exactly as the reference does it:
    a literal segment is compared verbatim, so ``%zz`` there is a miss."""
    router = Router([route("GET", "/v2/%zz", _ok)])
    assert isinstance(router.match("GET", "/v2/%zz"), Match)


def test_method_not_allowed_lists_every_verb_sorted_and_deduplicated() -> None:
    router = Router(
        [
            route("POST", "/v2/orders", _ok),
            route("PUT", "/v2/orders", _ok),
            route("POST", "/v2/orders", _ok, capability="other"),
        ]
    )
    outcome = router.match("DELETE", "/v2/orders")
    assert isinstance(outcome, MethodNotAllowed)
    assert outcome.allowed == ("POST", "PUT")


def test_no_route_and_method_not_allowed_are_different_answers() -> None:
    router = Router([route("GET", "/v2/orders", _ok)])
    assert isinstance(router.match("POST", "/v2/orders"), MethodNotAllowed)
    assert isinstance(router.match("GET", "/v2/nope"), NoRoute)


def test_registration_order_decides_between_two_matching_routes() -> None:
    first = route("GET", "/v2/orders/{id}", _ok)
    second = route("GET", "/v2/orders/search", _ok)
    assert Router([first, second]).match("GET", "/v2/orders/search").route is first
    assert Router([second, first]).match("GET", "/v2/orders/search").route is second


def test_the_method_comparison_is_case_insensitive_in_both_directions() -> None:
    router = Router([route("get", "/v2/orders", _ok)])
    assert isinstance(router.match("GET", "/v2/orders"), Match)


def test_empty_segments_are_dropped_so_a_trailing_slash_still_matches() -> None:
    assert split_path("/v2//orders/") == ["v2", "orders"]
    router = Router([route("GET", "/v2/orders", _ok)])
    assert isinstance(router.match("GET", "/v2/orders/"), Match)


def test_a_vendor_route_may_not_claim_the_control_plane_namespace() -> None:
    with pytest.raises(UnitError) as caught:
        Router([route("GET", f"{INTERNAL_PATH_PREFIX}info", _ok)])
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "routes"


def test_an_internal_route_may_claim_it() -> None:
    router = Router([route("GET", f"{INTERNAL_PATH_PREFIX}info", _ok, internal=True)])
    assert isinstance(router.match("GET", "/__unit/info"), Match)


def test_the_bare_prefix_without_a_trailing_slash_is_reserved_too() -> None:
    with pytest.raises(UnitError):
        Router([route("GET", "/__unit", _ok)])


def test_a_path_that_merely_starts_with_the_letters_is_not_reserved() -> None:
    Router([route("GET", "/__units-of-measure", _ok)])


def test_assert_no_reserved_paths_names_every_offender_at_once() -> None:
    routes = [
        route("GET", "/__unit/a", _ok),
        route("GET", "/v2/ok", _ok),
        route("POST", "/__unit/b", _ok),
    ]
    with pytest.raises(UnitError) as caught:
        assert_no_reserved_paths(routes)
    assert caught.value.info is not None
    assert caught.value.info["routes"] == ["GET /__unit/a", "POST /__unit/b"]


def test_routes_are_returned_in_registration_order() -> None:
    a = route("GET", "/a", _ok)
    b = route("GET", "/b", _ok)
    assert Router([a, b]).routes() == (a, b)


def test_param_names_are_reported_in_path_order() -> None:
    r = route("GET", "/v2/{a}/x/{b}", _ok)
    assert Router([r]).param_names(r) == ("a", "b")
