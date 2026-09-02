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

DECLARATION = FidelityDeclaration(anchor="tests.synthetic", sources=(SpecSource(kind="openapi3", url=URL),))


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
    assert extract["x-vendorfake"]["sources"] == [pin.sources[0].to_json()]


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
