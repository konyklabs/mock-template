"""Turning what a handler returned into the exact bytes that go out.

INVARIANT: **precedence is keyed on presence, never on truthiness.** ``raw`` wins, then ``text``, then JSON, each
chosen with ``is not None``, because :func:`redirect` and :func:`no_content` both return ``text=""`` and a
truthiness test would send them out as a JSON ``{}``. A zero-length text body gets no ``content-type`` at all, per
RFC 9110. ``json=None`` serialises to ``b"{}"``, never ``b"null"``; a handler wanting a JSON ``null`` says
``raw=b"null"``. Serialisation goes through :func:`vendorfake.core.util.json.dump_json` and nowhere else, so a
response body, a webhook body and a log line agree on separators and on non-ASCII text, which a webhook signature is
computed over. Header names are lower-cased here, the only place a response is built.
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
    """A JSON reply; trailing underscore to leave ``json`` free."""
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
    """Resolve a handler's return into serialised bytes exactly once. A
    :class:`UnitResponse` passes through untouched, never re-encoded."""
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
    """The response body as text; undecodable bytes become U+FFFD."""
    return res.body.decode("utf-8", errors="replace")


def parse_body(res: UnitResponse) -> Any:
    """The response body parsed as JSON; ``None`` for an empty body, and
    whatever :mod:`json` raises for a non-JSON one."""
    raw = decode_body(res)
    if not raw:
        return None
    return _json.loads(raw)
