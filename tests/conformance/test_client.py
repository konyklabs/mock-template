"""The request encoder is shared by both bindings, so it is pinned by itself.

If the two clients encoded differently, C10 would be comparing two different
requests and reporting the difference as a transport defect.
"""

from __future__ import annotations

import pytest

from vendorfake.conformance.client import (
    FORM_CONTENT_TYPE,
    JSON_CONTENT_TYPE,
    ConformanceResponse,
    encode_request,
)
from vendorfake.conformance.env import ancestors, concrete_path


def test_a_json_body_is_compact_and_labelled() -> None:
    body, headers = encode_request(json_body={"b": 1, "a": [1, 2]})
    assert body == b'{"b":1,"a":[1,2]}'
    assert headers["content-type"] == JSON_CONTENT_TYPE


def test_a_form_body_keeps_repeated_keys_in_order() -> None:
    body, headers = encode_request(form=[("scope", "one"), ("scope", "two")])
    assert body == b"scope=one&scope=two"
    assert headers["content-type"] == FORM_CONTENT_TYPE


def test_raw_bytes_are_sent_untouched_and_unlabelled() -> None:
    body, headers = encode_request(body=b"{not json", headers={"Content-Type": "application/json"})
    assert body == b"{not json"
    assert headers == {"content-type": "application/json"}


def test_a_callers_content_type_always_wins() -> None:
    _, headers = encode_request(json_body={}, headers={"content-type": "text/plain"})
    assert headers["content-type"] == "text/plain"


def test_no_body_is_empty_bytes_and_no_content_type() -> None:
    assert encode_request() == (b"", {})


def test_two_body_spellings_at_once_are_refused() -> None:
    with pytest.raises(ValueError, match="at most one"):
        encode_request(json_body={}, form={"a": "b"})


def test_a_response_exposes_bytes_text_json_and_the_error_kind() -> None:
    res = ConformanceResponse(status=400, headers={"x-unit-error": "invalid_json"}, body=b'{"a":1}')
    assert res.json() == {"a": 1}
    assert res.text == '{"a":1}'
    assert res.error_kind == "invalid_json"
    assert res.header("X-Unit-Error") == "invalid_json"


def test_a_non_json_body_names_itself_in_the_error() -> None:
    res = ConformanceResponse(status=500, headers={}, body=b"<html>nope</html>")
    with pytest.raises(ValueError, match="nope"):
        res.json()


def test_concrete_path_fills_every_template_segment() -> None:
    assert concrete_path("/v2/orders/{order_id}/pay") == "/v2/orders/conformance-probe/pay"


def test_ancestors_walks_dotted_capability_names() -> None:
    assert ancestors("a.b.c") == ("a", "a.b")
    assert ancestors("a") == ()
