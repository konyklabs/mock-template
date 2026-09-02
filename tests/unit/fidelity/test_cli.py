"""``vendorfake-fidelity`` -- argparse, the three subcommands, the exit codes. No network."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.unit.fidelity.test_runner import CREATE, DECLARATION, case, make_anchor, step, synthetic_target
from vendorfake.core.transport.inprocess import in_process
from vendorfake.fidelity.__main__ import main
from vendorfake.fidelity.runner import TARGET_ENV_VAR, FidelityTarget

ANCHOR = ""
"""Set per test: the target factory below reads it, so the CLI's ``module:attr``
resolution reaches a package this test wrote."""

HERE = f"{__name__}:target"


def target() -> FidelityTarget:
    return synthetic_target(ANCHOR)


@pytest.fixture
def anchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv(TARGET_ENV_VAR, raising=False)

    def make(cases: list[dict[str, Any]], **kwargs: Any) -> str:
        name = make_anchor(tmp_path, monkeypatch, cases, **kwargs)
        monkeypatch.setattr(f"{__name__}.ANCHOR", name)
        return name

    return make


WRONG = dict(CREATE, expect={"status": 200, "body": {"order": {"state": "COMPLETED"}}})


# ---------------------------------------------------------------------------
# argparse.
# ---------------------------------------------------------------------------


def test_help_lists_the_three_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    printed = capsys.readouterr().out
    assert "{pin,run,report}" in printed


def test_no_target_is_refused_rather_than_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TARGET_ENV_VAR, raising=False)
    with pytest.raises(SystemExit) as raised:
        main(["run"])
    assert raised.value.code == 2


def test_an_unresolvable_target_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--target", "tests.unit.fidelity.test_cli:ANCHOR"]) == 2
    assert "not a FidelityTarget" in capsys.readouterr().err
    assert main(["run", "--target", "no.such.module:x"]) == 2


# ---------------------------------------------------------------------------
# run.
# ---------------------------------------------------------------------------


def test_run_exits_zero_when_every_case_passes(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([case("a", [CREATE]), case("b", [CREATE], provenance="judgment")])
    assert main(["run", "--target", HERE, "--no-validate"]) == 0
    printed = capsys.readouterr().out
    assert "[PASS] a (documented, test)" in printed and "[PASS] b (judgment, test)" in printed
    assert "2 passed, 0 failed (documented 1/1, judgment 1/1); responses NOT validated" in printed
    assert printed.rstrip().endswith("OK")


def test_run_exits_one_on_a_failed_case_and_prints_the_pointer(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([case("a", [CREATE]), case("b", [WRONG])])
    assert main(["run", "--target", HERE], client_factory=in_process) == 1
    printed = capsys.readouterr().out
    assert "[FAIL] b (documented, test)" in printed
    assert "step 'create' at /order/state: expected 'COMPLETED', got 'OPEN'" in printed
    assert printed.rstrip().endswith("NOT OK")


def test_run_case_filters_and_an_unknown_id_is_a_usage_error(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([case("a", [CREATE]), case("b", [WRONG])])
    assert main(["run", "--target", HERE, "--no-validate", "--case", "a"]) == 0
    assert "[FAIL]" not in capsys.readouterr().out
    assert main(["run", "--target", HERE, "--no-validate", "--case", "zzz"]) == 2
    assert "no such case(s): zzz; the corpus has: a, b" in capsys.readouterr().err


def test_run_reads_the_target_from_the_environment(anchor: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    anchor([case("a", [CREATE])])
    monkeypatch.setenv(TARGET_ENV_VAR, HERE)
    assert main(["run", "--no-validate"]) == 0


def test_run_profile_override_reaches_every_case(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([case("a", [step("who", "GET", "/v2/whoami")], profile="own")])
    assert main(["run", "--target", HERE, "--no-validate", "--profile", "forced"]) == 0
    assert "[PASS] a (documented, forced)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# run --base-url.
# ---------------------------------------------------------------------------


def test_base_url_with_nothing_behind_it_is_an_error_not_a_crash(
    anchor: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    name = anchor([case("a", [CREATE])])
    assert main(["run", "--base-url", "http://127.0.0.1:1", "--anchor", name]) == 2
    assert "cannot reach a unit at http://127.0.0.1:1" in capsys.readouterr().err
    # --target is an alternative way to name the anchor
    assert main(["run", "--base-url", "http://127.0.0.1:1", "--target", HERE]) == 2


def test_base_url_refuses_a_profile_flag_and_needs_an_anchor(anchor: Any) -> None:
    name = anchor([case("a", [CREATE])])
    with pytest.raises(SystemExit) as raised:
        main(["run", "--base-url", "http://127.0.0.1:1", "--anchor", name, "--profile", "full"])
    assert raised.value.code == 2
    with pytest.raises(SystemExit) as raised:
        main(["run", "--base-url", "http://127.0.0.1:1"])
    assert raised.value.code == 2


# ---------------------------------------------------------------------------
# report.
# ---------------------------------------------------------------------------


def test_report_prints_the_matrix_and_exits_one_on_an_undeclared_route(
    anchor: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    anchor([case("a", [CREATE])])
    assert main(["report", "--target", HERE], client_factory=in_process) == 1
    printed = capsys.readouterr().out
    assert "POST /v2/orders" in printed and "spec: operation CreateOrder" in printed
    assert "GET /v2/undeclared" in printed and "| spec: UNDECLARED |" in printed
    assert "cases: 1 passed, 0 failed" in printed
    assert printed.rstrip().endswith("NOT OK")


def test_report_exits_zero_when_every_route_is_declared_and_every_case_passes(
    anchor: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    declared = {
        **DECLARATION,
        "excused": [*DECLARATION["excused"], {"method": "GET", "path": "/v2/undeclared", "reason": "now excused"}],
    }
    anchor([case("a", [CREATE])], declaration=declared)
    assert main(["report", "--target", HERE], client_factory=in_process) == 0
    printed = capsys.readouterr().out
    assert "EXCUSED (now excused)" in printed and printed.rstrip().endswith("OK")


def test_report_exits_one_on_a_failed_case(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    declared = {
        **DECLARATION,
        "excused": [*DECLARATION["excused"], {"method": "GET", "path": "/v2/undeclared", "reason": "now excused"}],
    }
    anchor([case("a", [WRONG])], declaration=declared)
    assert main(["report", "--target", HERE], client_factory=in_process) == 1
    assert "FAILED a (documented)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# pin.
# ---------------------------------------------------------------------------


@dataclass
class _Refresh:
    changed_upstream: bool = False
    changed_extract: bool = False
    diff_summary: str = ""
    calls: list[dict[str, Any]] | None = None

    def __call__(self, anchor_dir: Path, declaration: Any, modeled: Any, **kwargs: Any) -> _Refresh:
        assert self.calls is not None
        self.calls.append({"anchor_dir": anchor_dir, "declaration": declaration, "modeled": modeled, **kwargs})
        return self


def test_pin_passes_the_modeled_routes_and_writes_when_not_checking(
    anchor: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = anchor([])
    refresh = _Refresh(changed_extract=True, diff_summary="+ one operation", calls=[])
    assert main(["pin", "--target", HERE], refresh=refresh) == 0
    (call,) = refresh.calls
    assert call["anchor_dir"] == tmp_path / name
    assert call["declaration"].variables == {"location_id": "LOC_1"}
    assert call["modeled"] == (
        ("GET", "/v2/orders/{order_id}"),
        ("GET", "/v2/plain"),
        ("GET", "/v2/undeclared"),
        ("GET", "/v2/whoami"),
        ("POST", "/v2/orders"),
    )
    assert call["check"] is False and len(call["fetched"]) == 10
    printed = capsys.readouterr().out
    assert "+ one operation" in printed and "pin: written" in printed


def test_pin_check_exits_one_on_any_change_and_zero_otherwise(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([])
    assert main(["pin", "--target", HERE, "--check"], refresh=_Refresh(calls=[])) == 0
    assert "pin: up to date" in capsys.readouterr().out
    changed = _Refresh(changed_upstream=True, diff_summary="upstream sha256 moved", calls=[])
    assert main(["pin", "--target", HERE, "--check"], refresh=changed) == 1
    printed = capsys.readouterr().out
    assert "upstream sha256 moved" in printed and "pin: CHANGED" in printed
    assert changed.calls[0]["check"] is True


def test_pin_offline_never_fetches_and_exits_on_inconsistency(
    anchor: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--offline`` is the pull-request form: it reads what is committed and
    fetches nothing, so a vendor release cannot turn every open PR red."""
    import json

    from vendorfake.fidelity.pin import Pin, write_pin

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline pin reached refresh")

    anchor_dir = tmp_path / anchor([])
    extract = anchor_dir / "extract.json"
    text = extract.read_text()
    write_pin(anchor_dir / "pin.json", Pin.from_extract(json.loads(text), text))
    assert main(["pin", "--offline", "--target", HERE], refresh=explode) == 0
    assert "consistent (offline)" in capsys.readouterr().out
    extract.write_text(text + "\n")
    assert main(["pin", "--offline", "--target", HERE], refresh=explode) == 1
    assert "INCONSISTENT" in capsys.readouterr().out
