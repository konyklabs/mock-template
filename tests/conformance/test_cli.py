"""``python -m vendorfake.conformance`` -- the rendering with no test runner in it.

The CLI is what a container healthcheck and a vendor without pytest actually
call, so its two ways of naming a unit are both exercised against a real one:
``--target`` builds a fresh unit per contract, and ``--base-url`` reaches a
unit this test starts and then leaves running while the whole registry is asked
of it.
"""

from __future__ import annotations

import pytest

from tests.conformance.harness import FAULTLESS_PROFILE, VENDOR, serving
from vendorfake.conformance import CHECKS, HttpConformanceClient, resolve_target
from vendorfake.conformance.__main__ import main
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.registry import create_unit

HARNESS_TARGET = "tests.conformance.harness:target"


# ---------------------------------------------------------------------------
# Naming a target.
# ---------------------------------------------------------------------------


def test_a_factory_and_a_bare_target_both_resolve() -> None:
    assert resolve_target(HARNESS_TARGET).name == VENDOR


def test_something_that_is_not_a_target_says_what_it_found() -> None:
    with pytest.raises(LookupError) as raised:
        resolve_target("tests.conformance.harness:VENDOR")
    assert "not a ConformanceTarget" in str(raised.value)


def test_naming_no_unit_at_all_is_refused_rather_than_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VENDORFAKE_CONFORMANCE_TARGET", raising=False)
    with pytest.raises(SystemExit) as raised:
        main([])
    assert raised.value.code == 2


def test_list_prints_the_registry_without_building_anything(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list"]) == 0
    printed = capsys.readouterr().out
    for spec in CHECKS:
        assert spec.id in printed
        assert spec.asserts in printed


# ---------------------------------------------------------------------------
# --target: the profile matrix.
# ---------------------------------------------------------------------------


def test_one_contract_on_one_profile_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--target", HARNESS_TARGET, "--transport", "inprocess", "--profile", "full", "--check", "C01"])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "[PASS] C01" in printed


def test_a_narrowed_run_reports_never_ran_as_information_not_as_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """C08 cannot run on a profile with no chaos capability, and that is not a defect.

    The anti-vacuity rule is a statement about a matrix. Asking one contract on
    one profile is not one, so the run must stay green and say why.
    """
    code = main(
        [
            "--target",
            HARNESS_TARGET,
            "--transport",
            "inprocess",
            "--profile",
            FAULTLESS_PROFILE,
            "--check",
            "C08",
        ]
    )
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "never ran on any profile in this run: C08 (informational" in printed
    assert printed.rstrip().endswith("OK")


# ---------------------------------------------------------------------------
# --base-url: a unit somebody else is running.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_base_url_discovers_the_profile_and_runs_the_registry(capsys: pytest.CaptureFixture[str]) -> None:
    unit = create_unit(vendor=VENDOR, profile="full", sink=MemorySink())
    try:
        with serving(unit) as base_url:
            code = main(["--base-url", base_url])
    finally:
        unit.stop()
    printed = capsys.readouterr().out
    assert code == 0, printed
    # The profile was read from the running unit, not passed in.
    assert "== full / http ==" in printed
    assert "SHARED, not rebuilt per check" in printed
    # C10 compares two bindings and a remote target has one; everything else is asked.
    assert f"{len(CHECKS) - 1} passed, 0 failed, 1 skipped" in printed, printed


def _enabled(base_url: str) -> frozenset[str]:
    client = HttpConformanceClient(base_url)
    try:
        document = client.call("GET", "/__unit/capabilities").json()
    finally:
        client.close()
    return frozenset(row["name"] for row in document["capabilities"] if row["enabled"])


@pytest.mark.integration
def test_base_url_restores_the_shared_unit_between_contracts() -> None:
    """The property that makes a shared unit usable at all.

    C03 switches capabilities off to see how a disabled one answers, and C11
    does the same for the core's gates. Against a unit that is rebuilt per
    check that is invisible; against a shared one, a restore that did not
    happen would leave every later contract reading a crippled unit and calling
    the result conformance. The capability set before and after is the only
    place that is observable from outside.
    """
    unit = create_unit(vendor=VENDOR, profile="full", sink=MemorySink())
    try:
        with serving(unit) as base_url:
            before = _enabled(base_url)
            assert main(["--base-url", base_url, "--check", "C03", "--check", "C11"]) == 0
            after = _enabled(base_url)
    finally:
        unit.stop()
    assert before == after
    assert before, "the full profile must enable something, or this proves nothing"


@pytest.mark.integration
def test_base_url_refuses_a_profile_flag_that_could_mislabel_the_report() -> None:
    unit = create_unit(vendor=VENDOR, profile="full", sink=MemorySink())
    try:
        with serving(unit) as base_url, pytest.raises(SystemExit) as raised:
            main(["--base-url", base_url, "--profile", "full"])
    finally:
        unit.stop()
    assert raised.value.code == 2


def test_an_address_with_no_unit_behind_it_is_an_error_not_a_crash(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--base-url", "http://127.0.0.1:1"])
    assert code == 2
    assert "cannot reach a unit at http://127.0.0.1:1" in capsys.readouterr().err
