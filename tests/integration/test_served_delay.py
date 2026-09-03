"""A deliberate delay, over a real socket, seen by a client that owns a timeout.

Marked ``integration`` because it needs a server: uvicorn on a background
thread, in front of an ordinary in-process unit.

This is the half of the reversal the in-process tests cannot prove. When the
kernel stopped sleeping for the ``timeout`` fault, the risk was that served
mode stopped delaying too -- and a served unit that answered a five-second
``timeout`` fault instantly would silently break every consumer whose retry
test points at a URL, with the fault still reporting the delay it never took.
So the ASGI binding awaits it, and this asserts that a real ``httpx.Client``
with a real timeout, over a real socket, sees exactly what it saw before.

Note what is *not* short-circuited here. In process, a delay longer than the
caller's read timeout raises at once because the binding is holding the caller
and can answer for them. Over a socket it cannot: the client's timeout is the
client's business, and the server simply does not answer until it does. That
asymmetry is deliberate, and it is why the elapsed floor below is asserted
against the client's timeout rather than against the delay.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import pytest

from vendorfake.testing import Driver, serve_in_thread, unit

pytestmark = pytest.mark.integration

DELAY_MS = 2000
"""Long enough to be unmistakably longer than the client timeout below, short
enough that the one test that does wait for it costs two seconds."""

CLIENT_TIMEOUT_S = 0.2


@pytest.fixture
def served_square() -> Iterator[Driver]:
    with unit("square") as started, serve_in_thread(started) as driver:
        yield driver


def test_a_delayed_response_times_the_socket_client_out(served_square: Driver) -> None:
    """The consumer-facing claim: arm the fault, point a normal client at the
    URL, and the client raises the exception its retry loop is written for."""
    served_square.add_chaos_rule(
        {
            "id": "slow",
            "scope": "request",
            "fault": "timeout",
            "match": {"route": "GET /v2/locations"},
            "params": {"delay_ms": DELAY_MS},
        }
    )
    with httpx.Client(base_url=served_square.base_url, timeout=CLIENT_TIMEOUT_S) as client:
        begun = time.monotonic()
        with pytest.raises(httpx.ReadTimeout):
            client.get("/v2/locations", headers={"authorization": "Bearer irrelevant"})
        elapsed_s = time.monotonic() - begun

    # It waited, and it waited about as long as the client asked to wait --
    # not the full delay, because the client gave up first.
    assert elapsed_s >= CLIENT_TIMEOUT_S
    assert elapsed_s < DELAY_MS / 1000.0


def test_a_patient_client_gets_the_fault_after_the_delay(served_square: Driver) -> None:
    """The same rule, a client willing to wait: the answer arrives, late.

    Without this the test above would also pass against a server that had
    simply stopped answering, which is a different defect with the same symptom.
    """
    served_square.add_chaos_rule(
        {
            "id": "slow",
            "scope": "request",
            "fault": "timeout",
            "match": {"route": "GET /v2/locations"},
            "params": {"delay_ms": DELAY_MS},
        }
    )
    with httpx.Client(base_url=served_square.base_url, timeout=30.0) as client:
        begun = time.monotonic()
        answered = client.get("/v2/locations", headers={"authorization": "Bearer irrelevant"})
        elapsed_s = time.monotonic() - begun

    assert answered.status_code == 504
    assert answered.headers["x-unit-error"] == "timeout"
    assert elapsed_s >= DELAY_MS / 1000.0, f"answered after {elapsed_s:.2f}s, short of {DELAY_MS / 1000.0}s"


def test_the_delay_does_not_block_the_server_for_everyone_else(served_square: Driver) -> None:
    """``await asyncio.sleep`` and not ``time.sleep`` on the worker thread.

    A delayed request must not hold the event loop, or one armed fault would
    make the whole unit unreachable -- including the control-plane call a test
    needs in order to disarm it. Proved by asking for something else while a
    two-second delay is outstanding, and getting it promptly.
    """
    served_square.add_chaos_rule(
        {
            "id": "slow",
            "scope": "request",
            "fault": "timeout",
            "match": {"route": "GET /v2/locations"},
            "params": {"delay_ms": DELAY_MS},
        }
    )
    with (
        httpx.Client(base_url=served_square.base_url, timeout=CLIENT_TIMEOUT_S) as slow,
        httpx.Client(base_url=served_square.base_url, timeout=5.0) as quick,
    ):
        with pytest.raises(httpx.ReadTimeout):
            slow.get("/v2/locations", headers={"authorization": "Bearer irrelevant"})
        begun = time.monotonic()
        health = quick.get("/__unit/health")
        elapsed_s = time.monotonic() - begun

    assert health.status_code == 200
    assert elapsed_s < 1.0, f"an unrelated request waited {elapsed_s:.2f}s behind a delayed one"
