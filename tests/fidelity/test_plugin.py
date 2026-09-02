"""The pytest rendering: one test per case, and the summary by provenance.

Two layers, as for the conformance plugin: the fast tests drive ``run_case``
and the ledger directly; the slow ones run pytest in a subprocess so that the
entry point registered in ``pyproject.toml`` is what loads the plugin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

import pytest

from tests.fidelity.harness import ANCHOR, VENDOR
from tests.unit.fidelity.test_runner import CREATE, case, make_anchor, synthetic_target
from vendorfake.fidelity.corpus import load_corpus, parse_case
from vendorfake.fidelity.plugin import FidelityCaseFailure, PluginCase, _Ledger, run_case
from vendorfake.fidelity.runner import TARGET_ENV_VAR, resolve_target

REPO_ROOT = Path(__file__).resolve().parents[2]
WRONG = dict(CREATE, expect={"status": 200, "body": {"order": {"state": "COMPLETED"}}})


def test_the_shipped_vendor_publishes_a_target_whose_unit_opens() -> None:
    target = resolve_target("tests.fidelity.harness:square_target")
    assert target.name == VENDOR and target.anchor == ANCHOR
    with target.open_unit(None) as unit:
        assert unit.routes
    assert isinstance(load_corpus(ANCHOR), tuple)


def test_an_unconfigured_run_skips_once_and_says_how_to_configure_it() -> None:
    with pytest.raises(unittest.SkipTest) as raised:
        run_case(None)
    assert "--fidelity-target" in str(raised.value) and TARGET_ENV_VAR in str(raised.value)


def test_run_case_records_by_provenance_and_raises_on_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = make_anchor(tmp_path, monkeypatch, [])
    target = synthetic_target(anchor)
    ledger = _Ledger()
    run_case(PluginCase(parse_case(case("ok", [CREATE])), target, None, validate=False), ledger)
    with pytest.raises(FidelityCaseFailure) as raised:
        run_case(
            PluginCase(parse_case(case("bad", [WRONG], provenance="judgment")), target, None, validate=False), ledger
        )
    assert "step 'create' at /order/state: expected 'COMPLETED', got 'OPEN'" in str(raised.value)
    assert "source: https://example.test/docs" in str(raised.value)
    assert ledger.counts == {"documented": [1, 0], "judgment": [0, 1]}


# ---------------------------------------------------------------------------
# Through pytest itself.
# ---------------------------------------------------------------------------


def _write_suite(tmp_path: Path, cases: list[dict[str, Any]]) -> None:
    anchor = tmp_path / "synthetic_anchor_plugin"
    (anchor / "corpus").mkdir(parents=True)
    (anchor / "__init__.py").write_text("")
    from tests.unit.fidelity.test_runner import DECLARATION, EXTRACT

    (anchor / "declaration.json").write_text(json.dumps(DECLARATION))
    (anchor / "extract.json").write_text(json.dumps(EXTRACT))
    for index, doc in enumerate(cases):
        (anchor / "corpus" / f"{index:02d}.json").write_text(json.dumps(doc))
    (tmp_path / "fidelity_target_mod.py").write_text(
        "from tests.unit.fidelity.test_runner import synthetic_target\n"
        "target = synthetic_target('synthetic_anchor_plugin')\n"
    )
    (tmp_path / "test_generated.py").write_text(
        "from vendorfake.fidelity.plugin import run_case\n\ndef test_case(fidelity_case):\n    run_case(fidelity_case)\n"
    )


def _pytest(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop(TARGET_ENV_VAR, None)
    env["PYTHONPATH"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "test_generated.py"), "-q", "-p", "no:cacheprovider", *extra],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_pytest_expands_the_corpus_into_one_test_per_case_and_summarises_by_provenance(tmp_path: Path) -> None:
    _write_suite(tmp_path, [case("a", [CREATE]), case("b", [WRONG]), case("c", [CREATE], provenance="judgment")])
    run = _pytest(tmp_path, "--fidelity-target", "fidelity_target_mod:target", "--fidelity-no-validate")
    out = run.stdout
    assert run.returncode == 1, out + run.stderr
    assert "test_case[b]" in out and "FidelityCaseFailure" in out
    assert "2 passed" in out and "1 failed" in out
    assert (
        "fidelity: target 'synthetic', 3 case(s) [documented: 1 passed, 1 failed; judgment: 1 passed, 0 failed]" in out
    )
    assert "responses NOT validated" in out


def test_pytest_case_filter_and_unconfigured_skip(tmp_path: Path) -> None:
    _write_suite(tmp_path, [case("a", [CREATE]), case("b", [WRONG])])
    run = _pytest(
        tmp_path, "--fidelity-target", "fidelity_target_mod:target", "--fidelity-no-validate", "--fidelity-case", "a"
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout and "test_case[b]" not in run.stdout
    run = _pytest(tmp_path, "-rs")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 skipped" in run.stdout and "no fidelity target: pass --fidelity-target" in run.stdout


def test_pytest_default_run_validates_through_the_validating_client(tmp_path: Path) -> None:
    """Without ``--fidelity-no-validate`` every response goes through ``fidelity/validate.py``."""
    _write_suite(tmp_path, [case("a", [CREATE])])
    run = _pytest(tmp_path, "--fidelity-target", "fidelity_target_mod:target")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout and "responses NOT validated" not in run.stdout
