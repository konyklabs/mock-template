"""Turning what a handler returned into the exact bytes that go out.

FOR: giving every handler in every vendor one way to say "200 with this
object", "302 to there", "204" or "these exact bytes", and one place where that
becomes a :class:`UnitResponse`.

INVARIANT: **precedence is contract, and it is keyed on presence, never on
truthiness.** ``raw`` wins, then ``text``, then JSON, and each is chosen with
``is not None``. That distinction is the whole reason this docstring is long:

* :func:`redirect` and :func:`no_content` both return ``text=""``. Under a
  truthiness test (``if init.text:``) an empty string is false, so both fall
  through to the JSON branch and a 302 goes out as ``{}`` with
  ``content-type: application/json`` and a two-byte body. The reference writes
  ``r.text !== undefined`` for exactly this reason, and its ``GET
  /oauth2/authorize`` -- the first thing an OAuth consumer touches -- is a
  redirect.
* A zero-length text body gets **no** ``content-type`` at all
  (``r.text.length > 0 && !headers['content-type']`` in the reference), so a
  204 carries no content type, which is what RFC 9110 asks for.
* ``json=None`` serialises to ``b"{}"``, never ``b"null"``: the reference's
  ``JSON.stringify(r.json ?? {})`` collapses both of JavaScript's empty values
  onto the same two bytes, and Python has only one of them. A handler that
  genuinely wants a JSON ``null`` body says so with ``raw=b"null"``.

Serialisation goes through :func:`vendorfake.core.util.json.dump_json` and
nowhere else, so a response body, a webhook body and a log line agree on
separators and on non-ASCII text -- which is not cosmetic, because a webhook
signature is computed over those bytes.

One deviation from the reference, taken deliberately: header names are
lower-cased here. The reference looks ``headers['content-type']`` up by exact
key, so a handler returning ``Content-Type`` would receive a *second*,
conflicting ``content-type: application/json``. HTTP header names are
case-insensitive; lower-casing once, at the only place a response is built,
makes that unrepresentable and makes a byte-for-byte comparison of two
bindings' headers meaningful.
"""

from __future__ import annotations

import json as _json
from collections.abc import Mapping
from typing import Any

from vendorfake.core.kernel.types import ReplyInit, UnitResponse
from vendorfake.core.util.json import dump_json

__all__ = [
    "JSON_CONTENT_TYPE",
    "TEXT_CONTENT_TYPE",
    "decode_body",
    "json_",
    "no_content",
    "normalize",
    "parse_body",
    "redirect",
    "text",
]

JSON_CONTENT_TYPE = "application/json"
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"


def json_(body: Any, status: int = 200, headers: Mapping[str, str] | None = None) -> ReplyInit:
    """A JSON reply. Named with a trailing underscore so the module can still
    reach the standard library's ``json``, which :func:`dump_json` uses."""
    return ReplyInit(status=status, json=body, headers=headers)


def text(body: str, status: int = 200, headers: Mapping[str, str] | None = None) -> ReplyInit:
    """A ``text/plain`` reply. An empty ``body`` sends no content type."""
    return ReplyInit(status=status, text=body, headers=headers)


def redirect(location: str, status: int = 302) -> ReplyInit:
    """A redirect: a ``location`` header and a zero-byte body."""
    return ReplyInit(status=status, headers={"location": location}, text="")


def no_content() -> ReplyInit:
    """``204``: no body, no content type."""
    return ReplyInit(status=204, text="")


def normalize(init: ReplyInit | UnitResponse) -> UnitResponse:
    """Resolve a handler's return into serialised bytes exactly once.

    A :class:`UnitResponse` passes through untouched -- a handler that has
    already produced bytes (an idempotent replay, a proxied body) must not have
    them re-encoded.
    """
    if isinstance(init, UnitResponse):
        return init

    headers: dict[str, str] = {}
    if init.headers is not None:
        for name, value in init.headers.items():
            headers[name.lower()] = value

    body: bytes
    if init.raw is not None:
        body = init.raw
    elif init.text is not None:
        body = init.text.encode("utf-8")
        if init.text and "content-type" not in headers:
            headers["content-type"] = TEXT_CONTENT_TYPE
    else:
        body = dump_json({} if init.json is None else init.json)
        headers.setdefault("content-type", JSON_CONTENT_TYPE)

    return UnitResponse(status=200 if init.status is None else init.status, headers=headers, body=body)


def decode_body(res: UnitResponse) -> str:
    """The response body as text. Undecodable bytes become U+FFFD rather than
    raising, matching ``TextDecoder``: a test asserting on a garbled body must
    see the garble, not an exception from its own assertion helper."""
    return res.body.decode("utf-8", errors="replace")


def parse_body(res: UnitResponse) -> Any:
    """The response body parsed as JSON; ``None`` for an empty body.

    Raises whatever :mod:`json` raises for a non-JSON body. Callers that must
    tolerate one -- the in-process binding, which cannot know what a route
    returns -- catch it themselves rather than being handed a silent ``None``
    that hides the difference between "no body" and "not JSON".
    """
    raw = decode_body(res)
    if not raw:
        return None
    return _json.loads(raw)
