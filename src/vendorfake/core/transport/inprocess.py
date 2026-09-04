"""The in-process binding: call a unit directly, with no socket -- the seam every test and the
conformance suite drives, so a unit with no port is still fully exercisable.

It converts, not interprets: ``.text``/``.json()`` are derived from the untouched
:class:`UnitResponse` on ``.raw``, never from anything kept on the side. It does not wait, either:
a ``timeout`` fault sets ``UnitResponse.delay_ms``, but this binding holds no caller to carry the
delay out on a clock, so elapsed wall time here measures the unit, not a sleep.
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
    status: int
    headers: Mapping[str, str]
    raw: UnitResponse

    @property
    def body(self) -> bytes:
        return self.raw.body

    @property
    def text(self) -> str:
        """The body decoded as UTF-8, undecodable bytes shown rather than raised."""
        return decode_body(self.raw)

    def json(self) -> Any:
        """The body parsed as JSON, or ``None`` for empty; raises ``ValueError`` (with the body) if it is not JSON."""
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
        """Build a request, hand it to the unit, and wrap the response; ``raw_body`` wins over ``body``."""
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
