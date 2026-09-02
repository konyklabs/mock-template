"""The case format: what the schema accepts, and the three operations on a case."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.unit.fidelity.test_runner import make_anchor
from vendorfake.fidelity.corpus import (
    MISSING,
    CorpusError,
    InterpolationError,
    absent_violations,
    interpolate,
    load_corpus,
    load_schema,
    match,
    match_headers,
    parse_case,
    resolve_pointer,
    validate_case,
)

EXAMPLE: dict[str, Any] = {
    "schema": 1,
    "id": "orders.create.minimal",
    "title": "CreateOrder with one ad-hoc line item answers an OPEN order with computed totals",
    "source": {
        "url": "https://example.test/reference/create-order",
        "fetched": "2026-09-02",
        "provenance": "documented",
        "note": "why this is documented",
    },
    "routes": ["POST /v2/orders"],
    "profile": "full",
    "steps": [
        {
            "name": "create",
            "request": {
                "method": "POST",
                "path": "/v2/orders",
                "headers": {"$auth": "bearer"},
                "query": {},
                "body": {"idempotency_key": "${uuid}", "order": {"location_id": "${vars.location_id}"}},
            },
            "expect": {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body": {"order": {"state": "OPEN"}},
                "absent": ["/order/closed_at"],
            },
            "capture": {"order_id": "/order/id"},
        }
    ],
}


def _with(**changes: Any) -> dict[str, Any]:
    doc = json.loads(json.dumps(EXAMPLE))
    for dotted, value in changes.items():
        parts = dotted.split(".")
        node = doc
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        last = parts[-1]
        if value is ...:
            del node[last]
        elif isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return doc


# ---------------------------------------------------------------------------
# Schema.
# ---------------------------------------------------------------------------


def test_the_shipped_schema_is_a_valid_draft_2020_12_schema_and_accepts_the_brief_example() -> None:
    load_schema()
    parsed = parse_case(EXAMPLE)
    assert parsed.id == "orders.create.minimal" and parsed.provenance == "documented"
    assert parsed.profile == "full" and parsed.routes == ("POST /v2/orders",)
    (only,) = parsed.steps
    assert only.request.headers == {"$auth": "bearer"} and only.request.has_body
    assert only.expect.status == 200 and only.expect.absent == ("/order/closed_at",)
    assert only.capture == {"order_id": "/order/id"}


def test_a_case_without_a_body_or_profile_is_fine() -> None:
    doc = _with(**{"steps.0.request.body": ..., "profile": ..., "source.note": ...})
    parsed = parse_case(doc)
    assert parsed.profile is None and not parsed.steps[0].request.has_body


@pytest.mark.parametrize(
    ("changes", "pointer"),
    [
        ({"source.url": ...}, "/source"),
        ({"id": "Orders.Create"}, "/id"),
        ({"id": ".leading-dot"}, "/id"),
        ({"source.provenance": "guess"}, "/source/provenance"),
        ({"source.fetched": "yesterday"}, "/source/fetched"),
        ({"extra": 1}, "/"),
        ({"steps.0.expect.status": ...}, "/steps/0/expect"),
        ({"steps.0.expect.absent": ["order/id"]}, "/steps/0/expect/absent/0"),
        ({"routes": ["post /v2/orders"]}, "/routes/0"),
        ({"steps": []}, "/steps"),
        ({"steps.0.request.method": "FETCH"}, "/steps/0/request/method"),
        ({"steps.0.request.headers": {"a": 1}}, "/steps/0/request/headers/a"),
        ({"steps.0.capture": {"x": "no-slash"}}, "/steps/0/capture/x"),
        ({"schema": 2}, "/schema"),
    ],
)
def test_the_schema_rejects_and_names_the_pointer(changes: dict[str, Any], pointer: str) -> None:
    with pytest.raises(CorpusError) as raised:
        validate_case(_with(**changes), where="case.json")
    message = str(raised.value)
    assert message.startswith("case.json: not a valid corpus case")
    assert f"  {pointer}: " in message, message


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------


def test_load_corpus_reads_every_file_sorted_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = _with(id="b.case")
    a = _with(id="a.case")
    anchor = make_anchor(tmp_path, monkeypatch, [b, a])  # written as 00-b.case.json, 01-a.case.json
    assert [case.id for case in load_corpus(anchor)] == ["b.case", "a.case"]


def test_load_corpus_refuses_a_duplicate_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anchor = make_anchor(tmp_path, monkeypatch, [EXAMPLE, EXAMPLE])
    with pytest.raises(CorpusError) as raised:
        load_corpus(anchor)
    assert "duplicate case id 'orders.create.minimal'" in str(raised.value)


def test_load_corpus_refuses_a_malformed_file_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anchor = make_anchor(tmp_path, monkeypatch, [_with(id="fine"), _with(**{"id": "broken", "source.url": ...})])
    with pytest.raises(CorpusError) as raised:
        load_corpus(anchor)
    assert f"{anchor}/corpus/01-broken.json: not a valid corpus case" in str(raised.value)


def test_load_corpus_without_a_corpus_directory_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anchor = make_anchor(tmp_path, monkeypatch, [])
    (tmp_path / anchor / "corpus").rmdir()
    assert load_corpus(anchor) == ()
    with pytest.raises(FileNotFoundError):
        load_corpus("no_such_package_anywhere")


# ---------------------------------------------------------------------------
# Interpolation.
# ---------------------------------------------------------------------------


def _interp(value: Any, **captures: Any) -> Any:
    ids = iter(["u-1", "u-2", "u-3"])
    return interpolate(value, variables={"location_id": "LOC_1"}, captures=captures, uuid=lambda: next(ids))


def test_interpolation_resolves_vars_captures_and_uuids_in_nested_values() -> None:
    value = {
        "a": "${vars.location_id}",
        "b": "id=${cap.order_id}/loc=${vars.location_id}",
        "c": ["${uuid}", "${uuid}", 7, None, True],
        "d": {"n": "${cap.count}"},
    }
    assert _interp(value, order_id="ord_1", count=3) == {
        "a": "LOC_1",
        "b": "id=ord_1/loc=LOC_1",
        "c": ["u-1", "u-2", 7, None, True],
        "d": {"n": 3},
    }


def test_a_whole_string_reference_keeps_the_captured_type_and_an_embedded_one_renders_it() -> None:
    assert _interp("${cap.count}", count=3) == 3
    assert _interp("n=${cap.count}", count=3) == "n=3"


def test_matcher_tokens_pass_through_interpolation_untouched() -> None:
    assert _interp({"x": "${any}", "y": "${re:ord_[0-9]+}"}) == {"x": "${any}", "y": "${re:ord_[0-9]+}"}


@pytest.mark.parametrize(
    ("text", "fragment"),
    [("${vars.nope}", "no variable 'nope'"), ("${cap.nope}", "no earlier step captured 'nope'")],
)
def test_an_unresolvable_reference_names_itself(text: str, fragment: str) -> None:
    with pytest.raises(InterpolationError) as raised:
        _interp(text)
    assert fragment in str(raised.value)


# ---------------------------------------------------------------------------
# Pointers.
# ---------------------------------------------------------------------------

DOC = {"order": {"id": "ord_1", "items": [{"n": 1}, {"n": 2}], "a/b": 1, "m~n": 2, "none": None}}


@pytest.mark.parametrize(
    ("pointer", "expected"),
    [
        ("", DOC),
        ("/order/id", "ord_1"),
        ("/order/items/1/n", 2),
        ("/order/a~1b", 1),
        ("/order/m~0n", 2),
        ("/order/none", None),
        ("/order/missing", MISSING),
        ("/order/items/2", MISSING),
        ("/order/items/x", MISSING),
        ("/order/id/deeper", MISSING),
    ],
)
def test_resolve_pointer(pointer: str, expected: Any) -> None:
    assert resolve_pointer(DOC, pointer) is expected or resolve_pointer(DOC, pointer) == expected


def test_a_pointer_without_a_leading_slash_is_refused() -> None:
    with pytest.raises(ValueError, match="starts with '/'"):
        resolve_pointer(DOC, "order/id")


# ---------------------------------------------------------------------------
# The subset match: a truth table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "actual", "pointer"),
    [
        # scalars
        (1, 1, None),
        (1, 2, ""),
        ("a", "a", None),
        (1, True, ""),
        (True, 1, ""),
        (None, None, None),
        (None, 0, ""),
        (1.0, 1, None),
        # ${any} and ${re:}
        ("${any}", 0, None),
        ("${any}", None, None),
        ("${any}", {"deep": True}, None),
        ("${re:ord_[0-9]+}", "ord_12", None),
        ("${re:ord_[0-9]+}", "ord_12x", ""),
        ("${re:[0-9]+}", 42, None),
        ("${re:.*}", None, ""),
        ("${re:.*}", {"x": 1}, ""),
        # objects: subset
        ({"a": 1}, {"a": 1, "b": 2}, None),
        ({"a": 1, "b": 2}, {"a": 1}, "/b"),
        ({"a": {"b": 1}}, {"a": {"b": 2}}, "/a/b"),
        ({"a": 1}, [1], ""),
        ({"a/b": 1}, {"a/b": 2}, "/a~1b"),
        # lists: equal length, element-wise
        ([1, 2], [1, 2], None),
        ([1, 2], [1, 2, 3], ""),
        ([{"n": 1}], [{"n": 1, "extra": True}], None),
        ([{"n": 1}, {"n": 2}], [{"n": 1}, {"n": 3}], "/1/n"),
        ([1], {"0": 1}, ""),
    ],
)
def test_match_truth_table(expected: Any, actual: Any, pointer: str | None) -> None:
    found = match(expected, actual)
    if pointer is None:
        assert found is None, found
    else:
        assert found is not None and found.pointer == pointer, found


def test_a_missing_key_is_reported_as_missing_not_none() -> None:
    found = match({"a": None}, {})
    assert found is not None and found.actual is MISSING and found.pointer == "/a"
    assert "expected None, got <missing>" in str(found)


def test_headers_match_case_insensitively_as_a_subset() -> None:
    actual = {"content-type": "application/json", "x-request-id": "r1"}
    assert match_headers({"Content-Type": "application/json"}, actual) is None
    assert match_headers({"x-request-id": "${re:r[0-9]}"}, actual) is None
    found = match_headers({"Content-Type": "text/plain"}, actual)
    assert found is not None and found.pointer == "/headers/content-type"
    found = match_headers({"x-missing": "${any}"}, actual)
    assert found is not None and found.actual is MISSING


def test_absent_violations_name_the_first_pointer_that_resolves() -> None:
    assert absent_violations(DOC, ["/order/closed_at", "/nope"]) is None
    found = absent_violations(DOC, ["/order/closed_at", "/order/none", "/order/id"])
    assert found is not None and found.pointer == "/order/none" and found.actual is None
