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


_UNMATCHED_SUITE = """
import pytest

@pytest.mark.vendorfake("square")
def test_the_strict_default_raises(vendorfake_unit):
    from vendorfake.testing import UnmatchedRequest
    with pytest.raises(UnmatchedRequest):
        vendorfake_unit.client.get("/v2/nothing-here")


@pytest.mark.vendorfake("square", unmatched="vendor-404")
def test_the_opt_out_answers_instead(vendorfake_unit):
    assert vendorfake_unit.client.get("/v2/nothing-here").status_code == 404
"""


def test_the_marker_carries_the_unmatched_policy(pytester: pytest.Pytester) -> None:
    """The marker's keyword list is the plugin's whole surface onto
    :func:`vendorfake.testing.unit`, so a control that stream S added to that
    function and stream D's marker never learned about would be unreachable
    from the fixture -- a consumer wanting the v0.1 404 back would have had to
    abandon the fixture and call ``unit()`` by hand. Both halves are asserted
    in one subprocess: the strict default raises, and ``unmatched=`` reaches
    the unit that answers instead (konyklabs/roadmap#67).
    """
    pytester.makepyfile(test_unmatched=_UNMATCHED_SUITE)
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)


def test_the_marker_still_refuses_a_keyword_it_does_not_know(pytester: pytest.Pytester) -> None:
    """Widening the keyword list must not widen it to anything: a typo'd
    ``unmatchd=`` is a test-authoring mistake, and silently dropping it would
    leave the suite running strict while its author believed otherwise."""
    pytester.makepyfile(
        test_typo="""
import pytest

@pytest.mark.vendorfake("square", unmatchd="vendor-404")
def test_typo(vendorfake_unit):
    assert False
"""
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(errors=1)
    assert "unmatchd" in result.stdout.str()


def test_the_marker_refuses_a_third_positional_argument(pytester: pytest.Pytester) -> None:
    """The same mistake as an unknown keyword, in positional syntax: only
    ``vendor`` and ``profile`` are positional, and a consumer who drops the
    keyword off a call like ``unit("square", "oauth-only",
    unmatched="vendor-404")`` -- writing
    ``@pytest.mark.vendorfake("square", "oauth-only", "vendor-404")`` instead
    -- must not have the third argument silently discarded. Before this fix it
    was: the unit started on the default ``unmatched="error"`` with no error
    naming the argument that vanished (konyklabs/roadmap#73).
    """
    pytester.makepyfile(
        test_extra_positional="""
import pytest

@pytest.mark.vendorfake("square", "oauth-only", "vendor-404")
def test_extra_positional(vendorfake_unit):
    assert False
"""
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(errors=1)
    output = result.stdout.str()
    assert "at most two positional" in output
    assert "vendor" in output
    assert "profile" in output


def test_marker_arguments_fails_directly_on_a_third_positional_argument() -> None:
    """The same refusal, asserted directly against :func:`_marker_arguments`
    rather than through a subprocess -- ``pytest.fail`` raises ``Failed``
    inside the function under test itself, which a real fixture call cannot
    observe as cleanly as a direct call can. The ``Mark`` itself is built
    through ``pytest.mark`` rather than by hand, so this does not pin the
    internal shape of a pytest object this package does not own."""
    from _pytest.outcomes import Failed

    from vendorfake.pytest import _marker_arguments

    mark = pytest.mark.vendorfake("square", "oauth-only", "vendor-404").mark

    class _FakeNode:
        nodeid = "test_module.py::test_thing"

        def get_closest_marker(self, name: str) -> pytest.Mark:
            assert name == mark.name
            return mark

    class _FakeRequest:
        node = _FakeNode()

    with pytest.raises(Failed, match="at most two positional"):
        _marker_arguments(_FakeRequest(), "vendorfake_unit")  # type: ignore[arg-type]
