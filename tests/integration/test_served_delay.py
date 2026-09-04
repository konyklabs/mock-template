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

import threading
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
    """Rules out an inline blocking sleep in ``dispatch`` itself.

    A delayed request must not hold the event loop, or one armed fault would
    make the whole unit unreachable -- including the control-plane call a test
    needs in order to disarm it. Proved by asking for something else while a
    two-second delay is outstanding, and getting it promptly.

    What this does *not* rule out: ``dispatch`` awaits
    ``run_in_threadpool(unit.handle, ...)`` before it ever reaches the delay,
    and that pool holds 40 workers by default. A regression that took the
    delay itself via the pool (``run_in_threadpool(time.sleep, ...)`` in place
    of the real ``await asyncio.sleep``) would still leave 39 workers free for
    this test's one unrelated request, so it would stay green here. See
    :func:`test_the_delay_does_not_saturate_the_worker_thread_pool` for the
    version that actually distinguishes the two.
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


#: anyio's default worker-thread limiter for ``run_in_threadpool`` caps
#: concurrent calls at 40
#: (``anyio.to_thread.current_default_thread_limiter().total_tokens``,
#: unconfigured by this project). One more than that outstanding at once is
#: what the test below needs to guarantee every worker thread is occupied if
#: the delay were ever taken on one.
WORKER_THREAD_POOL_SIZE = 40


def test_the_delay_does_not_saturate_the_worker_thread_pool(served_square: Driver) -> None:
    """Closes the gap the test above discloses but cannot rule out itself.

    Fires one more concurrently delayed request than anyio's default 40
    worker threads, then asks for something else. If the delay costs no
    thread at all -- a coroutine suspended on the event loop, which is what
    ``dispatch`` actually does -- the unrelated request answers immediately no
    matter how many delayed requests are outstanding. If a regression took
    the delay via ``run_in_threadpool`` instead, all 40 workers would be busy
    sleeping and the unrelated request would queue behind them, well past the
    bound below.
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

        def _drive_one() -> None:
            with pytest.raises(httpx.ReadTimeout):
                slow.get("/v2/locations", headers={"authorization": "Bearer irrelevant"})

        threads = [threading.Thread(target=_drive_one) for _ in range(WORKER_THREAD_POOL_SIZE + 1)]
        for thread in threads:
            thread.start()
        time.sleep(0.1)  # let every delayed request reach the server and enter the wait
        begun = time.monotonic()
        health = quick.get("/__unit/health")
        elapsed_s = time.monotonic() - begun
        for thread in threads:
            thread.join(timeout=CLIENT_TIMEOUT_S + 5.0)

    assert health.status_code == 200
    assert elapsed_s < 1.0, (
        f"an unrelated request waited {elapsed_s:.2f}s behind {WORKER_THREAD_POOL_SIZE + 1} delayed ones"
    )
