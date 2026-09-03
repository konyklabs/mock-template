"""``connection_reset``, ``empty_response`` and ``slow_body``, over a real
socket -- the half of E1 the in-process transport cannot prove, because it
holds no socket to reset, starve or stream over in the first place.

Marked ``integration``: uvicorn on a background thread, in front of an
ordinary in-process unit, exactly as ``test_served_delay.py`` does for the
``timeout`` fault this stream builds on.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import httpx
import pytest

from vendorfake.testing import Driver, serve_in_thread, unit

pytestmark = pytest.mark.integration


@pytest.fixture
def served_square() -> Iterator[Driver]:
    with unit("square") as started, serve_in_thread(started) as driver:
        yield driver


def _token_body(seed: object) -> dict[str, str]:
    return {
        "client_id": seed.application_id,  # type: ignore[attr-defined]
        "client_secret": seed.application_secret,  # type: ignore[attr-defined]
        "grant_type": "refresh_token",
        "refresh_token": seed.refresh_token,  # type: ignore[attr-defined]
    }


def test_connection_reset_is_an_incomplete_response_to_a_real_client(served_square: Driver) -> None:
    """ "After http.response.start, close without completing" -- the spec's own
    words for this fault -- observed from a real ``httpx.Client`` against a
    real uvicorn server, not asserted from inside the ASGI application."""
    served_square.add_chaos_rule(
        {"id": "reset", "scope": "request", "fault": "connection_reset", "match": {"route": "POST /oauth2/token"}}
    )
    with (
        httpx.Client(base_url=served_square.base_url, timeout=5.0) as client,
        pytest.raises(httpx.TransportError) as caught,
    ):
        client.post("/oauth2/token", json=_token_body(served_square.seed))
    # The exact exception class is the ASGI server's call, not this fault's;
    # what the fault promises is that the response never completes. Recorded
    # here rather than asserted narrowly, per the spec's own "document the
    # closest achievable behaviour".
    assert isinstance(caught.value, httpx.TransportError)


def test_empty_response_is_also_an_incomplete_response(served_square: Driver) -> None:
    """The closest this ASGI server can get to "before any bytes": headers
    have already gone out by the time any binding could refuse to send a
    body (Starlette sends ``http.response.start`` before it ever asks a
    streaming body for its first chunk), so this and ``connection_reset``
    are observably the same fault over a socket -- see
    ``asgi/app.py``'s ``TransportFaultAbort`` docstring.
    """
    served_square.add_chaos_rule(
        {"id": "empty", "scope": "request", "fault": "empty_response", "match": {"route": "POST /oauth2/token"}}
    )
    with httpx.Client(base_url=served_square.base_url, timeout=5.0) as client, pytest.raises(httpx.TransportError):
        client.post("/oauth2/token", json=_token_body(served_square.seed))


def test_slow_body_streams_for_real_and_a_short_read_timeout_fires_mid_stream(served_square: Driver) -> None:
    """Unlike the in-process transport, this binding holds a real socket: the
    client's own read timeout races the real gap between two chunks, with
    nothing here needing to predict the outcome.

    The gap (500 ms) is deliberately larger than the client's read timeout
    (200 ms), not merely their sum: httpx's read timeout is inactivity-based
    *per chunk* ("the maximum duration to wait for a chunk of data"), so a
    client with a 200 ms timeout reading many 60 ms gaps would never time out
    at all, no matter how long the whole transfer took -- proved by the next
    test, which relies on exactly that. ``testing/transport.py``'s
    ``_would_exhaust_read_timeout_ms`` races the single gap for the same
    reason, so a test green there is green here too.
    """
    served_square.add_chaos_rule(
        {
            "id": "slow",
            "scope": "request",
            "fault": "slow_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"chunk_bytes": 8, "chunk_delay_ms": 500},
        }
    )
    with httpx.Client(base_url=served_square.base_url, timeout=0.2) as client:
        begun = time.monotonic()
        with pytest.raises(httpx.ReadTimeout):
            client.post("/oauth2/token", json=_token_body(served_square.seed))
        elapsed_s = time.monotonic() - begun
    # It genuinely waited for the client's own timeout to elapse mid-gap --
    # proof this is a real stream racing a real clock, not a precomputed
    # refusal the way the in-process transport's is.
    assert elapsed_s >= 0.15


def test_slow_body_with_gaps_under_the_read_timeout_never_times_out_however_long_the_whole_transfer_takes(
    served_square: Driver,
) -> None:
    """The other half of the read-timeout race, and the one that could look
    like a bug if it were not asserted directly: many gaps, each comfortably
    under a short read timeout, summing to well over ten times that timeout in
    total -- and the client still gets a clean 200, because nothing ever made
    it wait longer than one gap at a time.
    """
    served_square.add_chaos_rule(
        {
            "id": "slow",
            "scope": "request",
            "fault": "slow_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"chunk_bytes": 8, "chunk_delay_ms": 60},
        }
    )
    with httpx.Client(base_url=served_square.base_url, timeout=0.2) as client:
        response = client.post("/oauth2/token", json=_token_body(served_square.seed))
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_slow_body_with_an_explicit_zero_delay_streams_with_no_gap_served(served_square: Driver) -> None:
    """An explicit ``chunk_delay_ms: 0`` must produce no gap between chunks
    served, the same as it already does in process
    (``tests/unit/test_transport_faults_binding.py``): zero is a value a rule
    can reach on purpose, not "unset". Before this fix ``asgi/app.py``
    substituted its own 100ms default over an explicit zero, so the same
    directive streamed instantly in process and dribbled for whole seconds
    served, with no rule anywhere asking for that gap -- see
    ``asgi/app.py``'s ``_directive_response`` docstring.
    """
    served_square.add_chaos_rule(
        {
            "id": "slow-zero",
            "scope": "request",
            "fault": "slow_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"chunk_bytes": 8, "chunk_delay_ms": 0},
        }
    )
    with httpx.Client(base_url=served_square.base_url, timeout=2.0) as client:
        begun = time.monotonic()
        response = client.post("/oauth2/token", json=_token_body(served_square.seed))
        elapsed_s = time.monotonic() - begun
    assert response.status_code == 200
    assert response.json()["access_token"]
    # A token response splits into many more than one 8-byte chunk, so the
    # buggy 100ms-per-gap default this pins would take several seconds; a
    # genuine zero gap completes in well under a second even on a slow CI box.
    assert elapsed_s < 1.0, f"a zero chunk_delay_ms took {elapsed_s:.2f}s served -- the 100ms default leaked through"


def test_slow_body_delivers_the_full_body_to_a_patient_client(served_square: Driver) -> None:
    served_square.add_chaos_rule(
        {
            "id": "slow",
            "scope": "request",
            "fault": "slow_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"chunk_bytes": 16, "chunk_delay_ms": 20},
        }
    )
    with httpx.Client(base_url=served_square.base_url, timeout=10.0) as client:
        response = client.post("/oauth2/token", json=_token_body(served_square.seed))
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.headers["vendorfake-fault"] == "slow_body"


def test_slow_body_does_not_block_the_server_for_everyone_else(served_square: Driver) -> None:
    """Real ``asyncio.sleep`` between chunks, not a blocking wait: an
    unrelated request lands promptly while a slow one is still streaming."""
    served_square.add_chaos_rule(
        {
            "id": "slow",
            "scope": "request",
            "fault": "slow_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"chunk_bytes": 16, "chunk_delay_ms": 200},
        }
    )
    with (
        httpx.Client(base_url=served_square.base_url, timeout=10.0) as slow,
        httpx.Client(base_url=served_square.base_url, timeout=5.0) as quick,
    ):
        result: dict[str, httpx.Response] = {}

        def _drive_slow() -> None:
            result["slow"] = slow.post("/oauth2/token", json=_token_body(served_square.seed))

        thread = threading.Thread(target=_drive_slow)
        thread.start()
        time.sleep(0.1)  # let the slow request start streaming
        begun = time.monotonic()
        health = quick.get("/__unit/health")
        elapsed_s = time.monotonic() - begun
        thread.join(timeout=10.0)

    assert health.status_code == 200
    assert elapsed_s < 1.0, f"an unrelated request waited {elapsed_s:.2f}s behind a streaming one"
    assert result["slow"].status_code == 200
