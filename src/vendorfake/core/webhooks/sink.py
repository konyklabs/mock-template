"""Outbound transport for one delivery attempt: the dispatcher decides what to send and
when to retry, this decides how the bytes leave.

**The sink is the only place in the core that may reach the network, and the only place
``httpx`` may be imported** (``tools/boundary.toml`` records the permission), because per
D-001 the core does not assume HTTP. ``SinkResult.status == 0`` is a contract: there is
no status when the transport failed before a response existed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

__all__ = [
    "DeliverySink",
    "HttpSink",
    "MemorySink",
    "SinkRequest",
    "SinkResult",
]

_SNIPPET_LIMIT = 200
"""How much of a subscriber's response body is kept, so it cannot fill the log."""


@dataclass(frozen=True, slots=True)
class SinkRequest:
    """One outbound delivery. ``body`` is bytes: the signature covers these exact bytes."""

    url: str
    headers: Mapping[str, str]
    body: bytes
    timeout_ms: int


@dataclass(frozen=True, slots=True)
class SinkResult:
    status: int
    body_snippet: str | None = None
    error: str | None = None
    #: True when nothing came back in time; ``status == 0`` also covers a refusal.
    timed_out: bool = False


class DeliverySink(Protocol):
    @property
    def kind(self) -> str: ...

    def send(self, req: SinkRequest) -> SinkResult:
        """Deliver once. Never raises: the retry decision is driven by the returned value."""
        ...


class MemorySink:
    kind = "memory"

    def __init__(self, respond_with: int | Callable[[SinkRequest, int], int] = 200) -> None:
        self.received: list[SinkRequest] = []
        #: A status, or a function of ``(request, 0-based call index on this sink)``.
        self.respond_with: int | Callable[[SinkRequest, int], int] = respond_with
        self._lock = threading.Lock()

    def send(self, req: SinkRequest) -> SinkResult:
        with self._lock:
            index = len(self.received)
            self.received.append(req)
        responder = self.respond_with
        status = responder(req, index) if callable(responder) else responder
        if status == 0:
            return SinkResult(status=0, error="simulated transport failure", timed_out=True)
        return SinkResult(status=status)

    def clear(self) -> None:
        with self._lock:
            self.received.clear()


class HttpSink:
    """Posts each delivery over HTTP. One thread-safe client, built on first use."""

    kind = "http"

    def __init__(self, *, verify: bool = True) -> None:
        self._verify = verify
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _ensure_client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(verify=self._verify, follow_redirects=False)
            return self._client

    def send(self, req: SinkRequest) -> SinkResult:
        """Post once; every failure mode becomes a :class:`SinkResult`. ``follow_redirects``
        is off because a subscriber answering ``302`` has not accepted the delivery."""
        client = self._ensure_client()
        try:
            res = client.post(
                req.url,
                headers=dict(req.headers),
                content=req.body,
                timeout=req.timeout_ms / 1000.0,
            )
        except httpx.TimeoutException as exc:
            return SinkResult(status=0, error=_describe(exc), timed_out=True)
        except httpx.HTTPError as exc:
            return SinkResult(status=0, error=_describe(exc), timed_out=False)
        return SinkResult(status=res.status_code, body_snippet=res.text[:_SNIPPET_LIMIT])

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()


def _describe(exc: BaseException) -> str:
    """A description that never collapses to the empty string, which would read as no error."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
