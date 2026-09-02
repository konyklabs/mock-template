"""``refresh`` and the pin, with bytes in hand and no network.

Every test drives ``refresh`` through an injected fetcher over the synthetic
document from ``test_extract``; the assertions are on what lands on disk and
on the three fields of ``RefreshResult``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.unit.fidelity.test_extract import MODELED, URL, blob, synthetic
from vendorfake.fidelity.pin import Pin, PinnedSource, fetch, read_pin, refresh, verify, write_pin
from vendorfake.fidelity.types import EXTRACT_FILE, PIN_FILE, FidelityDeclaration, SpecSource

DECLARATION = FidelityDeclaration(
    anchor="tests.synthetic", sources=(SpecSource(kind="openapi3", url=URL),), stubs_accepted=("Missing",)
)


def fetcher_for(document: dict[str, Any]) -> Any:
    data = blob(document)
    calls: list[str] = []

    def fetcher(url: str) -> bytes:
        calls.append(url)
        return data

    fetcher.calls = calls  # type: ignore[attr-defined]
    return fetcher


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def pinned(tmp_path: Path) -> Path:
    """A directory that already holds a refreshed extract and pin."""
    result = refresh(tmp_path, DECLARATION, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-09-02")
    assert result.changed_upstream and result.changed_extract
    return tmp_path


def test_pin_round_trips_through_write_and_read(tmp_path: Path) -> None:
    pin = Pin(
        sources=(PinnedSource(url=URL, sha256="ab" * 32, bytes=12, version="1.0", fetched="2026-09-02"),),
        extract_sha256="cd" * 32,
    )
    write_pin(tmp_path / PIN_FILE, pin)
    assert read_pin(tmp_path / PIN_FILE) == pin
    text = (tmp_path / PIN_FILE).read_text()
    assert text.endswith("\n")
    assert json.loads(text)["schema"] == 1


def test_a_pin_with_the_wrong_schema_is_refused(tmp_path: Path) -> None:
    (tmp_path / PIN_FILE).write_text('{"schema": 2, "sources": [], "extract_sha256": ""}')
    with pytest.raises(ValueError, match='"schema": 1'):
        read_pin(tmp_path / PIN_FILE)


def test_fetch_uses_the_injected_fetcher_and_returns_its_bytes() -> None:
    fetcher = fetcher_for(synthetic())
    assert fetch(SpecSource(kind="openapi3", url=URL), fetcher=fetcher) == blob(synthetic())
    assert fetcher.calls == [URL]


def test_first_refresh_writes_both_files_and_the_pin_digests_the_extract(tmp_path: Path) -> None:
    fetcher = fetcher_for(synthetic())
    result = refresh(tmp_path, DECLARATION, MODELED, fetcher=fetcher, fetched="2026-09-02")
    assert fetcher.calls == [URL]
    assert result.changed_upstream is True
    assert result.changed_extract is True
    assert "new pin" in result.diff_summary
    assert f"{EXTRACT_FILE}: new" in result.diff_summary
    pin = read_pin(tmp_path / PIN_FILE)
    assert pin.extract_sha256 == sha(tmp_path / EXTRACT_FILE)
    assert pin.sources == (
        PinnedSource(
            url=URL,
            sha256=hashlib.sha256(blob(synthetic())).hexdigest(),
            bytes=len(blob(synthetic())),
            version="2.3.4",
            fetched="2026-09-02",
        ),
    )
    extract = json.loads((tmp_path / EXTRACT_FILE).read_text())
    assert extract["x-vendorfake"]["sources"] == [{**pin.sources[0].to_json(), "label": "spec"}]


def test_unchanged_upstream_on_a_later_day_is_byte_identical_and_keeps_the_pinned_date(pinned: Path) -> None:
    before = ((pinned / EXTRACT_FILE).read_bytes(), (pinned / PIN_FILE).read_bytes())
    result = refresh(pinned, DECLARATION, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-12-25")
    assert result.changed_upstream is False
    assert result.changed_extract is False
    assert result.changed is False
    assert "unchanged" in result.diff_summary
    assert ((pinned / EXTRACT_FILE).read_bytes(), (pinned / PIN_FILE).read_bytes()) == before
    assert read_pin(pinned / PIN_FILE).sources[0].fetched == "2026-09-02"


def test_check_detects_a_changed_upstream_byte_and_writes_nothing(pinned: Path) -> None:
    before = ((pinned / EXTRACT_FILE).read_bytes(), (pinned / PIN_FILE).read_bytes())
    document = synthetic()
    document["info"]["description"] = "prose, edited"  # stripped: the extract itself does not change
    result = refresh(pinned, DECLARATION, MODELED, fetcher=fetcher_for(document), fetched="2026-09-03", check=True)
    assert result.changed_upstream is True
    assert result.changed_extract is False
    assert "changed sha256" in result.diff_summary
    assert "check mode: nothing written" in result.diff_summary
    assert ((pinned / EXTRACT_FILE).read_bytes(), (pinned / PIN_FILE).read_bytes()) == before


def test_check_detects_a_changed_extract_and_names_the_schema(pinned: Path) -> None:
    document = synthetic()
    document["components"]["schemas"]["Money"]["properties"]["amount"]["type"] = "string"
    result = refresh(pinned, DECLARATION, MODELED, fetcher=fetcher_for(document), fetched="2026-09-03", check=True)
    assert result.changed_upstream is True
    assert result.changed_extract is True
    assert f"{EXTRACT_FILE}: schema ~ Money" in result.diff_summary
    assert "lines)" in result.diff_summary


def test_a_changed_modeled_list_changes_the_extract_without_touching_the_upstream(pinned: Path) -> None:
    fewer = [pair for pair in MODELED if pair[0] != "GET"]
    result = refresh(pinned, DECLARATION, fewer, fetcher=fetcher_for(synthetic()), fetched="2026-09-03")
    assert result.changed_upstream is False
    assert result.changed_extract is True
    assert f"{EXTRACT_FILE}: modeled - GET /v1/widgets/{{widget_id}}" in result.diff_summary
    assert f"{EXTRACT_FILE}: schema - Audit" in result.diff_summary
    # Written, since not a check: the pin now digests the new extract and keeps the pinned date.
    pin = read_pin(pinned / PIN_FILE)
    assert pin.extract_sha256 == sha(pinned / EXTRACT_FILE)
    assert pin.sources[0].fetched == "2026-09-02"


def test_a_hand_edited_extract_is_reported_as_changed(pinned: Path) -> None:
    path = pinned / EXTRACT_FILE
    path.write_text(path.read_text().replace('"minimum": 0', '"minimum": 1'))
    result = refresh(pinned, DECLARATION, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-09-03", check=True)
    assert result.changed_upstream is False
    assert result.changed_extract is True
    assert "edited by hand" in result.diff_summary


def test_check_on_an_empty_directory_writes_nothing(tmp_path: Path) -> None:
    result = refresh(tmp_path, DECLARATION, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-09-02", check=True)
    assert result.changed is True
    assert not (tmp_path / EXTRACT_FILE).exists()
    assert not (tmp_path / PIN_FILE).exists()


# -- verify: the offline half ----------------------------------------------


def test_verify_is_clean_after_a_refresh_and_never_fetches(pinned: Path) -> None:
    result = verify(pinned, DECLARATION)
    assert not result.changed
    assert "offline; not re-fetched" in result.diff_summary
    assert "matches pin.json" in result.diff_summary


def test_verify_reports_a_hand_edited_extract(pinned: Path) -> None:
    extract = pinned / EXTRACT_FILE
    extract.write_text(extract.read_text().replace('"openapi": "3.0.0"', '"openapi": "3.0.1"'))
    result = verify(pinned, DECLARATION)
    assert result.changed_extract and not result.changed_upstream
    assert "edited by hand?" in result.diff_summary


def test_verify_reports_a_source_the_declaration_and_pin_disagree_on(pinned: Path) -> None:
    other = replace(DECLARATION, sources=(SpecSource(kind="openapi3", url="https://example.test/other.json"),))
    result = verify(pinned, other)
    assert result.changed_upstream and not result.changed_extract
    assert "declared source not pinned: https://example.test/other.json" in result.diff_summary
    assert f"pinned source no longer declared: {URL}" in result.diff_summary


def test_verify_names_missing_files(tmp_path: Path) -> None:
    result = verify(tmp_path, DECLARATION)
    assert result.changed
    assert "extract.json, pin.json" in result.diff_summary


def test_verify_refuses_a_stub_the_declaration_has_not_accepted(pinned: Path) -> None:
    """A stubbed schema validates everything it types (deep-lens D4,
    konyklabs/roadmap#55): a new one is red offline until it is accepted by name."""
    from vendorfake.fidelity.extract import render_json

    extract = pinned / EXTRACT_FILE
    doc = json.loads(extract.read_text())
    assert doc["x-vendorfake"]["stubbed"] == ["Missing"]
    # An upstream release dangles a second schema; `pin` re-cuts and re-pins.
    doc["x-vendorfake"]["stubbed"] = ["Missing", "Money"]
    text = render_json(doc)
    extract.write_text(text)
    write_pin(pinned / PIN_FILE, Pin.from_extract(doc, text))
    result = verify(pinned, DECLARATION)
    assert result.changed_extract and not result.changed_upstream
    assert "schema 'Money' is stubbed to {}" in result.diff_summary
    assert "Missing" not in result.diff_summary
    assert not verify(pinned, replace(DECLARATION, stubs_accepted=("Missing", "Money"))).changed


# -- verify: the non-vendored form (konyklabs/roadmap#56) ---------------------


NOT_VENDORED = replace(DECLARATION, vendored=False)


def test_verify_of_a_non_vendored_declaration_with_no_cache_passes_with_a_note(tmp_path: Path) -> None:
    """Offline cannot fetch, and the design accepts that: an empty cache is
    not an inconsistency, it is nothing to verify."""
    package = tmp_path / "pkg"
    package.mkdir()
    refresh(
        package, NOT_VENDORED, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-09-02", cache_dir=tmp_path / "c"
    )
    assert not (package / EXTRACT_FILE).exists()
    result = verify(package, NOT_VENDORED, cache_dir=tmp_path / "empty")
    # Nothing checked is not a pass (adversarial A6, konyklabs/roadmap#56).
    assert result.changed
    assert "no cache" in result.diff_summary and "cannot be verified offline" in result.diff_summary
    assert "fetch" in result.diff_summary


def test_verify_of_a_non_vendored_declaration_reads_the_cache_and_a_drift_note(tmp_path: Path) -> None:
    from vendorfake.fidelity.cache import DRIFT_FILE, cache_path

    package = tmp_path / "pkg"
    package.mkdir()
    cache = tmp_path / "c"
    refresh(package, NOT_VENDORED, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-09-02", cache_dir=cache)
    result = verify(package, NOT_VENDORED, cache_dir=cache)
    assert not result.changed
    assert (
        f"matches {PIN_FILE} (cached at {cache_path(NOT_VENDORED.anchor, cache) / EXTRACT_FILE})" in result.diff_summary
    )
    # A cache cut at run time from moved upstream carries a DRIFT note: the pin does not describe it.
    (cache_path(NOT_VENDORED.anchor, cache) / DRIFT_FILE).write_text(
        json.dumps(
            {
                "schema": 1,
                "rows": [{"url": URL, "pinned_sha256": "ab" * 32, "fetched_sha256": "cd" * 32, "fetched_bytes": 3}],
            }
        )
    )
    result = verify(package, NOT_VENDORED, cache_dir=cache)
    assert result.changed_upstream
    assert f"UPSTREAM MOVED: {URL} pinned {'ab' * 6} fetched {'cd' * 6}" in result.diff_summary


def test_verify_of_a_non_vendored_declaration_needs_the_pin_only(tmp_path: Path) -> None:
    result = verify(tmp_path, NOT_VENDORED, cache_dir=tmp_path / "c")
    assert result.changed
    assert f"missing under {tmp_path}: {PIN_FILE} -- run `pin` once" == result.diff_summary


def test_a_vendored_pin_carries_no_modeled_list_and_a_non_vendored_one_does(pinned: Path, tmp_path: Path) -> None:
    """Square's committed pin must stay byte-identical: ``modeled`` is written only where the cache needs it."""
    assert "modeled" not in json.loads((pinned / PIN_FILE).read_text())
    assert read_pin(pinned / PIN_FILE).modeled == ()
    package = tmp_path / "nv"
    package.mkdir()
    refresh(
        package, NOT_VENDORED, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-09-02", cache_dir=tmp_path / "c"
    )
    assert read_pin(package / PIN_FILE).modeled == tuple(f"{m} {p}" for m, p in MODELED)
    # A second refresh on an unchanged upstream is byte-identical, modeled included.
    before = (package / PIN_FILE).read_bytes()
    result = refresh(
        package, NOT_VENDORED, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-12-25", cache_dir=tmp_path / "c"
    )
    assert not result.changed and (package / PIN_FILE).read_bytes() == before


def test_refresh_hands_the_declarations_extension_map_and_error_schema_to_the_cut(tmp_path: Path) -> None:
    """``Unreachable`` is referenced by no modeled operation; it lands in the
    extract only because the declaration names it as the error schema. The
    mapped extension shows the map went through as well."""
    document = synthetic()
    document["components"]["schemas"]["Money"]["properties"]["currency"]["x-nullable"] = True
    declaration = replace(DECLARATION, error_schema="Unreachable", extension_map={"x-nullable": "nullable"})
    refresh(tmp_path, declaration, MODELED, fetcher=fetcher_for(document), fetched="2026-09-02")
    extract = json.loads((tmp_path / EXTRACT_FILE).read_text())
    assert "Unreachable" in extract["components"]["schemas"]
    assert extract["components"]["schemas"]["Money"]["properties"]["currency"] == {"type": "string", "nullable": True}
    assert extract["x-vendorfake"]["rewritten"]["extensions"] == {"x-nullable": 1}


def test_refresh_refuses_a_declared_error_schema_the_upstream_lacks(tmp_path: Path) -> None:
    declaration = replace(DECLARATION, error_schema="NoSuchSchema")
    with pytest.raises(ValueError, match="NoSuchSchema"):
        refresh(tmp_path, declaration, MODELED, fetcher=fetcher_for(synthetic()), fetched="2026-09-02")
    assert not (tmp_path / EXTRACT_FILE).exists()
