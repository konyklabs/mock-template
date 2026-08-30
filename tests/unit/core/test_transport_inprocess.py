"""The in-process binding converts, and does not interpret."""

from __future__ import annotations

import pytest

from tests.fakes import make_unit, route
from vendorfake.core.kernel.reply import json_, no_content, text
from vendorfake.core.kernel.types import ReplyInit
from vendorfake.core.transport.inprocess import in_process


def _echo(args):  # type: ignore[no-untyped-def]
    return json_(
        {
            "method": args.req.method,
            "transport": args.req.transport,
            "content_type": args.header("content-type"),
            "raw": args.body_text(),
            "query": dict(args.req.query),
            "query_all": {name: list(values) for name, values in args.req.query_all.items()},
            "k_all": list(args.query_all("k")),
        }
    )


def _unit():  # type: ignore[no-untyped-def]
    return make_unit(
        [
            route("POST", "/echo", _echo),
            route("GET", "/echo", _echo),
            route("PUT", "/echo", _echo),
            route("PATCH", "/echo", _echo),
            route("DELETE", "/echo", _echo),
            route("GET", "/html", lambda args: text("<p/>", headers={"content-type": "text/html"})),
            route("GET", "/empty", lambda args: no_content()),
            route("GET", "/bytes", lambda args: ReplyInit(raw=b"\xff\xfe")),
        ]
    )


def test_the_transport_is_named_so_a_vendor_can_see_which_binding_called() -> None:
    assert in_process(_unit()).get("/echo").json()["transport"] == "inprocess"


def test_a_dict_body_is_serialised_and_typed_as_json() -> None:
    body = in_process(_unit()).post("/echo", {"a": 1}).json()
    assert body["content_type"] == "application/json"
    assert body["raw"] == '{"a":1}'


def test_raw_body_wins_over_body_so_a_caller_can_pin_the_exact_bytes() -> None:
    body = (
        in_process(_unit())
        .call(
            method="POST",
            path="/echo",
            headers={"content-type": "application/x-www-form-urlencoded"},
            raw_body=b"a=1&a=2",
        )
        .json()
    )
    assert body["raw"] == "a=1&a=2"
    assert body["content_type"] == "application/x-www-form-urlencoded"


def test_query_parameters_reach_the_request() -> None:
    assert in_process(_unit()).get("/echo", query={"limit": "2"}).json()["query"] == {"limit": "2"}


def test_a_repeated_query_parameter_reaches_the_handler_whole() -> None:
    """``query`` keeps the last value, as every binding always did; ``query_all``
    keeps them all, in arrival order, and a handler asks for it by name."""
    body = in_process(_unit()).get("/echo?k=a&k=b&limit=2").json()
    assert body["query"] == {"k": "b", "limit": "2"}
    assert body["query_all"] == {"k": ["a", "b"], "limit": ["2"]}
    assert body["k_all"] == ["a", "b"]


def test_a_bare_query_key_is_kept_as_an_empty_value() -> None:
    body = in_process(_unit()).get("/echo?k").json()
    assert body["query"] == {"k": ""}
    assert body["query_all"] == {"k": [""]}


def test_every_verb_helper_sends_its_own_verb() -> None:
    api = in_process(_unit())
    assert api.get("/echo").json()["method"] == "GET"
    assert api.post("/echo").json()["method"] == "POST"
    assert api.put("/echo").json()["method"] == "PUT"
    assert api.patch("/echo").json()["method"] == "PATCH"
    assert api.delete("/echo").json()["method"] == "DELETE"


def test_the_untouched_response_is_reachable_and_body_is_its_exact_bytes() -> None:
    res = in_process(_unit()).get("/bytes")
    assert res.body == b"\xff\xfe"
    assert res.raw.body is res.body
    assert res.text == "��"


def test_an_empty_body_reads_as_none_rather_than_raising() -> None:
    res = in_process(_unit()).get("/empty")
    assert res.status == 204
    assert res.json() is None


def test_a_non_json_body_names_itself_in_the_error() -> None:
    """The reference's client swallows the parse failure and returns the raw
    text in the same field, so an assertion on ``body["id"]`` fails several
    frames from the cause."""
    with pytest.raises(ValueError) as caught:
        in_process(_unit()).get("/html").json()
    assert "<p/>" in str(caught.value)


def test_header_lookup_is_case_insensitive() -> None:
    res = in_process(_unit()).get("/html")
    assert res.header("Content-Type") == "text/html"
    assert res.header("missing") is None


def test_a_caller_supplied_request_id_survives_the_round_trip() -> None:
    res = in_process(_unit()).get("/echo", request_id="corr-9")
    assert res.header("x-unit-request-id") == "corr-9"
