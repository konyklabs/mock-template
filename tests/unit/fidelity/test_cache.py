"""The fetch-never-commit cache, with an injected fetcher and a ``tmp_path`` cache.

Every document here is the synthetic one from ``test_extract``; no upstream
byte of any real vendor appears in this file or is fetched by it. The pin is
written into a throwaway importable package the way ``pin`` would write it,
so ``cached_extract`` reads it through ``importlib.resources`` as in production.
"""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.unit.fidelity.test_extract import MODELED, SOURCE, URL, blob, synthetic
from vendorfake.fidelity.cache import (
    CACHE_ENV_VAR,
    DRIFT_FILE,
    cache_path,
    cache_root,
    cached_extract,
    drift_rows,
    populate,
)
from vendorfake.fidelity.extract import cut_extract, render_json, sha256_hex
from vendorfake.fidelity.pin import Pin, read_pin, refresh, write_pin
from vendorfake.fidelity.types import EXTRACT_FILE, PIN_FILE, FidelityDeclaration, SpecSource, load_extract

FETCHED = "2026-09-02"


def counting_fetcher(document: dict[str, Any] | None, *, error: Exception | None = None) -> Any:
    """A fetcher over one literal document; ``error`` makes it raise instead (no network)."""
    data = None if document is None else blob(document)
    calls: list[str] = []

    def fetcher(url: str) -> bytes:
        calls.append(url)
        if error is not None:
            raise error
        assert data is not None
        return data

    fetcher.calls = calls  # type: ignore[attr-defined]
    return fetcher


def make_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pin: Pin | None) -> str:
    """A throwaway importable package holding a non-vendored declaration and, optionally, its pin."""
    name = f"synthetic_cache_anchor_{uuid.uuid4().hex[:8]}"
    package = tmp_path / "pkg" / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "declaration.json").write_text(
        json.dumps({"schema": 1, "vendored": False, "sources": [{"kind": "openapi3", "url": URL}]})
    )
    if pin is not None:
        write_pin(package / PIN_FILE, pin)
    monkeypatch.syspath_prepend(str(tmp_path / "pkg"))
    return name


def pin_for(document: dict[str, Any]) -> Pin:
    """The pin ``refresh`` would write for ``document`` under MODELED."""
    cut = cut_extract([(SOURCE, blob(document))], MODELED, fetched=FETCHED)
    return Pin.from_extract(cut, render_json(cut), modeled=MODELED)


def listing(package: Path) -> list[str]:
    """Every name under the package but the bytecode cache importing it creates."""
    return sorted(p.name for p in package.iterdir() if p.name != "__pycache__")


def declaration_for(anchor: str) -> FidelityDeclaration:
    return FidelityDeclaration(anchor=anchor, sources=(SpecSource(kind="openapi3", url=URL),), vendored=False)


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test's cache root, through the environment variable a CI step would set."""
    root = tmp_path / "cache"
    monkeypatch.setenv(CACHE_ENV_VAR, str(root))
    return root


# ---------------------------------------------------------------------------
# Where the cache is.
# ---------------------------------------------------------------------------


def test_cache_root_precedence_is_argument_then_env_then_xdg_then_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CACHE_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert cache_root() == tmp_path / "home" / ".cache" / "vendorfake" / "fidelity"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_root() == tmp_path / "xdg" / "vendorfake" / "fidelity"
    monkeypatch.setenv(CACHE_ENV_VAR, str(tmp_path / "explicit"))
    assert cache_root() == tmp_path / "explicit"
    assert cache_root(tmp_path / "argument") == tmp_path / "argument"
    assert cache_path("a.b.fidelity", tmp_path / "argument") == tmp_path / "argument" / "a.b.fidelity"


# ---------------------------------------------------------------------------
# Miss, hit, and what lands where.
# ---------------------------------------------------------------------------


def test_miss_fetches_cuts_and_caches_and_writes_nothing_under_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    package = tmp_path / "pkg" / name
    before = listing(package)
    fetcher = counting_fetcher(synthetic())
    err = io.StringIO()
    result = populate(name, declaration_for(name), fetcher=fetcher, fetched="2026-12-25", stderr=err)
    assert fetcher.calls == [URL]
    assert result.hit is False and result.drift == () and result.extract_differs is False
    assert result.path == cache / name
    cached = cache / name / EXTRACT_FILE
    assert cached.is_file()
    assert sha256_hex(cached.read_bytes()) == read_pin(package / PIN_FILE).extract_sha256
    assert not (cache / name / DRIFT_FILE).exists()
    assert err.getvalue() == ""
    # The package is untouched: the extract is nowhere under it.
    assert listing(package) == before
    assert not (package / EXTRACT_FILE).exists()
    # The row keeps the pinned date even though the cut happened on a later day.
    assert result.document["x-vendorfake"]["sources"][0]["fetched"] == FETCHED
    assert "matches pin.json" in result.summary


def test_hit_reads_the_cache_and_never_fetches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path) -> None:
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    populate(name, declaration_for(name), fetcher=counting_fetcher(synthetic()))
    fetcher = counting_fetcher(None, error=AssertionError("a cache hit reached the network"))
    extract = cached_extract(name, declaration_for(name), fetcher=fetcher)
    assert fetcher.calls == []
    assert extract.operation("POST", "/v1/widgets") is not None
    result = populate(name, declaration_for(name), fetcher=fetcher)
    assert result.hit is True and "hit" in result.summary


def test_load_extract_routes_a_non_vendored_anchor_through_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    """The seam ``types.load_extract`` fixed for #56: a cache populated by
    ``fetch`` is what the validator reads, through the environment alone."""
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    populate(name, declaration_for(name), fetcher=counting_fetcher(synthetic()))
    extract = load_extract(name)
    assert extract.operation("GET", "/v1/widgets/{anything}") is not None
    assert extract.metadata["sources"][0]["url"] == URL


def test_a_cache_that_does_not_match_the_pin_is_recut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    (cache / name).mkdir(parents=True)
    (cache / name / EXTRACT_FILE).write_text('{"openapi": "3.0.0", "stale": true}')
    fetcher = counting_fetcher(synthetic())
    result = populate(name, declaration_for(name), fetcher=fetcher)
    assert fetcher.calls == [URL] and result.hit is False
    assert "stale" not in (cache / name / EXTRACT_FILE).read_text()


# ---------------------------------------------------------------------------
# Drift: the vendor released.
# ---------------------------------------------------------------------------


def test_drift_still_cuts_from_the_fresh_bytes_writes_drift_and_says_so_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    pinned_sha = read_pin(tmp_path / "pkg" / name / PIN_FILE).sources[0].sha256
    moved = synthetic()
    moved["components"]["schemas"]["Money"]["properties"]["amount"]["type"] = "string"
    fresh_sha = sha256_hex(blob(moved))
    err = io.StringIO()
    result = populate(name, declaration_for(name), fetcher=counting_fetcher(moved), fetched="2026-12-25", stderr=err)
    assert result.hit is False and len(result.drift) == 1
    line = (
        f"UPSTREAM MOVED: {URL} pinned {pinned_sha[:12]} fetched {fresh_sha[:12]} "
        f"-- the pin is stale; tests run against the fresh document"
    )
    assert err.getvalue() == line + "\n"
    # The cut is the fresh document's, dated today, and it is what the validator reads.
    cached = json.loads((cache / name / EXTRACT_FILE).read_text())
    assert cached["components"]["schemas"]["Money"]["properties"]["amount"]["type"] == "string"
    row = {key: value for key, value in cached["x-vendorfake"]["sources"][0].items() if key != "label"}
    assert row == {
        "url": URL,
        "sha256": fresh_sha,
        "bytes": len(blob(moved)),
        "version": "2.3.4",
        "fetched": "2026-12-25",
    }
    drift = json.loads((cache / name / DRIFT_FILE).read_text())
    assert drift["rows"] == [
        {"url": URL, "pinned_sha256": pinned_sha, "fetched_sha256": fresh_sha, "fetched_bytes": len(blob(moved))}
    ]
    assert [row.line for row in drift_rows(cache / name)] == [line]
    assert "UPSTREAM MOVED" in result.summary
    # Nothing under the package moved.
    assert not (tmp_path / "pkg" / name / EXTRACT_FILE).exists()
    assert read_pin(tmp_path / "pkg" / name / PIN_FILE).sources[0].sha256 == pinned_sha


def test_drift_is_recut_on_every_call_until_repinned_and_settles_once_the_pin_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    moved = synthetic()
    moved["info"]["version"] = "2.4.0"
    fetcher = counting_fetcher(moved)
    populate(name, declaration_for(name), fetcher=fetcher, fetched="2026-12-25", stderr=io.StringIO())
    populate(name, declaration_for(name), fetcher=fetcher, fetched="2026-12-25", stderr=io.StringIO())
    assert fetcher.calls == [URL, URL]
    assert (cache / name / DRIFT_FILE).exists()
    # Re-pin against the moved document: the drift note is settled and the next call is a hit.
    write_pin(tmp_path / "pkg" / name / PIN_FILE, pin_for(moved))
    result = populate(name, declaration_for(name), fetcher=fetcher, stderr=io.StringIO())
    assert result.hit is False and result.drift == ()
    assert not (cache / name / DRIFT_FILE).exists()
    assert populate(name, declaration_for(name), fetcher=fetcher).hit is True
    assert fetcher.calls == [URL, URL, URL]


def test_a_source_the_pin_does_not_know_is_drift_with_no_pinned_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    pin = pin_for(synthetic())
    name = make_package(
        tmp_path, monkeypatch, pin=Pin(sources=(), extract_sha256=pin.extract_sha256, modeled=pin.modeled)
    )
    err = io.StringIO()
    result = populate(name, declaration_for(name), fetcher=counting_fetcher(synthetic()), stderr=err)
    assert result.drift[0].pinned_sha256 is None
    assert err.getvalue().startswith(f"UPSTREAM MOVED: {URL} pinned none fetched ")


def test_matching_bytes_whose_cut_no_longer_digests_to_the_pin_is_reported_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    """The cutter (or the modeled list) moved, not the vendor: the cut is used,
    the summary says so, and ``pin`` is the fix."""
    pin = pin_for(synthetic())
    fewer = tuple(key for key in pin.modeled if not key.startswith("PUT"))
    name = make_package(tmp_path, monkeypatch, pin=Pin(pin.sources, pin.extract_sha256, fewer))
    err = io.StringIO()
    result = populate(name, declaration_for(name), fetcher=counting_fetcher(synthetic()), stderr=err)
    assert result.drift == () and result.extract_differs is True
    assert "EXTRACT DIFFERS" in err.getvalue() and "re-run `vendorfake-fidelity pin`" in err.getvalue()
    assert not (cache / name / DRIFT_FILE).exists()


# ---------------------------------------------------------------------------
# The two LookupErrors.
# ---------------------------------------------------------------------------


def test_no_pin_is_a_lookup_error_naming_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    name = make_package(tmp_path, monkeypatch, pin=None)
    fetcher = counting_fetcher(synthetic())
    with pytest.raises(LookupError, match=r"no pin\.json .* run `vendorfake-fidelity pin --target"):
        cached_extract(name, declaration_for(name), fetcher=fetcher)
    assert fetcher.calls == []


def test_a_pin_without_modeled_routes_cannot_be_recut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    pin = pin_for(synthetic())
    name = make_package(tmp_path, monkeypatch, pin=Pin(pin.sources, pin.extract_sha256))
    with pytest.raises(LookupError, match="carries no modeled routes"):
        cached_extract(name, declaration_for(name), fetcher=counting_fetcher(synthetic()))


def test_no_network_and_no_cache_is_a_lookup_error_naming_the_path_and_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    fetcher = counting_fetcher(None, error=ConnectionError("no route to host"))
    with pytest.raises(LookupError) as raised:
        cached_extract(name, declaration_for(name), fetcher=fetcher)
    message = str(raised.value)
    assert URL in message and str(cache / name / EXTRACT_FILE) in message and "no route to host" in message
    assert not (cache / name).exists()


def test_no_network_with_a_stale_cache_uses_it_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    """Unspecified by the brief; chosen so a re-pin on a plane does not turn
    the whole suite into one LookupError when yesterday's cut is right there."""
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    populate(name, declaration_for(name), fetcher=counting_fetcher(synthetic()))
    moved = synthetic()
    moved["info"]["version"] = "2.4.0"
    write_pin(tmp_path / "pkg" / name / PIN_FILE, pin_for(moved))
    err = io.StringIO()
    fetcher = counting_fetcher(None, error=ConnectionError("offline"))
    result = populate(name, declaration_for(name), fetcher=fetcher, stderr=err)
    assert fetcher.calls == [URL]
    assert result.hit is False and result.extract_differs is True
    assert err.getvalue().startswith(f"OFFLINE: cannot fetch {URL} (offline); using the cached extract")


# ---------------------------------------------------------------------------
# The cache dir the argument names beats the environment.
# ---------------------------------------------------------------------------


def test_an_explicit_cache_dir_beats_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path
) -> None:
    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    elsewhere = tmp_path / "elsewhere"
    cached_extract(name, declaration_for(name), fetcher=counting_fetcher(synthetic()), cache_dir=elsewhere)
    assert (elsewhere / name / EXTRACT_FILE).is_file()
    assert not cache.exists()


# ---------------------------------------------------------------------------
# refresh on a non-vendored declaration writes the extract to the cache.
# ---------------------------------------------------------------------------


def test_refresh_of_a_non_vendored_declaration_writes_only_the_pin_under_the_package(
    tmp_path: Path, cache: Path
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "declaration.json").write_text("{}")
    declaration = declaration_for("some.anchor")
    result = refresh(package, declaration, MODELED, fetcher=counting_fetcher(synthetic()), fetched=FETCHED)
    assert result.changed
    assert sorted(p.name for p in package.iterdir()) == ["declaration.json", PIN_FILE]
    cached = cache / "some.anchor" / EXTRACT_FILE
    assert cached.is_file()
    pin = read_pin(package / PIN_FILE)
    assert pin.extract_sha256 == sha256_hex(cached.read_bytes())
    assert pin.modeled == ("POST /v1/widgets", "GET /v1/widgets/{id}", "PUT /v1/widgets/{id}")
    assert json.loads((package / PIN_FILE).read_text())["modeled"] == list(pin.modeled)
    assert (
        f"wrote {PIN_FILE} beside the declaration and {EXTRACT_FILE} to {cache / 'some.anchor'}" in result.diff_summary
    )


# -- the prose rule, mechanically ------------------------------------------


def test_a_case_note_that_repeats_the_vendor_document_is_a_leak() -> None:
    from vendorfake.fidelity.cache import prose_leaks

    document = b"description: The tax rate is expressed as a decimal value, for example 0.0625 for six and a quarter percent.\n"
    own_words = {"corpus/a.json": '{"note": "the rate 0.0625 is what the page gives for 6.25 percent"}'}
    copied = {"corpus/b.json": '{"note": "The tax rate is expressed as a decimal value, for example 0.0625 for six"}'}
    urls = {"corpus/c.json": '{"url": "https://doc.example.test/the/tax/rate/is/expressed/as/a/decimal"}'}
    assert prose_leaks(own_words, [document]) == {}
    assert prose_leaks(urls, [document]) == {}
    leak = prose_leaks(copied, [document])
    assert list(leak) == ["corpus/b.json"]
    assert any(window.startswith("the tax rate is expressed as a decimal") for window in leak["corpus/b.json"])


def test_a_cache_inside_the_package_is_refused(tmp_path: Path) -> None:
    """Adversarial A8 (konyklabs/roadmap#56): the cache must never be the
    package directory, where `git add -A` would sweep the vendor's document in."""
    from importlib import resources

    from vendorfake.fidelity.cache import cache_path

    package = Path(str(resources.files("vendorfake.fidelity")))
    with pytest.raises(LookupError, match="must not be inside or above the package"):
        cache_path("vendorfake.fidelity", cache_dir=package)
    with pytest.raises(LookupError, match="must not be inside or above the package"):
        cache_path("vendorfake.fidelity", cache_dir=package.parent.parent)
    assert cache_path("vendorfake.fidelity", cache_dir=tmp_path) == tmp_path / "vendorfake.fidelity"


# ---------------------------------------------------------------------------
# T1 (konyklabs/roadmap#116): the `fetch` CLI's own exit code for this case.
# ---------------------------------------------------------------------------

_FETCH_TARGET_ANCHOR = ""
"""Set per test by monkeypatch; :func:`_fetch_target` reads it fresh, the same
trick ``tests/unit/fidelity/test_cli.py``'s own ``target()`` uses, so the
CLI's ``module:attr`` resolution reaches a package this test wrote."""


def _fetch_target() -> Any:
    """A :class:`~vendorfake.fidelity.runner.FidelityTarget` the ``fetch``
    subcommand can resolve without ever opening a unit -- ``_fetch`` never
    calls ``open_unit``, so a call here would mean the CLI took a wrong turn."""
    from contextlib import contextmanager

    from vendorfake.fidelity.runner import FidelityTarget

    @contextmanager
    def open_unit(profile: str | None) -> Any:
        raise AssertionError("`fetch` must never open a unit")
        yield  # pragma: no cover

    return FidelityTarget(name=_FETCH_TARGET_ANCHOR, anchor=_FETCH_TARGET_ANCHOR, open_unit=open_unit)


def test_fetch_with_no_network_and_no_cache_is_a_named_skip_not_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T1 (konyklabs/roadmap#116): the ``fetch`` CLI turns "no network, no
    cache" into exit 3 -- a named skip ``tools/self-test.sh`` reports as SKIP
    rather than failing the whole run -- never the generic usage-error exit 2
    a bad ``--target`` or a pin-less anchor still get (see
    ``populate``'s ``Unavailable``, which this is the CLI's own view of)."""
    from vendorfake.fidelity.__main__ import main

    name = make_package(tmp_path, monkeypatch, pin=pin_for(synthetic()))
    monkeypatch.setattr(f"{__name__}._FETCH_TARGET_ANCHOR", name)
    offline = counting_fetcher(None, error=ConnectionError("unreachable"))
    assert main(["fetch", "--target", f"{__name__}:_fetch_target"], fetcher=offline) == 3
    assert offline.calls == [URL]
    err = capsys.readouterr().err
    assert f"fidelity fetch: {name} UNAVAILABLE" in err
    assert "its fidelity leg is skipped in this run" in err
