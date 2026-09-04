"""One ``timeout`` rule means one thing on both clocks (konyklabs/roadmap#101,
item 18).

Before: ``delay_ms: 120000`` against a 10 s client raised ``ReadTimeout`` on a
real clock and answered a 504 on a virtual one, so a consumer's "network
error" assertion failed naming an HTTP status the moment their expiry tests
(which want a virtual clock) shared a module with their timeout tests. Now
the answer carries the delay the rule asked for and the in-process transport
races that against the read timeout on either clock.
"""

from __future__ import annotations

import time

import httpx
import pytest

from vendorfake.core.kernel.unit import DELAY_ASKED_HEADER
from vendorfake.testing import async_unit, unit

RULE = {
    "id": "stall",
    "scope": "request",
    "fault": "timeout",
    "match": {"route": "GET /v2/locations"},
    "params": {"delay_ms": 120000},
}
VIRTUAL = {"VENDORFAKE_CLOCK": "virtual"}


def test_past_the_read_timeout_a_virtual_clock_raises_read_timeout_without_waiting() -> None:
    with unit("square", env=VIRTUAL) as started:
        started.add_chaos_rule(RULE)
        begun = time.monotonic()
        with pytest.raises(httpx.ReadTimeout):
            started.client.get("/v2/locations", headers=started.seed.auth, timeout=10.0)
        elapsed_ms = (time.monotonic() - begun) * 1000
        # The request was still received and logged as the fault it was.
        (row,) = started.requests(route="GET /v2/locations")
    assert row["fault"] == "timeout"
    assert elapsed_ms < 500, f"waited {elapsed_ms:.1f}ms"


@pytest.mark.anyio
async def test_the_async_client_agrees() -> None:
    async with async_unit("square", env=VIRTUAL) as started:
        started.add_chaos_rule(RULE)
        with pytest.raises(httpx.ReadTimeout):
            await started.async_client.get("/v2/locations", headers=started.seed.auth, timeout=10.0)


def test_under_the_read_timeout_a_virtual_clock_still_answers_at_once() -> None:
    with unit("square", env=VIRTUAL) as started:
        started.add_chaos_rule(RULE)
        before = started.client.get("/__unit/info").json()["clock"]["now"]
        begun = time.monotonic()
        answered = started.client.get("/v2/locations", headers=started.seed.auth, timeout=200.0)
        elapsed_ms = (time.monotonic() - begun) * 1000
        after = started.client.get("/__unit/info").json()["clock"]["now"]
    assert answered.status_code == 504
    assert float(answered.headers[DELAY_ASKED_HEADER]) == 120000
    assert before != after, "scenario time moved by the delay"
    assert elapsed_ms < 500, f"waited {elapsed_ms:.1f}ms on a virtual clock"


def test_a_real_clock_publishes_the_same_header() -> None:
    with unit("square") as started:
        started.add_chaos_rule({**RULE, "params": {"delay_ms": 20}})
        answered = started.client.get("/v2/locations", headers=started.seed.auth, timeout=5.0)
    assert answered.status_code == 504
    assert float(answered.headers[DELAY_ASKED_HEADER]) == 20


def test_an_answer_without_the_fault_carries_no_such_header() -> None:
    with unit("square") as started:
        answered = started.client.get("/v2/locations", headers=started.seed.auth)
    assert answered.status_code == 200
    assert DELAY_ASKED_HEADER not in answered.headers
