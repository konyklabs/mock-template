"""The only way a check reaches a unit.

A check drives a unit through :class:`ConformanceClient` and asserts only on what crosses that boundary -- a status, a header map, a byte string -- never a unit object, so the same contracts could one day run against another language's implementation.

A check cannot tell which client implementation it is using: both encode the request with the same function and return the same concrete :class:`ConformanceResponse` -- the same request, minus a socket for the in-process one. That is the basis of C10, which asserts the two agree byte for byte; a per-client encoder would make that circular.

``form=`` is a first-class parameter, not a header the caller sets by hand, so a form-encoded body is never an afterthought here.
"""

from __future__ import annotations

import json as _json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlencode

import httpx

__all__ = [
    "MISSING",
    "ConformanceClient",
    "ConformanceResponse",
    "HttpConformanceClient",
    "InProcessConformanceClient",
    "encode_request",
    "with_query",
]

MISSING: Any = object()
"""Sentinel for "no JSON body was given" -- distinct from ``None``, since ``null`` is a legitimate JSON document a check may need to send."""

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
JSON_CONTENT_TYPE = "application/json"

FormPairs = Mapping[str, str] | Sequence[tuple[str, str]]
QueryPairs = Mapping[str, str] | Sequence[tuple[str, str]]


def with_query(path: str, query: QueryPairs | None) -> str:
    """Append ``query`` to ``path`` as a query string, after any it already has -- never through httpx's own ``params=``, which replaces a URL's existing query. A sequence of pairs is accepted so a repeated key can be sent; a mapping cannot spell one."""
    pairs = list(query.items()) if isinstance(query, Mapping) else list(query or ())
    if not pairs:
        return path
    return f"{path}{'&' if '?' in path else '?'}{urlencode(pairs)}"


@dataclass(frozen=True, slots=True)
class ConformanceResponse:
    """One answered call: the status, the headers, and the exact bytes. Concrete rather than a protocol, and shared by both clients, so there is only one type to hand back."""

    status: int
    #: Lower-cased keys, always -- HTTP header casing is not information.
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        """The body decoded as UTF-8, replacing anything undecodable. Never raises, so a failure message can always reach for it."""
        return self.body.decode("utf-8", errors="replace")

    @property
    def error_kind(self) -> str | None:
        """The ``x-unit-error`` header: which core error kind this answer is, or ``None`` if the unit did not shape it as an error."""
        return self.headers.get("x-unit-error")

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def json(self) -> Any:
        """The body parsed as JSON. Raises ``ValueError`` naming the body when it is not JSON."""
        text = self.text
        if not text:
            return None
        try:
            return _json.loads(text)
        except ValueError as exc:
            raise ValueError(f"response body is not JSON ({exc}): {text[:400]!r}") from exc


def encode_request(
    *,
    json_body: Any = MISSING,
    form: FormPairs | None = None,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Turn a check's request into exact bytes and exact headers. Precedence is ``body``, then ``form``, then ``json_body``; a caller-supplied ``content-type`` always wins, so a check can send a malformed JSON document labelled as JSON (how C04 works)."""
    sent = {key.lower(): value for key, value in (headers or {}).items()}
    given = sum(1 for value in (body, form) if value is not None) + (0 if json_body is MISSING else 1)
    if given > 1:
        raise ValueError("pass at most one of body=, form= or json_body=; they are three spellings of one body")

    if body is not None:
        return body, sent
    if form is not None:
        pairs = list(form.items()) if isinstance(form, Mapping) else list(form)
        sent.setdefault("content-type", FORM_CONTENT_TYPE)
        return urlencode(pairs).encode("utf-8"), sent
    if json_body is not MISSING:
        sent.setdefault("content-type", JSON_CONTENT_TYPE)
        return _json.dumps(json_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), sent
    return b"", sent


@runtime_checkable
class ConformanceClient(Protocol):
    """One method, because one method is the whole contract: a vendor implementing this against its own transport gets the entire suite for free."""

    def call(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = MISSING,
        form: FormPairs | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        query: QueryPairs | None = None,
    ) -> ConformanceResponse: ...


class _UnitLike(Protocol):
    """The in-process binding's own surface, typed structurally so this module states what it needs -- one call taking raw bytes -- without depending on the kernel."""

    def call(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = ...,
        headers: Mapping[str, str] | None = ...,
        body: object = ...,
        raw_body: bytes | str | None = ...,
    ) -> Any: ...


class InProcessConformanceClient:
    """The suite over the in-process binding: the same request, minus a socket."""

    __slots__ = ("_binding",)

    def __init__(self, binding: _UnitLike) -> None:
        self._binding = binding

    def call(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = MISSING,
        form: FormPairs | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        query: QueryPairs | None = None,
    ) -> ConformanceResponse:
        payload, sent = encode_request(json_body=json_body, form=form, body=body, headers=headers)
        answered = self._binding.call(
            method=method,
            path=with_query(path, query),
            headers=sent,
            raw_body=payload,
        )
        return ConformanceResponse(
            status=int(answered.status),
            headers={key.lower(): value for key, value in dict(answered.headers).items()},
            body=bytes(answered.body),
        )


class HttpConformanceClient:
    """The suite against a base URL -- a running unit, or a container. A URL and never a server: this package never starts one itself."""

    __slots__ = ("_client",)

    def __init__(self, base_url: str, *, timeout_s: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)

    def call(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = MISSING,
        form: FormPairs | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        query: QueryPairs | None = None,
    ) -> ConformanceResponse:
        payload, sent = encode_request(json_body=json_body, form=form, body=body, headers=headers)
        answered = self._client.request(
            method.upper(),
            with_query(path, query),
            headers=sent,
            content=payload,  # never json=/data=: httpx would re-encode and the two bindings would differ
        )
        return ConformanceResponse(
            status=answered.status_code,
            headers={key.lower(): value for key, value in answered.headers.items()},
            body=answered.content,
        )

    def close(self) -> None:
        self._client.close()
