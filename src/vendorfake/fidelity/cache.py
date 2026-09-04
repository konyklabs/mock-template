"""The fetch-never-commit path: a non-vendored vendor's extract, cut at run time.

FOR: konyklabs/roadmap#56. A vendor whose terms do not permit a copy of its
specification in a public repository declares ``vendored: false``; its
``pin.json`` still ships (sha256, size, version, fetch date of each upstream
file -- facts, not copies -- plus the modeled route list the cut was scoped
to) and its extract is cut here from a fresh fetch into a local cache
directory that is never inside the repository.

INVARIANT: **no upstream byte is written under the package.** The cache lives
under ``$VENDORFAKE_FIDELITY_CACHE``, else ``$XDG_CACHE_HOME/vendorfake/fidelity``,
else ``~/.cache/vendorfake/fidelity``, keyed by the anchor and the pin: the
cached ``extract.json`` is used only while its sha256 is the pin's
``extract_sha256``, and anything else is re-fetched and re-cut.

SECOND INVARIANT: **a vendor release never fails a test run.** When a fetched
document no longer matches its pinned row, the extract is still cut from the
fresh bytes and cached; the discrepancy is written to ``DRIFT`` beside it and
one ``UPSTREAM MOVED`` line goes to stderr. ``pin --check`` is what fails on
drift, on purpose, in the one place a failure is actionable.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from vendorfake.fidelity.extract import cut_extract, render_json, sha256_hex
from vendorfake.fidelity.pin import Fetcher, Pin, fetch
from vendorfake.fidelity.types import CORPUS_DIR, DECLARATION_FILE, EXTRACT_FILE, PIN_FILE, Extract, FidelityDeclaration

__all__ = [
    "CACHE_ENV_VAR",
    "DRIFT_FILE",
    "CacheResult",
    "DriftRow",
    "ProseLeak",
    "cache_path",
    "cache_root",
    "cached_extract",
    "drift_rows",
    "populate",
    "prose_leaks",
    "read_package_pin",
]

CACHE_ENV_VAR = "VENDORFAKE_FIDELITY_CACHE"
"""Overrides the cache root wholesale: ``$VENDORFAKE_FIDELITY_CACHE/<anchor>/extract.json``."""

DRIFT_FILE = "DRIFT"
"""Written beside a cached extract that was cut from bytes the pin does not describe."""

_DRIFT_SCHEMA = 1


def cache_root(cache_dir: Path | str | None = None) -> Path:
    """Where every non-vendored vendor's cut lands, one subdirectory per anchor.

    Precedence: an explicit ``cache_dir``, then ``$VENDORFAKE_FIDELITY_CACHE``,
    then ``$XDG_CACHE_HOME/vendorfake/fidelity``, then ``~/.cache/vendorfake/fidelity``.
    """
    if cache_dir is not None:
        return Path(cache_dir)
    explicit = os.environ.get(CACHE_ENV_VAR)
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "vendorfake" / "fidelity"


def _refuse_inside_package(anchor: str, root: Path) -> None:
    """A cache under the package would put the vendor's document beside the
    declaration, where `git add -A` could sweep it into a public commit --
    the one thing the fetch-never-commit rule exists to prevent."""
    try:
        package = Path(str(resources.files(anchor))).resolve()
        resolved = root.resolve()
    except (ModuleNotFoundError, OSError):
        return
    if resolved == package or package in resolved.parents or resolved in package.parents:
        raise LookupError(
            f"{anchor}: the fidelity cache ({root}) must not be inside or above the package ({package}); "
            f"no upstream byte may land in the repository"
        )


def cache_path(anchor: str, cache_dir: Path | str | None = None) -> Path:
    """The directory holding ``extract.json`` (and, on drift, ``DRIFT``) for ``anchor``."""
    root = cache_root(cache_dir)
    _refuse_inside_package(anchor, root)
    return root / anchor


def read_package_pin(anchor: str) -> Pin:
    """The committed ``pin.json`` of ``anchor``, or a ``LookupError`` that says what to run."""
    try:
        text = (resources.files(anchor) / PIN_FILE).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise LookupError(
            f"{anchor}: no {PIN_FILE} in the package -- run `vendorfake-fidelity pin --target MODULE:ATTR` once "
            f"(the target that publishes anchor {anchor!r}) to pin the upstream documents ({exc})"
        ) from exc
    return Pin.of(json.loads(text))


@dataclass(frozen=True, slots=True)
class DriftRow:
    """One upstream document whose fetched bytes are not the pinned ones."""

    url: str
    pinned_sha256: str | None
    fetched_sha256: str
    fetched_bytes: int

    @property
    def line(self) -> str:
        """The one stderr line; its wording is a contract with whoever greps CI logs."""
        pinned = self.pinned_sha256[:12] if self.pinned_sha256 else "none"
        return (
            f"UPSTREAM MOVED: {self.url} pinned {pinned} fetched {self.fetched_sha256[:12]} "
            f"-- the pin is stale; tests run against the fresh document"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "pinned_sha256": self.pinned_sha256,
            "fetched_sha256": self.fetched_sha256,
            "fetched_bytes": self.fetched_bytes,
        }


@dataclass(frozen=True, slots=True)
class CacheResult:
    """What :func:`populate` did. ``hit`` means nothing was fetched."""

    anchor: str
    path: Path
    document: Any
    hit: bool
    drift: tuple[DriftRow, ...] = ()
    #: Set when the fresh bytes matched every pinned row but the cut did
    #: not digest to the pin's ``extract_sha256``: the cutter or the modeled
    #: list moved, not the upstream. ``pin`` is the fix; the cut is used anyway.
    extract_differs: bool = False
    #: Nothing was fetched: the network was unreachable and a cache existed.
    offline: bool = False

    @property
    def extract_path(self) -> Path:
        return self.path / EXTRACT_FILE

    @property
    def summary(self) -> str:
        if self.offline:
            return (
                f"fidelity cache: OFFLINE -- nothing fetched; using the cached extract at {self.extract_path}, "
                f"which does not match {PIN_FILE}"
            )
        if self.hit:
            return f"fidelity cache: hit {self.extract_path} (matches {PIN_FILE})"
        if self.drift:
            return f"fidelity cache: fetched and cut into {self.extract_path}; UPSTREAM MOVED ({len(self.drift)} source(s))"
        if self.extract_differs:
            return f"fidelity cache: fetched and cut into {self.extract_path}; the cut does not digest to {PIN_FILE}"
        return f"fidelity cache: fetched and cut into {self.extract_path} (matches {PIN_FILE})"


def _cached_text(path: Path) -> str | None:
    extract_path = path / EXTRACT_FILE
    if not extract_path.is_file():
        return None
    return extract_path.read_text(encoding="utf-8")


def _modeled(anchor: str, pin: Pin) -> list[tuple[str, str]]:
    if not pin.modeled:
        raise LookupError(
            f"{anchor}: {PIN_FILE} carries no modeled routes, so the extract cannot be re-cut -- "
            f"run `vendorfake-fidelity pin --target MODULE:ATTR` once with the current tool"
        )
    pairs: list[tuple[str, str]] = []
    for key in pin.modeled:
        method, _, spec_path = key.partition(" ")
        if not method or not spec_path:
            raise ValueError(f"{anchor}: {PIN_FILE} modeled entry {key!r} is not 'METHOD /path'")
        pairs.append((method, spec_path))
    return pairs


def _write_atomic(path: Path, text: str) -> None:
    """Write through a uniquely named temporary file in the same directory, so
    two processes cutting the same anchor at once cannot tear each other's
    bytes; the last ``os.replace`` wins whole."""
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


class ProseLeak(LookupError):
    """Repository text repeats the vendor's document: an eight-word window of
    a corpus case or the declaration also occurs in a fetched source file.

    The fetch-never-commit rule is about bytes, and a sentence copied out of
    a ``description`` into a case note is bytes. This is the mechanical half
    of the rule, run where the fresh bytes are at hand and nowhere else.
    """


_WINDOW = 8


def _prose_words(text: str) -> list[str]:
    """Words, lower-cased and stripped of punctuation, with URLs removed first:
    a citation of the vendor's page is not a copy of the vendor's prose."""
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"https?://\S+", " ", text).lower()).split()


def prose_leaks(texts: Mapping[str, str], documents: Sequence[bytes]) -> dict[str, list[str]]:
    """Which of ``texts`` (name -> content) share an eight-word window with any
    of ``documents``. URLs are not prose and are ignored."""
    corpus = " " + " ".join(" ".join(_prose_words(blob.decode("utf-8", "replace"))) for blob in documents) + " "
    leaks: dict[str, list[str]] = {}
    for name, text in texts.items():
        words = _prose_words(text)
        found = sorted(
            {
                window
                for i in range(len(words) - _WINDOW + 1)
                if (window := " ".join(words[i : i + _WINDOW])) and f" {window} " in corpus and "http" not in window
            }
        )
        if found:
            leaks[name] = found
    return leaks


def _package_prose(anchor: str) -> dict[str, str]:
    """Every text file shipped beside the declaration: the declaration and the corpus."""
    root = resources.files(anchor)
    out: dict[str, str] = {}
    for name in (DECLARATION_FILE,):
        with contextlib.suppress(FileNotFoundError, OSError):
            out[name] = (root / name).read_text(encoding="utf-8")
    corpus = root / CORPUS_DIR
    if corpus.is_dir():
        for entry in sorted(corpus.iterdir(), key=lambda e: e.name):
            if entry.name.endswith(".json"):
                out[f"{CORPUS_DIR}/{entry.name}"] = entry.read_text(encoding="utf-8")
    return out


def populate(
    anchor: str,
    declaration: FidelityDeclaration,
    *,
    fetcher: Fetcher | None = None,
    cache_dir: Path | str | None = None,
    fetched: str | None = None,
    stderr: Any = None,
) -> CacheResult:
    """Make ``<cache>/<anchor>/extract.json`` current, fetching only when it is not.

    ``fetched`` is the ISO date stamped on a source row whose bytes differ from
    the pinned ones (today by default; injectable so a test is reproducible).
    ``stderr`` is where the ``UPSTREAM MOVED`` line goes (``sys.stderr`` by
    default). Raises ``LookupError`` when there is no pin, or when the network
    is unreachable and nothing is cached.
    """
    out = sys.stderr if stderr is None else stderr
    path = cache_path(anchor, cache_dir)
    pin = read_package_pin(anchor)
    cached = _cached_text(path)
    if cached is not None and sha256_hex(cached.encode("utf-8")) == pin.extract_sha256:
        return CacheResult(anchor, path, json.loads(cached), hit=True)

    modeled = _modeled(anchor, pin)
    blobs = []
    for source in declaration.sources:
        try:
            blobs.append((source, fetch(source, fetcher=fetcher)))
        except (httpx.HTTPError, OSError) as exc:
            if cached is not None:
                print(
                    f"OFFLINE: cannot fetch {source.url} ({exc}); using the cached extract at {path / EXTRACT_FILE}, "
                    f"which does not match {PIN_FILE} -- re-run `vendorfake-fidelity fetch` when online",
                    file=out,
                )
                return CacheResult(anchor, path, json.loads(cached), hit=False, extract_differs=True, offline=True)
            raise LookupError(
                f"{anchor}: cannot fetch {source.url} ({exc}) and there is no cached extract at "
                f"{path / EXTRACT_FILE}; connect once, or set {CACHE_ENV_VAR} to a directory holding one"
            ) from exc

    leaks = prose_leaks(_package_prose(anchor), [blob for _, blob in blobs])

    if leaks:
        detail = "; ".join(f"{name}: {len(windows)} window(s), e.g. {windows[0]!r}" for name, windows in leaks.items())

        raise ProseLeak(f"{anchor}: repository text repeats the vendor's document -- {detail}")

    document = cut_extract(
        blobs,
        modeled,
        fetched=fetched or _dt.date.today().isoformat(),
        extension_map=declaration.extension_map,
        error_schema=declaration.error_schema,
    )
    drift: list[DriftRow] = []
    for row in document["x-vendorfake"]["sources"]:
        pinned = pin.source(row["url"])
        if pinned is not None and pinned.sha256 == row["sha256"]:
            row["fetched"] = pinned.fetched
            continue
        drift.append(DriftRow(row["url"], None if pinned is None else pinned.sha256, row["sha256"], row["bytes"]))

    text = render_json(document)
    path.mkdir(parents=True, exist_ok=True)
    _write_atomic(path / EXTRACT_FILE, text)
    drift_path = path / DRIFT_FILE
    if drift:
        for row in drift:
            print(row.line, file=out)
        _write_atomic(
            drift_path,
            render_json({"schema": _DRIFT_SCHEMA, "anchor": anchor, "rows": [row.to_json() for row in drift]}),
        )
    elif drift_path.exists():
        drift_path.unlink()
    differs = not drift and sha256_hex(text.encode("utf-8")) != pin.extract_sha256
    if differs:
        print(
            f"EXTRACT DIFFERS: {anchor} cut from bytes matching every pinned row does not digest to "
            f"{PIN_FILE}'s extract_sha256 -- the cutter or the modeled list moved; re-run `vendorfake-fidelity pin`",
            file=out,
        )
    return CacheResult(anchor, path, document, hit=False, drift=tuple(drift), extract_differs=differs)


def cached_extract(
    anchor: str,
    declaration: FidelityDeclaration,
    *,
    fetcher: Fetcher | None = None,
    cache_dir: Path | str | None = None,
) -> Extract:
    """The extract for a non-vendored vendor: from the cache when it matches
    the pin, otherwise fetched, cut, verified against the pin and cached."""
    return Extract(populate(anchor, declaration, fetcher=fetcher, cache_dir=cache_dir).document)


def drift_rows(path: Path) -> Sequence[DriftRow]:
    """The rows a ``DRIFT`` file records; empty when there is none."""
    drift_path = path / DRIFT_FILE
    if not drift_path.is_file():
        return ()
    doc = json.loads(drift_path.read_text(encoding="utf-8"))
    return tuple(
        DriftRow(str(row["url"]), row.get("pinned_sha256"), str(row["fetched_sha256"]), int(row["fetched_bytes"]))
        for row in doc.get("rows", ())
    )
