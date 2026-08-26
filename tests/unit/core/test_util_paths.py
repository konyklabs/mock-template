"""Semantics of the one dotted-path resolver.

Weighted at the three places a JavaScript port silently changes meaning:
absence versus explicit null, list indices that JavaScript calls undefined and
Python calls "count from the end", and the containers that are allowed to be
indexed at all.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import FormData
from vendorfake.core.util.json import MISSING
from vendorfake.core.util.paths import dot_get

BODY = {
    "idempotency_key": "k-1",
    "order": {"reference_id": "ref-9", "cleared": None},
    "line_items": [{"note": "first"}, {"note": "second"}],
    "empty": [],
}


def test_a_plain_dotted_path_resolves() -> None:
    assert dot_get(BODY, "order.reference_id") == "ref-9"
    assert dot_get(BODY, "idempotency_key") == "k-1"


def test_an_explicit_null_is_not_absence() -> None:
    """The whole reason MISSING exists. `dict.get` collapses these two, and the
    consumers that read through this resolver both care which one happened."""
    assert dot_get(BODY, "order.cleared") is None
    assert dot_get(BODY, "order.never_set") is MISSING


def test_bracket_subscripts_index_lists_and_mappings() -> None:
    assert dot_get(BODY, "line_items[0].note") == "first"
    assert dot_get(BODY, "line_items[1].note") == "second"
    assert dot_get(BODY, "order[reference_id]") == "ref-9"


def test_a_negative_list_index_is_missing_and_not_the_last_element() -> None:
    """JavaScript's `arr[-1]` is undefined. Porting the read without a guard
    would invent a value at the end of every list, which is worse than absent
    because it looks like a real answer."""
    assert dot_get(BODY, "line_items[-1]") is MISSING
    assert dot_get(BODY, "line_items[2]") is MISSING
    assert dot_get(BODY, "empty[0]") is MISSING


def test_scalars_are_not_indexable() -> None:
    """`typeof cur !== 'object'` in the reference. A string is not a container,
    so `dot_get('abc', '0')` is absence and not 'a'."""
    assert dot_get("abc", "0") is MISSING
    assert dot_get(7, "x") is MISSING
    assert dot_get(None, "x") is MISSING
    assert dot_get(BODY, "idempotency_key.length") is MISSING


def test_javascript_host_properties_are_deliberately_unreachable() -> None:
    """Recorded divergence: JS returns 2 for `line_items.length` because that is
    a property of the array object, not of the document anybody wrote."""
    assert dot_get(BODY, "line_items.length") is MISSING


def test_a_malformed_segment_is_absence_and_not_an_exception() -> None:
    """These paths come from vendor declarations and profile documents. The
    failure a consumer needs to see is 'the rule never fired', which rule
    validation reports; a regex traceback would be a 500 on live traffic."""
    assert dot_get(BODY, "order..reference_id") is MISSING
    assert dot_get(BODY, "order]x") is MISSING
    assert dot_get(BODY, "") is MISSING


def test_any_mapping_is_a_container_including_a_form_body() -> None:
    """Load-bearing: `HandlerArgs.body()` answers with a FormData for a
    form-encoded request, and the reference's JSON-only reader could never see
    one. Matching on Mapping rather than dict is what makes a magic value in a
    form field reachable."""
    form = FormData([("idempotency_key", "k-form"), ("scopes", "a"), ("scopes", "b")])
    assert dot_get(form, "idempotency_key") == "k-form"
    assert dot_get(form, "scopes") == "b"
    assert dot_get(form, "absent") is MISSING
