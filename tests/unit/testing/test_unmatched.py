"""Strict mode: a request no route matched, on every vendor and every binding.

The behaviour this pins is a *change* from v0.1, so it is tested from the
consumer's side rather than the kernel's: what a mis-spelled path does in
process, what it does when the consumer has asked for the vendor's own 404
instead, and what it does over a socket -- where raising is not an option and
the diagnosis has to travel as a header.

The auth path is the probe on every vendor because it is the one route every
profile has, needs no credential, and is the first thing a consumer types.
"""

from __future__ import annotations

import json

import pytest

from vendorfake import available_vendors
from vendorfake.core.kernel.nearmiss import NEAR_MISS_HEADER
from vendorfake.testing import UnmatchedRequest, serve_in_thread, unit
from vendorfake.testing.transport import DEFAULT_INPROCESS_POLICY, UnitTransport

#: For each vendor: a path one character away from its real auth route, and the
#: operation the unit should name as the closest thing to it.
MISSPELLED_AUTH: dict[str, tuple[str, str]] = {
    "square": ("/oauth2/tokens", "ObtainToken"),
    "clover": ("/oauth/v2/tokens", "ExchangeToken"),
    "toast": ("/authentication/v1/authentication/logins", "Login"),
    "lightspeed": ("/api/1.0/tokens", "TokenExchange"),
}


def _real_auth_route(vendor: str) -> str:
    """The route the unit publishes for the operation we expect it to name.

    Read from ``/__unit/routes`` rather than written down, so this test says
    "the closest route is the real auth route" rather than repeating a path
    that a vendor could move underneath it.
    """
    with unit(vendor) as driver:
        wanted = MISSPELLED_AUTH[vendor][1]
        rows = driver.client.get("/__unit/routes").json()["routes"]
        return next(f"{row['method']} {row['path']}" for row in rows if row.get("operation_id") == wanted)


def test_every_vendor_is_covered_by_this_test() -> None:
    """A vendor added without a row here would be silently untested."""
    assert sorted(MISSPELLED_AUTH) == sorted(available_vendors())


@pytest.mark.parametrize("vendor", sorted(MISSPELLED_AUTH))
def test_a_misspelled_auth_path_fails_the_test_that_sent_it(vendor: str) -> None:
    """In process the unit is a test double, and a double that answers 404 to a
    path nobody serves lets a mis-targeted test pass against a unit it never
    reached. The message has to carry the diagnosis, because a pytest traceback
    is all the reader gets."""
    path, operation = MISSPELLED_AUTH[vendor]
    with unit(vendor) as driver, pytest.raises(UnmatchedRequest) as raised:
        driver.client.post(path, json={})
    message = str(raised.value)
    assert f"no route matched POST {path} on {vendor}" in message
    assert _real_auth_route(vendor) in message.splitlines()[2]
    assert operation in message
    assert 'unmatched="vendor-404"' in message


@pytest.mark.parametrize("vendor", sorted(MISSPELLED_AUTH))
def test_the_same_request_answers_the_vendors_own_404_when_asked(vendor: str) -> None:
    """The opt-out, for a consumer rehearsing what their code does with a real
    404. The body is the vendor's, untouched: fidelity is the reason the
    diagnosis is in a header and not in the document."""
    path, operation = MISSPELLED_AUTH[vendor]
    with unit(vendor, unmatched="vendor-404") as driver:
        answered = driver.client.post(path, json={})
        assert answered.status_code == 404
        assert answered.headers["x-unit-error"] == "not_found"
        misses = json.loads(answered.headers[NEAR_MISS_HEADER])
        assert misses[0]["operation_id"] == operation
        assert misses[0]["route"] == _real_auth_route(vendor)
        assert len(misses) == 3


@pytest.mark.parametrize("vendor", sorted(MISSPELLED_AUTH))
def test_a_served_unit_answers_and_never_raises(vendor: str) -> None:
    """Over a socket there is no caller stack to raise into, and the unit is
    standing in for the vendor rather than acting as a double. The header
    survives the ASGI adapter, which is the only way the diagnosis reaches a
    consumer in another language."""
    path, operation = MISSPELLED_AUTH[vendor]
    with unit(vendor) as started, serve_in_thread(started) as driver:
        answered = driver.client.post(path, json={})
        assert answered.status_code == 404
        misses = json.loads(answered.headers[NEAR_MISS_HEADER])
        assert misses[0]["operation_id"] == operation


def test_a_real_404_from_a_real_route_is_not_a_failure() -> None:
    """The header is the signal, not the status: a vendor's own 404 for an id
    that does not exist is a real answer from a route that matched, and a
    consumer testing their not-found handling must still be able to get one."""
    with unit("square") as square:
        answered = square.client.get("/v2/orders/nope-not-an-order", headers=square.seed.auth)
        assert answered.status_code == 404
        assert NEAR_MISS_HEADER not in answered.headers


def test_the_405_for_a_wrong_verb_is_not_a_failure_either() -> None:
    """The path exists. A 405 already names the methods that are allowed, so
    there is nothing a near-miss list would add and nothing to raise about."""
    with unit("square") as square:
        answered = square.client.delete("/v2/locations", headers=square.seed.auth)
        assert answered.status_code == 405
        assert NEAR_MISS_HEADER not in answered.headers


# ---------------------------------------------------------------------------
# where the policy comes from
# ---------------------------------------------------------------------------


def test_the_in_process_default_is_strict() -> None:
    with unit("square") as square:
        assert DEFAULT_INPROCESS_POLICY == "error"
        assert UnitTransport(square.unit).unmatched == "error"


def test_the_environment_sets_the_policy_for_a_whole_suite() -> None:
    """`VENDORFAKE_UNMATCHED` is how a consumer with a suite full of
    deliberate probes opts out once rather than per call."""
    with unit("square", env={"VENDORFAKE_UNMATCHED": "vendor-404"}) as square:
        assert square.client.post("/oauth2/tokens", json={}).status_code == 404


def test_the_argument_beats_the_environment() -> None:
    """Precedence, stated the way every other layer in this project states it:
    the caller is last and loudest."""
    with (
        unit("square", env={"VENDORFAKE_UNMATCHED": "vendor-404"}, unmatched="error") as square,
        pytest.raises(UnmatchedRequest),
    ):
        square.client.post("/oauth2/tokens", json={})


def test_a_policy_that_is_not_one_of_the_two_is_refused_at_startup() -> None:
    """A typo'd variable falling back to the default would leave a CI run
    configured to fail loudly still answering 404s."""
    with pytest.raises(Exception, match="VENDORFAKE_UNMATCHED"), unit("square", env={"VENDORFAKE_UNMATCHED": "err"}):
        pass


# -- konyklabs/roadmap#99, item 1: the argument is validated, not stored ------


@pytest.mark.parametrize("bad", ["raise", True, "ERROR", 0])
def test_unit_refuses_an_unmatched_value_that_is_not_a_policy(bad: object) -> None:
    """Before, any value here silently meant ``vendor-404`` -- strict mode
    *off* while the caller believed they had turned it on."""
    from vendorfake.testing import async_unit, unit

    with pytest.raises(ValueError) as caught:
        unit("square", unmatched=bad)  # type: ignore[call-overload]
    assert repr(bad) in str(caught.value)
    assert "'vendor-404'" in str(caught.value) and "'error'" in str(caught.value)
    with pytest.raises(ValueError):
        async_unit("square", unmatched=bad)  # type: ignore[call-overload]


def test_checked_unmatched_passes_the_two_policies_and_none() -> None:
    from vendorfake.testing import checked_unmatched

    assert checked_unmatched(None) is None
    assert checked_unmatched("error") == "error"
    assert checked_unmatched("vendor-404") == "vendor-404"


def test_the_transport_constructor_refuses_the_same_values() -> None:
    """The publicly exported constructor stores the value; it must not be the
    one door the check misses."""
    from vendorfake.testing import UnitTransport, unit

    with unit("square") as started, pytest.raises(ValueError, match="unmatched=True"):
        UnitTransport(started.unit, unmatched=True)  # type: ignore[arg-type]
