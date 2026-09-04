"""``Driver.requests`` and ``assert_called``, from the consumer's side.

What matters here is not that the filters work -- ``tests/unit/core`` pins the
ring and the routes -- but that the *assertion* is usable: that a failure names
what was called instead of a number, and that the count is over the whole log
rather than over whichever page the driver happened to fetch.
"""

from __future__ import annotations

import pytest

from vendorfake.testing import unit


def _obtain_a_token(square: object) -> None:
    """One real call to Square's token endpoint, through the seeded app.

    A refresh rather than an authorization-code exchange, because a code is
    single-use and this test needs to make the same call twice.
    """
    driver = square
    seed = driver.seed  # type: ignore[attr-defined]
    answered = driver.client.post(  # type: ignore[attr-defined]
        "/oauth2/token",
        json={
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "refresh_token",
            "refresh_token": seed.refresh_token,
        },
    )
    assert answered.status_code == 200, answered.text


def test_requests_reports_what_was_called_newest_first() -> None:
    with unit("square") as square:
        square.client.get("/v2/locations", headers=square.seed.auth)
        _obtain_a_token(square)
        rows = square.requests()
        assert [row["operation_id"] for row in rows] == ["ObtainToken", "ListLocations"]
        assert all(row["matched"] for row in rows)


def test_requests_filters_by_operation_route_and_match() -> None:
    with unit("square", unmatched="vendor-404") as square:
        square.client.get("/v2/locations", headers=square.seed.auth)
        square.client.get("/v2/locationz")
        assert [row["operation_id"] for row in square.requests(operation_id="ListLocations")] == ["ListLocations"]
        assert [row["route"] for row in square.requests(route="GET /v2/locations")] == ["GET /v2/locations"]
        assert [row["path"] for row in square.requests(unmatched=True)] == ["/v2/locationz"]
        assert [row["path"] for row in square.requests(unmatched=False)] == ["/v2/locations"]
        assert len(square.requests(limit=1)) == 1


def test_the_control_plane_calls_this_test_makes_are_not_in_the_answer() -> None:
    """Reading the log must not be visible in the log, or a consumer polling it
    would see their own polling rather than their own traffic."""
    with unit("square") as square:
        square.client.get("/v2/locations", headers=square.seed.auth)
        square.requests()
        square.health()
        assert [row["path"] for row in square.requests()] == ["/v2/locations"]


def test_assert_called_counts_and_returns_the_matching_records() -> None:
    with unit("square") as square:
        _obtain_a_token(square)
        _obtain_a_token(square)
        found = square.assert_called("ObtainToken", times=2)
        assert [row["status"] for row in found] == [200, 200]
        square.assert_called("ObtainToken")
        square.assert_called("ObtainToken", at_least=1)


def test_assert_called_fails_naming_every_operation_that_was_called() -> None:
    """A bare 'expected 2, got 1' sends the reader to the log by hand. The
    usual causes -- a typo'd path, a capability off, a call that never left the
    code under test -- are all visible in the list this prints instead."""
    with unit("square") as square:
        _obtain_a_token(square)
        square.client.get("/v2/locations", headers=square.seed.auth)
        with pytest.raises(AssertionError) as raised:
            square.assert_called("ObtainToken", times=2)
        message = str(raised.value)
        assert "expected exactly 2 call(s) to 'ObtainToken' on square (profile 'full'), saw 1" in message
        assert "1  ObtainToken" in message
        assert "1  ListLocations" in message


def test_assert_called_names_the_unmatched_calls_too() -> None:
    """The commonest reason an operation was not called is that the request
    went somewhere else entirely, so the list has to show the misses."""
    with unit("square", unmatched="vendor-404") as square:
        square.client.get("/v2/locationz")
        with pytest.raises(AssertionError) as raised:
            square.assert_called("ListLocations")
        assert "GET /v2/locationz (no route matched)" in str(raised.value)


def test_assert_called_says_so_when_nothing_was_called_at_all() -> None:
    """An empty list with no explanation reads as a broken assertion; the
    likeliest cause is a reset between the call and the check."""
    with unit("square") as square, pytest.raises(AssertionError, match="Nothing was called at all"):
        square.assert_called("ObtainToken")


def test_times_and_at_least_together_are_a_programming_error() -> None:
    """One of the two is always redundant, and guessing which would make the
    assertion mean different things to its reader and its runner."""
    with unit("square") as square, pytest.raises(ValueError, match="not both"):
        square.assert_called("ObtainToken", times=1, at_least=1)


def test_clear_requests_forgets_the_calls_and_keeps_the_state() -> None:
    """The line a consumer draws under their setup, so that what follows
    counts only what the part under test did."""
    with unit("square") as square:
        square.client.get("/v2/locations", headers=square.seed.auth)
        digest = square.info()["state"]["digest"]
        assert square.clear_requests() == 1
        assert square.requests() == []
        assert square.info()["state"]["digest"] == digest


def test_a_reset_clears_the_log_as_well_as_the_state() -> None:
    with unit("square") as square:
        square.client.get("/v2/locations", headers=square.seed.auth)
        square.reset()
        assert square.requests() == []


def test_a_served_unit_reports_the_same_log_over_http() -> None:
    """The whole point of putting it on the control plane: a consumer whose
    service runs in another process reads it the same way."""
    from vendorfake.testing import serve_in_thread

    with unit("square") as started, serve_in_thread(started) as served_driver:
        served_driver.client.get("/v2/locations", headers=started.seed.auth)
        served_driver.assert_called("ListLocations", times=1)
        # One unit, two bindings: the in-process driver sees the served call.
        assert [row["path"] for row in started.requests()] == ["/v2/locations"]


def test_assert_called_refuses_to_answer_when_the_log_is_switched_off() -> None:
    """`saw 0` would be a passing assert_called(times=0) and a failing
    everything else, about a unit that was never recording."""
    with unit("square", env={"VENDORFAKE_REQUEST_LOG_CAPACITY": "0"}) as square:
        square.client.get("/v2/locations", headers=square.seed.auth)
        assert square.requests() == []
        with pytest.raises(AssertionError, match="request log is switched off"):
            square.assert_called("ListLocations")


def test_the_capacity_can_be_raised_or_lowered_from_the_environment() -> None:
    with unit("square", env={"VENDORFAKE_REQUEST_LOG_CAPACITY": "1"}) as square:
        square.client.get("/v2/locations", headers=square.seed.auth)
        square.client.get("/v2/merchants", headers=square.seed.auth)
        assert [row["operation_id"] for row in square.requests()] == ["ListMerchants"]
