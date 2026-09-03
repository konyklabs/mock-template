"""The in-process binding: call a unit directly, with no socket.

FOR: being the primary seam every test and the whole conformance suite drives.
A unit that was never given a port must still be fully exercisable, because
that is what makes the conformance contracts a statement about the *unit* and
not about a server someone remembered to start.

INVARIANT: **it converts, and it does not interpret.** The response object
below carries the untouched :class:`UnitResponse` on ``.raw``, and every
convenience on top of it -- ``.text``, ``.json()`` -- is derived from those
exact bytes rather than from anything the binding kept on the side. A binding
that re-serialised a parsed body would make the thing under test invisible: a
webhook signature covers received bytes, and a byte-for-byte comparison
between two bindings is only meaningful if neither of them touched the bytes.

THAT IS ALSO WHY IT DOES NOT WAIT. A ``timeout`` chaos fault sets
``UnitResponse.delay_ms``, and every binding that holds a *caller* -- the
``httpx`` transport, the ASGI application, the file drop -- carries the delay
out on that caller's clock. This one holds no caller: it is a function call, so
there is nobody to make wait and nothing to time out. The delay is on
``.raw.delay_ms`` for a test that wants to assert the fault asked for one, and
elapsed wall time here stays a measurement of the unit rather than of a sleep
the binding chose to take. A suite driving this client sees the fault's status
and body exactly as a socket client does; what it does not see is the pause.

``json()`` raises on a body that is not JSON rather than returning ``None``.
The reference's in-process client swallows the parse error and hands back the
raw text in the same field, so a test asserting ``body["id"]`` against an HTML
error page fails with ``TypeError: string indices must be integers`` several
frames away from the cause. Here the failure names the body.

Speed is the second reason this exists and it is not a small one: a few
hundred assertions per second instead of a few dozen, which is the difference
between running the conformance suite on every change and running it in CI
only.
"""

from __future__ import annotations

import json as _json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vendorfake.core.kernel.reply import decode_body
from vendorfake.core.kernel.types import UnitResponse
from vendorfake.core.kernel.unit import Unit, make_request

__all__ = ["InProcessClient", "InProcessResponse", "in_process"]

TRANSPORT = "inprocess"


@dataclass(frozen=True, slots=True)
class InProcessResponse:
    """One answered call: the status, the headers, and the exact bytes."""

    status: int
    headers: Mapping[str, str]
    raw: UnitResponse

    @property
    def body(self) -> bytes:
        """The exact response bytes."""
        return self.raw.body

    @property
    def text(self) -> str:
        """The body decoded as UTF-8, undecodable bytes shown rather than raised."""
        return decode_body(self.raw)

    def json(self) -> Any:
        """The body parsed as JSON; ``None`` for an empty body.

        Raises ``ValueError`` -- with the offending body in the message -- when
        the body is not JSON, because a test that reached for ``.json()`` has
        already asserted what it expected and deserves to be told what it got.
        """
        text = self.text
        if not text:
            return None
        try:
            return _json.loads(text)
        except ValueError as exc:
            raise ValueError(f"response body is not JSON ({exc}): {text[:400]!r}") from exc

    def header(self, name: str) -> str | None:
        """One header, looked up case-insensitively."""
        return self.headers.get(name.lower())


class InProcessClient:
    """A tiny request builder bound to one unit."""

    __slots__ = ("_unit",)

    def __init__(self, unit: Unit) -> None:
        self._unit = unit

    def call(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: object = None,
        raw_body: bytes | str | None = None,
        transport: str = TRANSPORT,
        request_id: str | None = None,
    ) -> InProcessResponse:
        """Build a request, hand it to the unit, and wrap what comes back.

        ``raw_body`` wins over ``body``: a caller testing a form-encoded or a
        deliberately malformed body must be able to say the exact bytes, and a
        caller testing ordinary JSON should not have to serialise it by hand.
        """
        res = self._unit.handle(
            make_request(
                method=method,
                path=path,
                query=query,
                headers=headers,
                body=body,
                raw_body=raw_body,
                transport=transport,
                request_id=request_id,
            )
        )
        return InProcessResponse(status=res.status, headers=dict(res.headers), raw=res)

    def get(self, path: str, **kwargs: Any) -> InProcessResponse:
        return self.call(method="GET", path=path, **kwargs)

    def post(self, path: str, body: object = None, **kwargs: Any) -> InProcessResponse:
        return self.call(method="POST", path=path, body=body, **kwargs)

    def put(self, path: str, body: object = None, **kwargs: Any) -> InProcessResponse:
        return self.call(method="PUT", path=path, body=body, **kwargs)

    def patch(self, path: str, body: object = None, **kwargs: Any) -> InProcessResponse:
        return self.call(method="PATCH", path=path, body=body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> InProcessResponse:
        return self.call(method="DELETE", path=path, **kwargs)


def in_process(unit: Unit) -> InProcessClient:
    """The binding, as a function, so a fixture reads ``api = in_process(unit)``."""
    return InProcessClient(unit)
