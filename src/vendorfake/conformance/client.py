"""The only way a check reaches a unit.

FOR: making the suite a specification rather than a Python artifact. Every
check drives a unit through :class:`ConformanceClient` and asserts only on
what crosses that boundary -- a status, a header map, a byte string. No check
ever receives a unit object, which is why the same contracts can one day be
executed by a consumer written in another language against a running
container.

INVARIANT: **a check cannot tell which implementation it is using.** Both
implementations encode the request with the same function and return the same
concrete :class:`ConformanceResponse`, so there is no attribute to branch on
and no difference in the bytes on the way in. The in-process client is not a
"faster approximation" of the HTTP one; it is the same request, minus a
socket. That is the whole basis of C10, which asserts the two agree byte for
byte -- an assertion that would be circular if the two had different encoders.

WHY THE ENCODING LIVES HERE AND NOT IN EACH CLIENT. ``json.dumps`` with
default separators, a different key order, or a form encoder that spells
spaces as ``+`` in one place and ``%20`` in another would make two bindings
differ on the request side, and C10 would then be comparing two different
requests and calling the result a transport bug. One encoder, used twice.

``form=`` is a first-class parameter, not a header the caller sets by hand.
The form-encoded token request is the exact shape that broke two of three
bake-off entries, and a suite that could only express it by hand-building a
body would let it be an afterthought here too.
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
"""Sentinel for "no JSON body was given".

Distinct from ``None`` because ``null`` is a legitimate JSON document, and a
check probing what a unit does with one must be able to say so.
"""

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
JSON_CONTENT_TYPE = "application/json"

FormPairs = Mapping[str, str] | Sequence[tuple[str, str]]
QueryPairs = Mapping[str, str] | Sequence[tuple[str, str]]


def with_query(path: str, query: QueryPairs | None) -> str:
    """Append ``query`` to ``path`` as a query string, after any it already has.

    Both clients send the query on the path and never through the HTTP
    library's own ``params=``: httpx *replaces* a URL's query when ``params``
    is given, so a check that wrote ``/x?k=a`` with ``query=None`` would reach
    the handler with ``k`` over one binding and without it over the other.
    A sequence of pairs is accepted so a repeated key can be sent at all --
    a mapping cannot spell one.
    """
    pairs = list(query.items()) if isinstance(query, Mapping) else list(query or ())
    if not pairs:
        return path
    return f"{path}{'&' if '?' in path else '?'}{urlencode(pairs)}"


@dataclass(frozen=True, slots=True)
class ConformanceResponse:
    """One answered call: the status, the headers, and the exact bytes.

    Concrete rather than a protocol, and shared by both clients, because "a
    check cannot tell which binding it used" is easiest to guarantee when
    there is only one type to be handed.
    """

    status: int
    #: Lower-cased keys, always -- HTTP header casing is not information, and a
    #: check that had to try both spellings would be asserting about a client.
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        """The body decoded as UTF-8, replacing anything undecodable.

        Never raises: a check reaching for ``.text`` in a failure message must
        not fail differently because the unit answered with bytes.
        """
        return self.body.decode("utf-8", errors="replace")

    @property
    def error_kind(self) -> str | None:
        """The ``x-unit-error`` header: which core error kind this answer is.

        ``None`` means the unit did not shape this answer as an error, which
        for several checks is precisely the assertion.
        """
        return self.headers.get("x-unit-error")

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def json(self) -> Any:
        """The body parsed as JSON.

        Raises ``ValueError`` naming the body when it is not JSON, because a
        caller that reached for this had already decided what it expected.
        """
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
    """Turn a check's request into exact bytes and exact headers.

    Precedence is ``body`` (the caller said the bytes), then ``form``, then
    ``json_body``. A caller-supplied ``content-type`` always wins, so a check
    can send a malformed JSON document *labelled* as JSON -- which is how C04
    proves a validation error never reaches a consumer as a framework's own
    envelope.
    """
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
    """One method, because one method is the whole contract.

    A vendor implementing this against its own transport gets the entire suite
    for free; anything richer here would be something that implementation had
    to reproduce.
    """

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
    """The in-process binding's own surface, structurally.

    Typed structurally rather than imported so that this module states what it
    needs -- one call taking raw bytes -- instead of depending on the concrete
    binding class and, through it, on the kernel.
    """

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
    """The suite against a base URL -- a running unit, or a container.

    A URL and never a server: this package must never start one, because
    starting one would mean importing the web framework the core exists to
    stay clear of. Whoever has a server passes its address in.
    """

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
            # `content=` and never `json=`/`data=`: httpx would re-encode, and
            # the two bindings would then differ on the request side.
            content=payload,
        )
        return ConformanceResponse(
            status=answered.status_code,
            headers={key.lower(): value for key, value in answered.headers.items()},
            body=answered.content,
        )

    def close(self) -> None:
        self._client.close()
