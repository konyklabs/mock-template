"""``vendorfake-fidelity`` -- argparse, the four subcommands, the exit codes. No network."""

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


def test_help_lists_every_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    printed = capsys.readouterr().out
    assert "{pin,fetch,run,report,webhooks}" in printed


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
    assert "2 passed, 0 failed (documented 1/1, judgment 1/1, recorded 0/0); responses NOT validated" in printed
    assert printed.rstrip().endswith("OK")


def test_run_exits_one_on_a_failed_case_and_prints_the_pointer(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([case("a", [CREATE]), case("b", [WRONG])])
    assert main(["run", "--target", HERE], client_factory=in_process) == 1
    printed = capsys.readouterr().out
    assert "[FAIL value] b (documented, test)" in printed
    assert "step 'create' at /order/state: expected 'COMPLETED', got 'OPEN'" in printed
    assert printed.rstrip().endswith("NOT OK")


def test_run_case_filters_and_an_unknown_id_is_a_usage_error(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([case("a", [CREATE]), case("b", [WRONG])])
    assert main(["run", "--target", HERE, "--no-validate", "--case", "a"]) == 0
    assert "[FAIL" not in capsys.readouterr().out
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


def test_manifest_supplies_the_world_and_its_base_url(
    anchor: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--manifest`` is the run with no control plane behind it: the document answers instead."""
    from tests.unit.fidelity.test_runner import manifest_file
    from vendorfake.fidelity.report import CorpusReport
    from vendorfake.fidelity.runner import ManifestWorld

    name = anchor([case("a", [CREATE])])
    seen: dict[str, Any] = {}

    def fake(base_url: str, anchor_name: str, cases: Any, *, world: Any = None) -> CorpusReport:
        seen.update(base_url=base_url, anchor=anchor_name, world=world, cases=len(cases))
        return CorpusReport(target=base_url, results=(), validated=False, remote=True)

    monkeypatch.setattr("vendorfake.fidelity.__main__.run_corpus_remote", fake)
    assert main(["run", "--manifest", str(manifest_file(tmp_path)), "--anchor", name]) == 0
    assert seen["base_url"] == "http://localhost:8080" and seen["cases"] == 1
    assert isinstance(seen["world"], ManifestWorld) and seen["world"].profile() == "full"


def test_an_explicit_base_url_wins_over_the_manifests(
    anchor: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.fidelity.test_runner import manifest_file
    from vendorfake.fidelity.report import CorpusReport

    name = anchor([case("a", [CREATE])])
    seen: dict[str, Any] = {}

    def fake(base_url: str, anchor_name: str, cases: Any, *, world: Any = None) -> CorpusReport:
        seen["base_url"] = base_url
        return CorpusReport(target=base_url, results=(), validated=False, remote=True)

    monkeypatch.setattr("vendorfake.fidelity.__main__.run_corpus_remote", fake)
    argv = ["run", "--manifest", str(manifest_file(tmp_path)), "--base-url", "http://elsewhere:9", "--anchor", name]
    assert main(argv) == 0
    assert seen["base_url"] == "http://elsewhere:9"


def test_a_manifest_with_no_address_and_no_base_url_says_so(
    anchor: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.unit.fidelity.test_runner import manifest_file

    name = anchor([case("a", [CREATE])])
    path = manifest_file(tmp_path, base_url=...)
    assert main(["run", "--manifest", str(path), "--anchor", name]) == 2
    assert "carries no base_url; pass --base-url" in capsys.readouterr().err


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


# ---------------------------------------------------------------------------
# fetch, and pin on a vendor whose extract is never committed (konyklabs/roadmap#56).
# ---------------------------------------------------------------------------


@pytest.fixture
def not_vendored(anchor: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A non-vendored anchor with its pin written the way ``pin`` writes it,
    the cache root in ``tmp_path`` through the environment, and the
    synthetic document behind an injected fetcher. Returns ``(name, cache_root, fetcher)``."""
    from tests.unit.fidelity.test_cache import counting_fetcher, pin_for
    from tests.unit.fidelity.test_extract import URL, synthetic
    from vendorfake.fidelity.cache import CACHE_ENV_VAR
    from vendorfake.fidelity.pin import write_pin

    def make(*, pin: bool = True, document: dict[str, Any] | None = None) -> tuple[str, Path, Any]:
        declaration = {
            "schema": 1,
            "vendored": False,
            "sources": [{"kind": "openapi3", "url": URL}],
            "stubs_accepted": ["Missing"],
        }
        name = anchor([], declaration=declaration, extract=None)
        if pin:
            write_pin(tmp_path / name / "pin.json", pin_for(synthetic()))
        monkeypatch.setenv(CACHE_ENV_VAR, str(tmp_path / "cache"))
        return name, tmp_path / "cache", counting_fetcher(document or synthetic())

    return make


def test_fetch_populates_the_cache_and_exits_zero(
    not_vendored: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name, cache, fetcher = not_vendored()
    assert main(["fetch", "--target", HERE], fetcher=fetcher) == 0
    out, err = capsys.readouterr()
    assert f"fidelity cache: fetched and cut into {cache / name / 'extract.json'} (matches pin.json)" in out
    assert err == ""
    assert (cache / name / "extract.json").is_file()
    assert not (tmp_path / name / "extract.json").exists()
    # The second run is a hit and fetches nothing.
    assert main(["fetch", "--target", HERE], fetcher=fetcher) == 0
    assert "fidelity cache: hit" in capsys.readouterr().out
    assert len(fetcher.calls) == 1


def test_fetch_on_a_moved_upstream_exits_zero_with_the_loud_line(
    not_vendored: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.unit.fidelity.test_extract import synthetic

    moved = synthetic()
    moved["info"]["version"] = "9.9.9"
    name, cache, fetcher = not_vendored(document=moved)
    assert main(["fetch", "--target", HERE], fetcher=fetcher) == 0
    out, err = capsys.readouterr()
    assert err.startswith("UPSTREAM MOVED: https://example.test/spec.json pinned ")
    assert "-- the pin is stale; tests run against the fresh document" in err
    assert "UPSTREAM MOVED (1 source(s))" in out
    assert (cache / name / "DRIFT").is_file()


def test_fetch_without_a_pin_is_a_usage_error(not_vendored: Any, capsys: pytest.CaptureFixture[str]) -> None:
    _, cache, fetcher = not_vendored(pin=False)
    assert main(["fetch", "--target", HERE], fetcher=fetcher) == 2
    assert "run `vendorfake-fidelity pin --target" in capsys.readouterr().err
    assert fetcher.calls == [] and not cache.exists()


def test_fetch_with_no_network_and_no_cache_is_a_named_skip(
    not_vendored: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.unit.fidelity.test_cache import counting_fetcher

    not_vendored()
    offline = counting_fetcher(None, error=ConnectionError("unreachable"))
    assert main(["fetch", "--target", HERE], fetcher=offline) == 3
    err = capsys.readouterr().err
    assert "fetch:" in err and "cannot fetch https://example.test/spec.json (unreachable)" in err


def test_fetch_on_a_vendored_anchor_is_a_no_op(anchor: Any, capsys: pytest.CaptureFixture[str]) -> None:
    anchor([])
    assert main(["fetch", "--target", HERE]) == 0
    assert "is vendored; its extract is committed and there is nothing to cache" in capsys.readouterr().out


def test_pin_offline_on_a_non_vendored_anchor_verifies_the_cache_or_notes_there_is_none(
    not_vendored: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline pin reached refresh")

    name, cache, fetcher = not_vendored()
    # An empty cache verifies nothing: the named skip (exit 3), the same one
    # `fetch` and `report` answer, so a self-test step reads SKIP rather than FAIL.
    assert main(["pin", "--offline", "--target", HERE], refresh=explode) == 3
    captured = capsys.readouterr()
    assert "UNAVAILABLE" in captured.err and "no cached extract" in captured.err
    assert main(["fetch", "--target", HERE], fetcher=fetcher) == 0
    capsys.readouterr()
    assert main(["pin", "--offline", "--target", HERE], refresh=explode) == 0
    printed = capsys.readouterr().out
    assert f"extract.json: matches pin.json (cached at {cache / name / 'extract.json'})" in printed


def test_pin_on_a_non_vendored_anchor_writes_the_extract_to_the_cache_not_the_package(
    not_vendored: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real ``refresh``, through the CLI: the package gains nothing but a rewritten pin."""
    from vendorfake.fidelity import pin as pin_module

    name, cache, fetcher = not_vendored(pin=False)
    real = pin_module.refresh

    def refresh(*args: Any, **kwargs: Any) -> Any:
        return real(*args, fetcher=fetcher, **kwargs)

    def listing() -> list[str]:
        return sorted(p.name for p in (tmp_path / name).iterdir() if p.name != "__pycache__")

    before = listing()
    assert main(["pin", "--target", HERE], refresh=refresh) == 0
    printed = capsys.readouterr().out
    assert "pin: written" in printed and f"extract.json to {cache / name}" in printed
    assert listing() == sorted([*before, "pin.json"])
    assert (cache / name / "extract.json").is_file()
    assert main(["pin", "--target", HERE, "--check"], refresh=refresh) == 0
    assert "pin: up to date" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# webhooks.
# ---------------------------------------------------------------------------


SIGNING_TARGET = f"{__name__}:signing_target"


def signing_target() -> FidelityTarget:
    from tests.unit.fidelity.test_webhooks import stub_signer

    return FidelityTarget(
        name="synthetic",
        anchor=ANCHOR,
        open_unit=synthetic_target(ANCHOR).open_unit,
        default_profile="test",
        signer=stub_signer,
    )


def test_webhooks_verifies_every_golden_in_the_directory(capsys: pytest.CaptureFixture[str]) -> None:
    from tests.unit.fidelity.test_webhooks import GOLDENS

    assert main(["webhooks", "--target", SIGNING_TARGET, "--golden", str(GOLDENS)]) == 0
    printed = capsys.readouterr().out
    assert "[PASS] stub-delivery.json (synthetic, judgment)" in printed
    assert printed.rstrip().endswith("OK")


def test_webhooks_exits_one_on_a_divergence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json as _json

    from tests.unit.fidelity.test_webhooks import SIGNATURE_HEADER, _doc

    tampered = _doc(**{f"delivery.headers.{SIGNATURE_HEADER}": "AAAA"})
    (tmp_path / "one.json").write_text(_json.dumps(tampered))
    assert main(["webhooks", "--target", SIGNING_TARGET, "--golden", str(tmp_path)]) == 1
    assert "expected 'AAAA'" in capsys.readouterr().out


def test_webhooks_refuses_a_target_that_publishes_no_signer(capsys: pytest.CaptureFixture[str]) -> None:
    from tests.unit.fidelity.test_webhooks import GOLDENS

    assert main(["webhooks", "--target", HERE, "--golden", str(GOLDENS)]) == 2
    assert "publishes no signer" in capsys.readouterr().err


def test_webhooks_on_a_directory_that_is_not_there_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["webhooks", "--target", SIGNING_TARGET, "--golden", str(tmp_path / "nope")]) == 2
    assert "no such directory of goldens" in capsys.readouterr().err


def test_webhooks_on_an_empty_directory_says_so_rather_than_passing_silently(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["webhooks", "--target", SIGNING_TARGET, "--golden", str(tmp_path)]) == 0
    assert "no goldens in" in capsys.readouterr().out
