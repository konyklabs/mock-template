"""The tripwire, wired at both ends and shown to move.

FOR: making ``framework_answered`` a measurement. Two contracts assert that the
number is zero; until the harness was wired, the number *was* zero -- the
literal, returned by ``core/control/plane.py`` because no counter had been
handed to it -- and both assertions were tautologies.

The measurement that established this is reproduced here rather than described:
dropping ``PATCH`` from ``asgi/app.py``'s ``HTTP_METHODS`` made Starlette
answer a request the unit never saw, the tripwire counted it, and C04 printed
"framework_answered still 0" and passed. What follows drives the same hole
without editing anything, using the one verb the catch-all genuinely does not
claim, and asserts that the number the *unit* reports moves.
"""

from __future__ import annotations

import pytest

from tests.conformance.harness import PROFILES, open_client, target
from vendorfake.asgi import HTTP_METHODS
from vendorfake.conformance import ConformanceTarget, Outcome, find_check, run_check

CONTROL_HEALTH = "/__unit/health"

UNCLAIMED_METHOD = "PROPFIND"
"""A verb outside :data:`HTTP_METHODS`, and the only kind of request a web
framework can still answer first. The catch-all claims every ordinary verb, so
this is the sole remaining hole -- which makes it exactly the right probe: it
exercises the tripwire without pretending the catch-all is broken."""


def test_the_probe_verb_really_is_outside_the_catch_all() -> None:
    """Otherwise the test below would prove nothing about the tripwire."""
    assert UNCLAIMED_METHOD not in HTTP_METHODS, (
        f"{UNCLAIMED_METHOD} is now registered on the catch-all, so it can no longer make the "
        f"framework answer and this file needs a different probe verb."
    )


@pytest.mark.integration
def test_the_unit_reports_a_request_the_framework_answered() -> None:
    """The wiring, end to end: framework answers, unit reports it.

    Over the HTTP binding, because the tripwire only means anything there --
    in process there is no framework that *could* answer, and 0 is then the
    true answer rather than a stub.
    """
    with open_client("full", "http") as client:
        before = client.call("GET", CONTROL_HEALTH).json()["framework_answered"]
        assert before == 0, f"a freshly started unit reports framework_answered={before!r}, expected 0"

        client.call(UNCLAIMED_METHOD, "/v2/locations")

        after = client.call("GET", CONTROL_HEALTH).json()["framework_answered"]
    assert after == 1, (
        f"a {UNCLAIMED_METHOD} request was answered by the web framework and the unit still reports "
        f"framework_answered={after!r}. The counter the application increments and the counter the "
        f"control plane reports are not the same object: tests/conformance/harness.py must build "
        f"the unit with framework_answered=tripwire.get and pass that same tripwire to create_app. "
        f"Unwired, the number is the literal 0 and C01's and C04's assertions on it cannot fail."
    )


@pytest.mark.integration
def test_the_in_process_binding_reports_zero_because_there_is_no_framework() -> None:
    """The other half of "it is a measurement": 0 where 0 is the truth.

    A harness that satisfied the contracts by hardwiring 1 would be no better
    than one that hardwired 0.
    """
    with open_client("full", "inprocess") as client:
        client.call(UNCLAIMED_METHOD, "/v2/locations")
        reported = client.call("GET", CONTROL_HEALTH).json()["framework_answered"]
    assert reported == 0, (
        f"the in-process binding reports framework_answered={reported!r}. There is no web framework "
        f"in that path, so nothing can have answered above the unit."
    )


@pytest.mark.integration
@pytest.mark.parametrize("check_id", ["C01", "C04"])
def test_the_contracts_that_read_the_tripwire_pass_over_the_wired_binding(check_id: str) -> None:
    """Both contracts, asked over HTTP where the number is real.

    In process they read a genuine zero; over HTTP they read a counter that
    this file has just shown can move. Running them here is what makes their
    green a fact about the binding rather than about the default.
    """
    built: ConformanceTarget = target(profiles=PROFILES)
    result = run_check(find_check(check_id), built, "full", "http")
    assert result.outcome is Outcome.PASS, f"{result.case_id}: {result.detail}"
