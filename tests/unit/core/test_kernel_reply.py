"""Exact bytes and exact headers, for every branch of ``normalize``."""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.reply import (
    JSON_CONTENT_TYPE,
    TEXT_CONTENT_TYPE,
    decode_body,
    json_,
    no_content,
    normalize,
    parse_body,
    redirect,
    text,
)
from vendorfake.core.kernel.types import ReplyInit, UnitResponse


def test_json_none_is_two_bytes_and_not_null() -> None:
    """``JSON.stringify(r.json ?? {})``: both of JavaScript's empty values land
    on ``{}``, and Python has only one of them."""
    assert normalize(ReplyInit()).body == b"{}"
    assert normalize(json_(None)).body == b"{}"


def test_json_false_and_zero_are_not_treated_as_absent() -> None:
    """``??`` is null-coalescing, not truthiness: ``false ?? {}`` is ``false``."""
    assert normalize(json_(False)).body == b"false"
    assert normalize(json_(0)).body == b"0"
    assert normalize(json_([])).body == b"[]"


def test_a_redirect_has_a_zero_byte_body_and_no_content_type() -> None:
    res = normalize(redirect("/oauth2/authorize?x=1"))
    assert res.status == 302
    assert res.body == b""
    assert "content-type" not in res.headers
    assert res.headers["location"] == "/oauth2/authorize?x=1"


def test_no_content_has_a_zero_byte_body_and_no_content_type() -> None:
    res = normalize(no_content())
    assert res.status == 204
    assert res.body == b""
    assert "content-type" not in res.headers


def test_the_text_branch_is_chosen_on_presence_and_not_on_truthiness() -> None:
    """A truthiness test would send both of the two cases above down the JSON
    branch and answer a 302 with ``{}`` and ``content-type: application/json``."""
    empty = normalize(ReplyInit(status=302, text="", headers={"location": "/x"}))
    assert empty.body == b""
    assert "content-type" not in empty.headers


def test_a_non_empty_text_body_gets_a_charset_bearing_content_type() -> None:
    res = normalize(text("hello"))
    assert res.body == b"hello"
    assert res.headers["content-type"] == TEXT_CONTENT_TYPE


def test_an_explicit_content_type_is_never_overwritten() -> None:
    assert normalize(text("<p/>", headers={"content-type": "text/html"})).headers["content-type"] == "text/html"
    assert (
        normalize(json_({"a": 1}, headers={"content-type": "application/vnd.api+json"})).headers["content-type"]
        == "application/vnd.api+json"
    )


def test_header_names_are_lower_cased_so_a_content_type_cannot_be_set_twice() -> None:
    """The reference looks the key up verbatim, so a handler writing
    ``Content-Type`` receives a second, conflicting ``content-type``."""
    res = normalize(ReplyInit(json={"a": 1}, headers={"Content-Type": "text/html", "X-Thing": "1"}))
    assert res.headers == {"content-type": "text/html", "x-thing": "1"}


def test_raw_wins_over_text_and_json() -> None:
    res = normalize(ReplyInit(raw=b"\x00\x01", text="ignored", json={"also": "ignored"}))
    assert res.body == b"\x00\x01"
    assert "content-type" not in res.headers


def test_an_empty_raw_body_is_still_the_raw_branch() -> None:
    """``raw=b""`` is presence, not absence: a deliberate zero-byte binary body
    must not fall through and become ``{}``."""
    res = normalize(ReplyInit(raw=b""))
    assert res.body == b""
    assert "content-type" not in res.headers


def test_the_json_bytes_are_compact_and_utf8() -> None:
    """Python's defaults would emit ``{"name": "caf\\u00e9"}``. A webhook
    signature is computed over these bytes, so the difference is not cosmetic."""
    res = normalize(json_({"name": "café", "n": 1}))
    assert res.body == '{"name":"café","n":1}'.encode()
    assert res.headers["content-type"] == JSON_CONTENT_TYPE


def test_key_order_is_the_producer_s_order_and_is_not_sorted() -> None:
    assert normalize(json_({"b": 1, "a": 2})).body == b'{"b":1,"a":2}'


def test_the_default_status_is_200() -> None:
    assert normalize(ReplyInit()).status == 200
    assert normalize(json_({"x": 1}, 201)).status == 201


def test_a_unit_response_passes_through_untouched() -> None:
    original = UnitResponse(status=207, headers={"x": "1"}, body=b"already")
    assert normalize(original) is original


def test_decode_body_replaces_undecodable_bytes_rather_than_raising() -> None:
    assert decode_body(UnitResponse(status=200, headers={}, body=b"\xff")) == "�"


def test_parse_body_reads_an_empty_body_as_none_and_raises_on_junk() -> None:
    assert parse_body(UnitResponse(status=204, headers={}, body=b"")) is None
    assert parse_body(normalize(json_({"a": 1}))) == {"a": 1}
    with pytest.raises(ValueError):
        parse_body(UnitResponse(status=200, headers={}, body=b"<html>"))
