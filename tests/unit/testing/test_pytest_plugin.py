"""The ``vendorfake`` pytest plugin (``src/vendorfake/pytest.py``): registered
once, inert until asked, and no longer the reason ``--conformance-*`` shows up
in a consumer's ``pytest --help`` (konyklabs/roadmap#71, D3).

Every check here drives a REAL pytest subprocess against an isolated project
``pytester`` builds, because the promise this file pins is what a downstream
consumer's own pytest run sees -- a test that imports the plugin module and
never asks pytest to load it proves nothing about the plugin pytest actually
loads through the installed ``pytest11`` entry point.
"""

from __future__ import annotations

import pytest

_NO_MARKER_SUITE = """
def test_needs_nothing_from_vendorfake():
    assert 1 + 1 == 2
"""

_MARKER_SUITE = """
import pytest

@pytest.mark.vendorfake("{vendor}")
def test_a_unit_answers_health(vendorfake_unit):
    assert vendorfake_unit.vendor == "{vendor}"
    assert vendorfake_unit.health()["status"] == "ok"
"""

_MISSING_MARKER_SUITE = """
def test_forgot_the_marker(vendorfake_unit):
    assert vendorfake_unit is not None
"""

_RECEIVER_SUITE = """
def test_the_receiver_fixture_starts_and_stops(vendorfake_webhook_receiver):
    assert vendorfake_webhook_receiver.url.startswith("http://")
    assert vendorfake_webhook_receiver.received == []
"""


def test_a_marker_less_suite_runs_identically_loaded_and_disabled(pytester: pytest.Pytester) -> None:
    """Inertness, proven both ways: the plugin loaded changes nothing for a
    suite that never mentions it, and disabling it explicitly changes nothing
    either -- the two runs are the same suite."""
    pytester.makepyfile(test_plain=_NO_MARKER_SUITE)

    loaded = pytester.runpytest_subprocess()
    loaded.assert_outcomes(passed=1)

    disabled = pytester.runpytest_subprocess("-p", "no:vendorfake")
    disabled.assert_outcomes(passed=1)


def test_conformance_options_are_gone_from_help_and_restored_explicitly(pytester: pytest.Pytester) -> None:
    """Since D3, ``vendorfake_conformance`` is no longer a ``pytest11`` entry
    point -- installing the wheel no longer hands every consumer suite five
    ``--conformance-*`` options and a session hook it never asked for."""
    pytester.makepyfile(test_plain=_NO_MARKER_SUITE)

    bare = pytester.runpytest_subprocess("--help")
    assert "--conformance-target" not in bare.stdout.str()
    assert "--conformance-strict" not in bare.stdout.str()

    restored = pytester.runpytest_subprocess("--help", "-p", "vendorfake.conformance.plugin")
    assert "--conformance-target" in restored.stdout.str()
    assert "--conformance-strict" in restored.stdout.str()


@pytest.mark.parametrize("vendor", ["square", "clover", "toast"])
def test_the_marker_builds_a_started_unit_for_each_vendor(pytester: pytest.Pytester, vendor: str) -> None:
    pytester.makepyfile(test_marked=_MARKER_SUITE.format(vendor=vendor))
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_without_the_marker_the_fixture_error_names_the_fix(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_unmarked=_MISSING_MARKER_SUITE)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(errors=1)
    assert "@pytest.mark.vendorfake" in result.stdout.str()


def test_the_webhook_receiver_fixture_is_usable_standalone(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_receiver=_RECEIVER_SUITE)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
