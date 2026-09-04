"""The documented fixed-window limiter: the quota, the window, the headers, the 429.

Everything asserted here is on
https://x-series-api.lightspeedhq.com/docs/rate_limiting except where a test
says otherwise. The limiter is vendor behaviour rather than chaos, so it runs
on every profile and no capability switches it off; the quota is a config knob
instead.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.lightspeed.conftest import CLOCK_START, fake_ctx
from tests.unit.lightspeed.harness import Harness, harness
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.time.clock import Clock
from vendorfake.lightspeed.config import LightspeedConfig
from vendorfake.lightspeed.errors import (
    RATE_LIMIT_LIMIT_HEADER,
    RATE_LIMIT_REMAINING_HEADER,
    RATE_LIMITED_MESSAGE,
    RATE_LIMITED_TITLE,
    RETRY_AFTER_HEADER,
)
from vendorfake.lightspeed.ratelimit import LightspeedRateLimiter

WINDOW_MS = 300_000


@pytest.fixture
def tight() -> Iterator[Harness]:
    """A unit whose quota is three requests, so a test can spend it.

    The formula is read from the config, so narrowing the two numbers is the
    supported way to make the documented limiter reachable in a test -- there
    is no switch, because a real retailer has none either.
    """
    yield from harness(
        env={
            "VENDORFAKE_CLOCK": "virtual",
            "VENDORFAKE_CLOCK_START": CLOCK_START,
            "VENDORFAKE_VENDOR_RATE_LIMIT_PER_REGISTER": "1",
            "VENDORFAKE_VENDOR_RATE_LIMIT_BASE": "1",
        }
    )


# -- the formula and the window ---------------------------------------------


def test_the_quota_is_the_documented_formula() -> None:
    """'300 x <number of registers> + 50'."""
    config = LightspeedConfig()
    assert config.rate_limit_quota(1) == 350
    assert config.rate_limit_quota(2) == 650
    assert config.rate_limit_quota(0) == 50


def test_the_shipped_scenario_sizes_the_quota_from_its_two_registers(h: Harness) -> None:
    answered = h.get(h.path("/retailer"))
    assert answered.headers[RATE_LIMIT_LIMIT_HEADER] == "650"


def test_the_window_is_the_documented_five_minutes() -> None:
    assert LightspeedConfig().rate_limit_window_ms == WINDOW_MS == 5 * 60 * 1000


def test_the_window_is_fixed_and_rolls_on_the_clock() -> None:
    """Not a leaky bucket -- that mechanism belongs to the separate R-Series
    product line. Crossing the boundary restores the whole quota at once,
    which is what "fixed window" means."""
    clock = Clock("virtual", CLOCK_START)
    ctx = fake_ctx(clock=clock)
    limiter = LightspeedRateLimiter(limit=2, window_ms=WINDOW_MS)
    limiter.consume(ctx)
    limiter.consume(ctx)
    with pytest.raises(UnitError) as caught:
        limiter.consume(ctx)
    assert caught.value.kind is UnitErrorKind.RATE_LIMITED

    clock.advance(WINDOW_MS)
    limiter.consume(ctx)
    assert limiter.snapshot(ctx).remaining == 1


def test_a_refused_request_still_counts() -> None:
    """The real limiter counts requests, not answers: a caller hammering a
    spent window does not get their quota back by being refused."""
    ctx = fake_ctx()
    limiter = LightspeedRateLimiter(limit=1, window_ms=WINDOW_MS)
    limiter.consume(ctx)
    for _ in range(3):
        with pytest.raises(UnitError):
            limiter.consume(ctx)
    assert limiter.snapshot(ctx).remaining == 0


# -- the headers -------------------------------------------------------------


def test_both_headers_are_on_every_response(h: Harness) -> None:
    """DOCUMENTED: the two headers are present on EVERY response -- so on a
    success, on a shaped refusal, and on the 429 itself."""
    for answered in (
        h.get(h.path("/retailer")),
        h.get(h.path("/outlets/nope")),
        h.get(h.path("/webhooks"), headers=h.read_auth),
    ):
        assert RATE_LIMIT_LIMIT_HEADER in answered.headers
        assert RATE_LIMIT_REMAINING_HEADER in answered.headers


def test_remaining_counts_down(h: Harness) -> None:
    first = int(h.get(h.path("/retailer")).headers[RATE_LIMIT_REMAINING_HEADER])
    second = int(h.get(h.path("/retailer")).headers[RATE_LIMIT_REMAINING_HEADER])
    assert second == first - 1


def test_the_control_plane_is_not_counted(h: Harness) -> None:
    """``/__unit/*`` is this project's own side channel; no vendor's documented
    quota covers it, and a check that polled the journal would otherwise spend
    a consumer's requests."""
    before = int(h.get(h.path("/retailer")).headers[RATE_LIMIT_REMAINING_HEADER])
    for _ in range(5):
        h.api.get("/__unit/state")
    after = int(h.get(h.path("/retailer")).headers[RATE_LIMIT_REMAINING_HEADER])
    assert after == before - 1


# -- the refusal -------------------------------------------------------------


def test_spending_the_quota_answers_the_documented_429(tight: Harness) -> None:
    assert tight.get(tight.path("/retailer")).status == 200
    assert tight.get(tight.path("/retailer")).status == 200
    assert tight.get(tight.path("/retailer")).status == 200
    answered = tight.get(tight.path("/retailer"))
    assert answered.status == 429
    body = answered.json()
    assert body["error"] == RATE_LIMITED_TITLE
    assert body["message"] == RATE_LIMITED_MESSAGE


def test_the_429_carries_an_http_date_retry_after(tight: Harness) -> None:
    """DOCUMENTED as an RFC 1123 date, not delta-seconds. The instant is the
    end of the window the request fell in."""
    for _ in range(4):
        answered = tight.get(tight.path("/retailer"))
    assert answered.status == 429
    assert answered.headers[RETRY_AFTER_HEADER] == "Fri, 04 Sep 2026 12:05:00 GMT"


def test_the_429_still_carries_the_two_headers(tight: Harness) -> None:
    for _ in range(4):
        answered = tight.get(tight.path("/retailer"))
    assert answered.status == 429
    assert answered.headers[RATE_LIMIT_LIMIT_HEADER] == "3"
    assert answered.headers[RATE_LIMIT_REMAINING_HEADER] == "0"


def test_the_unauthenticated_routes_count_too(tight: Harness) -> None:
    """The quota is per retailer per application and counts requests, so the
    token endpoint and the authorize stand-in spend it like anything else."""
    for _ in range(3):
        tight.api.get("/connect", query={"client_id": "unit-lightspeed-client-id"})
    answered = tight.api.get("/connect", query={"client_id": "unit-lightspeed-client-id"})
    assert answered.status == 429


def test_the_window_reopens_on_the_clock(tight: Harness) -> None:
    for _ in range(4):
        tight.get(tight.path("/retailer"))
    tight.unit.context.clock.advance(WINDOW_MS)
    assert tight.get(tight.path("/retailer")).status == 200


def test_the_limiter_runs_on_the_no_chaos_profile_too() -> None:
    """It is vendor behaviour, not fault injection: no profile switches it
    off, because no Lightspeed retailer can either."""
    gen = harness("no-faults")
    started = next(gen)
    try:
        assert RATE_LIMIT_LIMIT_HEADER in started.get(started.path("/retailer")).headers
    finally:
        gen.close()
