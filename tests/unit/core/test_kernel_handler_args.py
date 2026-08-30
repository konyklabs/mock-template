"""Where the form-body trap is defeated.

FastAPI needs ``python-multipart`` to read a body even when that body is plain
``application/x-www-form-urlencoded``; a ``Form(...)`` parameter raises at
import time without it and ``await request.form()`` raises at request time.
Either way the content-type decision ends up at the transport edge, which is
the leak the framework-free-core invariant forbids. ``HandlerArgs`` makes that
decision in the core, from raw bytes, with no framework in the process at all
-- and every test in this file runs with the framework blocked, so the claim is
mechanical rather than stated.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

from vendorfake.core.kernel.types import (
    FormData,
    HandlerArgs,
    Route,
    UnitError,
    UnitErrorKind,
    UnitRequest,
)

BLOCKED = ("fastapi", "starlette", "uvicorn", "python_multipart", "multipart")


class _Blocked:
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError(f"{fullname} is not importable in this test")
        return None


@pytest.fixture(autouse=True)
def no_web_framework() -> Iterator[None]:
    finder = _Blocked()
    saved = {name: sys.modules.pop(name, None) for name in BLOCKED}
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


def _route() -> Route:
    return Route(method="POST", path="/oauth2/token", capability="oauth", handler=lambda args: None)


def args(
    raw_body: bytes = b"",
    content_type: str | None = None,
    *,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> HandlerArgs:
    all_headers = dict(headers or {})
    if content_type is not None:
        all_headers["content-type"] = content_type
    req = UnitRequest(
        id="req_1",
        method="POST",
        path="/oauth2/token",
        query=query or {},
        headers=all_headers,
        raw_body=raw_body,
        transport="inprocess",
        received_at="2026-01-01T00:00:00.000Z",
    )
    return HandlerArgs(req=req, params={}, ctx=None, route=_route())  # type: ignore[arg-type]


TOKEN_FORM = (
    b"client_id=sandbox-app-id"
    b"&client_secret=sandbox-app-secret"
    b"&grant_type=authorization_code"
    b"&code=sq0cgb-seeded-authorization-code"
)


class TestTheFormTrap:
    def test_a_form_encoded_token_body_reaches_the_handler_as_fields(self) -> None:
        body = args(TOKEN_FORM, "application/x-www-form-urlencoded").body()
        assert body["client_id"] == "sandbox-app-id"
        assert body["client_secret"] == "sandbox-app-secret"
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "sq0cgb-seeded-authorization-code"

    def test_the_same_fields_arrive_from_the_documented_json_body(self) -> None:
        json_body = args(
            b'{"client_id":"sandbox-app-id","grant_type":"authorization_code"}',
            "application/json",
        ).body()
        assert json_body["client_id"] == "sandbox-app-id"
        assert json_body["grant_type"] == "authorization_code"

    def test_content_type_parameters_do_not_defeat_the_branch(self) -> None:
        body = args(TOKEN_FORM, "application/x-www-form-urlencoded; charset=UTF-8").body()
        assert body["client_id"] == "sandbox-app-id"

    def test_the_media_type_match_is_case_insensitive(self) -> None:
        body = args(TOKEN_FORM, "Application/X-WWW-Form-Urlencoded").body()
        assert body["client_id"] == "sandbox-app-id"

    def test_an_absent_content_type_falls_through_to_json(self) -> None:
        assert args(b'{"a":1}').body() == {"a": 1}

    def test_an_unrecognised_content_type_falls_through_to_json(self) -> None:
        assert args(b'{"a":1}', "text/plain").body() == {"a": 1}

    def test_media_type_strips_parameters_and_casing(self) -> None:
        assert args(b"", "Application/JSON; charset=utf-8").media_type() == "application/json"
        assert args(b"").media_type() == ""


class TestFormSemantics:
    def test_a_single_occurrence_is_a_plain_string_not_a_list(self) -> None:
        # If it were a list, an ordinary client_id=x would fail the vendor's
        # own require_string and the token request would 400 on its parser.
        value = args(b"client_id=x", "application/x-www-form-urlencoded").body()["client_id"]
        assert value == "x"
        assert isinstance(value, str)

    def test_a_repeated_key_reads_as_the_last_value(self) -> None:
        # URLSearchParams parity: Object.fromEntries keeps the final occurrence.
        body = args(b"scope=a&scope=b", "application/x-www-form-urlencoded").body()
        assert body["scope"] == "b"
        assert body.get("scope") == "b"

    def test_a_repeated_key_is_still_recoverable_in_full(self) -> None:
        # Last-wins as a contract, not as lossiness: the repeats survive for a
        # caller that asks for them.
        form = args(b"scope=a&scope=b&scope=c", "application/x-www-form-urlencoded").form()
        assert form.get_all("scope") == ["a", "b", "c"]
        assert form.multi() == {"scope": ["a", "b", "c"]}

    def test_get_all_of_an_absent_key_is_empty(self) -> None:
        assert args(b"a=1", "application/x-www-form-urlencoded").form().get_all("b") == []

    def test_a_blank_value_is_present_as_the_empty_string(self) -> None:
        form = args(b"a=&b=2", "application/x-www-form-urlencoded").form()
        assert form["a"] == ""
        assert "a" in form

    def test_percent_and_plus_encoding_are_decoded(self) -> None:
        form = args(b"redirect_uri=https%3A%2F%2Fx.test%2Fcb&name=a+b", "application/x-www-form-urlencoded").form()
        assert form["redirect_uri"] == "https://x.test/cb"
        assert form["name"] == "a b"

    def test_a_bare_key_with_no_equals_is_present_as_the_empty_string(self) -> None:
        # URLSearchParams("foo") yields foo="" rather than dropping it.
        assert dict(args(b"foo", "application/x-www-form-urlencoded").form()) == {"foo": ""}

    def test_an_empty_form_body_is_an_empty_mapping(self) -> None:
        assert dict(args(b"", "application/x-www-form-urlencoded").form()) == {}

    def test_the_form_is_parsed_once_and_cached(self) -> None:
        handler_args = args(b"a=1", "application/x-www-form-urlencoded")
        assert handler_args.form() is handler_args.form()

    def test_form_data_is_a_mapping_in_first_seen_key_order(self) -> None:
        form = FormData([("b", "1"), ("a", "2"), ("b", "3")])
        assert list(form) == ["b", "a"]
        assert len(form) == 2
        assert dict(form) == {"b": "3", "a": "2"}


class TestJsonSemantics:
    def test_an_empty_body_parses_as_an_empty_object(self) -> None:
        assert args(b"").json() == {}
        assert args(b"   \n ").json() == {}

    def test_unparseable_json_raises_invalid_json_not_internal(self) -> None:
        with pytest.raises(UnitError) as caught:
            args(b"{not json", "application/json").json()
        assert caught.value.kind is UnitErrorKind.INVALID_JSON
        assert "not valid JSON" in str(caught.value)

    def test_json_is_parsed_once_and_cached(self) -> None:
        handler_args = args(b'{"a":1}')
        first = handler_args.json()
        first["a"] = 2
        assert handler_args.json()["a"] == 2

    def test_body_and_json_are_the_same_object_for_a_json_body(self) -> None:
        handler_args = args(b'{"a":1}')
        assert handler_args.body() is handler_args.json()

    def test_a_non_object_json_body_reaches_body_as_invalid_value(self) -> None:
        # json() itself is honest about arrays and scalars; body() promises
        # fields, so it says so rather than handing back something unindexable.
        assert args(b"[1,2]").json() == [1, 2]
        with pytest.raises(UnitError) as caught:
            args(b"[1,2]").body()
        assert caught.value.kind is UnitErrorKind.INVALID_VALUE
        assert caught.value.field == "body"


class TestRawAccess:
    def test_body_text_decodes_utf8(self) -> None:
        assert args("café=1".encode()).body_text() == "café=1"

    def test_undecodable_bytes_become_replacement_characters_not_an_exception(self) -> None:
        # A malformed byte must produce the vendor's own 400, not a 500 from
        # the decoder. TextDecoder behaves the same way.
        assert args(b"\xff\xfe").body_text() == "��"

    def test_query_reads_the_request_query(self) -> None:
        handler_args = args(query={"state": "xyz"})
        assert handler_args.query("state") == "xyz"
        assert handler_args.query("absent") is None

    def test_query_all_reads_every_value_and_is_empty_when_absent(self) -> None:
        handler_args = args(query={"state": "xyz"})
        assert list(handler_args.query_all("state")) == ["xyz"]
        assert list(handler_args.query_all("absent")) == []

    def test_header_lookup_lowercases_the_name(self) -> None:
        handler_args = args(headers={"authorization": "Bearer x"})
        assert handler_args.header("Authorization") == "Bearer x"
        assert handler_args.header("authorization") == "Bearer x"
        assert handler_args.header("absent") is None

    def test_auth_starts_unresolved(self) -> None:
        assert args().auth is None
