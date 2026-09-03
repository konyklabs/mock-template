"""The three directive faults against the in-process transport, sync and
async, plus the two cross-cutting invariants Definition-of-done items 5 and 6
ask for: provenance surfaces where a consumer would read it, and the fault
mechanism itself never journals anything.

``connection_reset`` and ``empty_response`` have no socket to reset or starve
here, so :mod:`vendorfake.testing.transport` raises the exception a real one
would surface, directly. ``slow_body`` folds into the same read-timeout race
as the ``timeout`` fault's ``delay_ms`` (``tests/unit/test_async_seam.py``),
but raced against a single chunk gap rather than their sum -- a real read
timeout is inactivity-based per chunk, so many small gaps never trip it no
matter how long they add up to. The served (real-socket) behaviour of all
three is ``tests/integration/test_transport_faults_served.py``.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import httpx
import pytest

from vendorfake.testing import unit

RESET_RULE = {
    "id": "reset",
    "scope": "request",
    "fault": "connection_reset",
    "match": {"route": "POST /oauth2/token"},
}
EMPTY_RULE = {
    "id": "empty",
    "scope": "request",
    "fault": "empty_response",
    "match": {"route": "POST /oauth2/token"},
}


def _token_body(seed: Any) -> dict[str, str]:
    return {
        "client_id": seed.application_id,
        "client_secret": seed.application_secret,
        "grant_type": "refresh_token",
        "refresh_token": seed.refresh_token,
    }


# ---------------------------------------------------------------------------
# connection_reset / empty_response, in process.
# ---------------------------------------------------------------------------


def test_connection_reset_raises_remote_protocol_error_sync() -> None:
    with unit("square") as started:
        started.add_chaos_rule(RESET_RULE)
        with pytest.raises(httpx.RemoteProtocolError):
            started.client.post("/oauth2/token", json=_token_body(started.seed))


@pytest.mark.anyio
async def test_connection_reset_raises_remote_protocol_error_async() -> None:
    with unit("square") as started:
        started.add_chaos_rule(RESET_RULE)
        with pytest.raises(httpx.RemoteProtocolError):
            await started.async_client.post("/oauth2/token", json=_token_body(started.seed))


def test_empty_response_raises_read_error_sync() -> None:
    with unit("square") as started:
        started.add_chaos_rule(EMPTY_RULE)
        with pytest.raises(httpx.ReadError):
            started.client.post("/oauth2/token", json=_token_body(started.seed))


@pytest.mark.anyio
async def test_empty_response_raises_read_error_async() -> None:
    with unit("square") as started:
        started.add_chaos_rule(EMPTY_RULE)
        with pytest.raises(httpx.ReadError):
            await started.async_client.post("/oauth2/token", json=_token_body(started.seed))


def test_a_connection_fault_never_waits() -> None:
    """Nothing here holds a socket to reset or starve, so there is nothing to
    wait for -- unlike ``slow_body``, which trades a real wait for a real
    chunked stream only when a binding actually holds one (the ASGI binding)."""
    with unit("square") as started:
        started.add_chaos_rule(RESET_RULE)
        started_at = time.monotonic()
        with pytest.raises(httpx.RemoteProtocolError):
            started.client.post("/oauth2/token", json=_token_body(started.seed))
        assert (time.monotonic() - started_at) * 1000 < 100


# ---------------------------------------------------------------------------
# slow_body: the same read-timeout race as a `timeout` fault's `delay_ms`.
# ---------------------------------------------------------------------------


def _slow_rule(*, chunk_bytes: int = 8, chunk_delay_ms: int = 300) -> dict[str, object]:
    """Default ``chunk_delay_ms`` (300 ms) is deliberately a single gap larger
    than the 0.2 s read timeout the two tests below use -- not their sum,
    which many small gaps could exceed while none of them individually would.
    See ``testing/transport.py``'s ``_would_exhaust_read_timeout_ms``."""
    return {
        "id": "slow",
        "scope": "request",
        "fault": "slow_body",
        "match": {"route": "POST /oauth2/token"},
        "params": {"chunk_bytes": chunk_bytes, "chunk_delay_ms": chunk_delay_ms},
    }


def test_slow_body_past_the_read_timeout_raises_read_timeout_without_waiting_sync() -> None:
    with unit("square") as started:
        started.add_chaos_rule(_slow_rule())
        assert started._transport is not None
        with httpx.Client(transport=started._transport, base_url=started.base_url, timeout=0.2) as client:
            started_at = time.monotonic()
            with pytest.raises(httpx.ReadTimeout):
                client.post("/oauth2/token", json=_token_body(started.seed))
            elapsed_ms = (time.monotonic() - started_at) * 1000
            assert elapsed_ms < 100, f"the binding waited {elapsed_ms:.1f}ms; it should not have waited at all"


@pytest.mark.anyio
async def test_slow_body_past_the_read_timeout_raises_read_timeout_without_waiting_async() -> None:
    with unit("square") as started:
        started.add_chaos_rule(_slow_rule())
        assert started._transport is not None
        async with httpx.AsyncClient(transport=started._transport, base_url=started.base_url, timeout=0.2) as client:
            started_at = time.monotonic()
            with pytest.raises(httpx.ReadTimeout):
                await client.post("/oauth2/token", json=_token_body(started.seed))
            elapsed_ms = (time.monotonic() - started_at) * 1000
            assert elapsed_ms < 100, f"the binding waited {elapsed_ms:.1f}ms; it should not have waited at all"


def test_a_body_that_fits_in_one_chunk_has_no_gap_to_race_and_never_raises_sync() -> None:
    """A single chunk means a served unit writes the body in one go and a
    patient client never waits, however large ``chunk_delay_ms`` is. The
    in-process binding must agree, or a test green here fails against a
    served unit -- the parity break review round 2 of konyklabs/roadmap#73
    reproduced (ReadTimeout in 0.3 ms in process, 200 in 1.6 ms served)."""
    with unit("square") as started:
        started.add_chaos_rule(_slow_rule(chunk_bytes=100_000, chunk_delay_ms=5_000))
        started_at = time.monotonic()
        response = started.client.post("/oauth2/token", json=_token_body(started.seed), timeout=0.2)
        elapsed_ms = (time.monotonic() - started_at) * 1000
        assert response.status_code == 200
        assert response.headers["vendorfake-fault"] == "slow_body"
        assert elapsed_ms < 100, f"one chunk owes no wait, yet the binding held the answer {elapsed_ms:.1f}ms"


@pytest.mark.anyio
async def test_a_body_that_fits_in_one_chunk_has_no_gap_to_race_and_never_raises_async() -> None:
    with unit("square") as started:
        started.add_chaos_rule(_slow_rule(chunk_bytes=100_000, chunk_delay_ms=5_000))
        assert started._transport is not None
        async with httpx.AsyncClient(transport=started._transport, base_url=started.base_url, timeout=0.2) as client:
            response = await client.post("/oauth2/token", json=_token_body(started.seed))
            assert response.status_code == 200
            assert response.headers["vendorfake-fault"] == "slow_body"


def test_many_small_gaps_under_the_timeout_never_raise_even_though_their_sum_would() -> None:
    """The other half of the race: each gap individually fits inside the read
    timeout, so nothing raises, however long the aggregate comes to -- proved
    against a real socket in
    ``tests/integration/test_transport_faults_served.py``'s twin of this test.
    """
    with unit("square") as started:
        started.add_chaos_rule(_slow_rule(chunk_bytes=8, chunk_delay_ms=60))
        response = started.client.post("/oauth2/token", json=_token_body(started.seed), timeout=0.2)
        assert response.status_code == 200


def test_slow_body_under_a_long_timeout_delivers_the_full_body() -> None:
    with unit("square") as started:
        started.add_chaos_rule(_slow_rule(chunk_bytes=32, chunk_delay_ms=10))
        response = started.client.post("/oauth2/token", json=_token_body(started.seed), timeout=10.0)
        assert response.status_code == 200
        assert response.json()["access_token"]
        assert response.headers["vendorfake-fault"] == "slow_body"


@pytest.mark.anyio
async def test_slow_body_under_a_long_timeout_delivers_the_full_body_async() -> None:
    with unit("square") as started:
        started.add_chaos_rule(_slow_rule(chunk_bytes=32, chunk_delay_ms=10))
        response = await started.async_client.post("/oauth2/token", json=_token_body(started.seed), timeout=10.0)
        assert response.status_code == 200
        assert response.json()["access_token"]


# ---------------------------------------------------------------------------
# Definition of done #6: the fault mechanism itself never journals anything.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        {
            "id": "mutate",
            "scope": "request",
            "fault": "body_mutation",
            "match": {"route": "POST /oauth2/token"},
            "params": {"ops": [{"op": "remove", "pointer": "/access_token"}]},
        },
        {
            "id": "malform",
            "scope": "request",
            "fault": "malformed_body",
            "match": {"route": "POST /oauth2/token"},
            "params": {"mode": "empty"},
        },
        {"id": "reset", "scope": "request", "fault": "connection_reset", "match": {"route": "POST /oauth2/token"}},
        {"id": "slow", "scope": "request", "fault": "slow_body", "match": {"route": "POST /oauth2/token"}},
    ],
    ids=["body_mutation", "malformed_body", "connection_reset", "slow_body"],
)
def test_a_transport_fault_journals_exactly_what_an_unfaulted_call_would(rule: dict[str, object]) -> None:
    """The fault corrupts what the caller reads, never what the store records.

    Compared against a *second*, unfaulted unit rather than a before/after on
    the same one: ``ObtainToken`` mints a token and journals it regardless of
    what happens to the response afterwards, so "the journal did not move" is
    the wrong claim -- "it moved by exactly as much as it always does" is the
    one these faults actually make, since ``apply_response_fault`` runs after
    the handler and never receives the context it would need to touch a
    journal in the first place.
    """
    with unit("square") as baseline:
        before = baseline.unit.context.store.journal_seq
        baseline.client.post("/oauth2/token", json=_token_body(baseline.seed))
        baseline_delta = baseline.unit.context.store.journal_seq - before

    with unit("square") as started:
        started.add_chaos_rule(rule)
        before = started.unit.context.store.journal_seq
        # connection_reset/empty_response: the handler still ran, so the
        # journal already moved by the time the transport raises.
        with contextlib.suppress(httpx.TransportError):
            started.client.post("/oauth2/token", json=_token_body(started.seed))
        after = started.unit.context.store.journal_seq
        assert after - before == baseline_delta


# ---------------------------------------------------------------------------
# Definition of done #5: provenance surfaces at /__unit/chaos and in info().
# ---------------------------------------------------------------------------


def test_provenance_transport_appears_in_the_chaos_listing() -> None:
    with unit("square") as started:
        faults = {row["name"]: row["provenance"] for row in started.client.get("/__unit/chaos").json()["faults"]}
    for name in ("malformed_body", "body_mutation", "connection_reset", "empty_response", "slow_body"):
        assert faults[name] == "transport", name
    assert faults["rate_limit"] == "vendor"


def test_provenance_transport_appears_in_info_which_the_cli_prints_verbatim() -> None:
    """``vendorfake info`` prints ``GET /__unit/info`` unchanged
    (``cli.py:_info``), so asserting on the in-process response here is
    asserting on the CLI's output too."""
    with unit("square") as started:
        faults = {row["name"]: row["provenance"] for row in started.info()["chaos"]["faults"]}
    assert faults["slow_body"] == "transport"


# ---------------------------------------------------------------------------
# Definition of done #4: the two headers on pre-existing fault kinds too.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "params"),
    [
        ("rate_limit", {}),
        ("server_error", {}),
        ("unavailable", {}),
        ("timeout", {"delay_ms": 1}),
        ("token_expiry", {}),
    ],
)
def test_a_pre_existing_fault_is_also_stamped(fault: str, params: dict[str, object]) -> None:
    """These five raise a ``UnitError`` rather than going through
    ``apply_response_fault``; the headers arrive via ``UnitError.fault`` /
    ``.rule_id`` and ``kernel/unit.py``'s ``_shape`` instead. Covered here
    rather than in ``core/test_chaos_faults.py`` because it needs a full
    pipeline run to see the headers actually land on the wire.
    """
    with unit("square") as started:
        started.add_chaos_rule(
            {
                "id": f"stamped-{fault}",
                "scope": "request",
                "fault": fault,
                "match": {"route": "GET /v2/locations"},
                "params": params,
            }
        )
        response = started.client.get("/v2/locations", headers=started.seed.auth)  # type: ignore[union-attr]
        assert response.headers["vendorfake-fault"] == fault
        assert response.headers["vendorfake-rule"] == f"stamped-{fault}"
