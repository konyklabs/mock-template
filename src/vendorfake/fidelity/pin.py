"""Tying the extract to the upstream bytes it was cut from.

FOR: making vendor drift a diff. ``pin.json`` records, per upstream document,
the sha256 of the exact bytes fetched, their length, the document's own
version string and the date they were fetched -- plus the sha256 of the
committed ``extract.json``. ``refresh`` fetches again, cuts again, and reports
whether the upstream changed, whether the extract changed, or both; in check
mode it writes nothing, so CI can fail on drift without ever touching the
tree.

INVARIANT: **the fetcher is a parameter.** Everything below the default
``fetch`` is testable with bytes in hand; no unit test reaches the network, and
the one real fetch per vendor is a deliberate act with a date attached.

SECOND INVARIANT: **an unchanged upstream refreshes to byte-identical files.**
The ``fetched`` date a caller passes applies only to sources whose bytes
differ from the pinned ones; a source with the same sha256 keeps the date it
was first pinned on, because that is still the date *these bytes* were
obtained. Without this, every check run on a new day would read as drift.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from vendorfake.fidelity.extract import cut_extract, render_json, sha256_hex
from vendorfake.fidelity.types import EXTRACT_FILE, PIN_FILE, FidelityDeclaration, SpecSource, route_key

__all__ = [
    "PIN_SCHEMA",
    "Fetcher",
    "Pin",
    "PinnedSource",
    "RefreshResult",
    "fetch",
    "read_pin",
    "refresh",
    "verify",
    "write_pin",
]

PIN_SCHEMA = 1

Fetcher = Callable[[str], bytes]
"""``url -> bytes``. The default is :func:`fetch`; tests pass a closure over a literal."""

_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class PinnedSource:
    """One row of ``x-vendorfake.sources`` / ``pin.json.sources``."""

    url: str
    sha256: str
    bytes: int
    version: str
    fetched: str

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> PinnedSource:
        return cls(
            url=str(row["url"]),
            sha256=str(row["sha256"]),
            bytes=int(row["bytes"]),
            version=str(row.get("version", "")),
            fetched=str(row.get("fetched", "")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "version": self.version,
            "fetched": self.fetched,
        }


@dataclass(frozen=True, slots=True)
class Pin:
    """``pin.json``: the upstream rows and the extract's own digest."""

    sources: tuple[PinnedSource, ...]
    extract_sha256: str
    #: The ``METHOD /path`` keys the extract was scoped to -- the *input* of
    #: :func:`cut_extract`, kept so a non-vendored vendor's cut can be
    #: reproduced at run time from the pin alone (konyklabs/roadmap#56). A
    #: vendored pin leaves it empty: the committed extract already says.
    modeled: tuple[str, ...] = ()

    @classmethod
    def of(cls, doc: Mapping[str, Any]) -> Pin:
        if int(doc.get("schema", 0)) != PIN_SCHEMA:
            raise ValueError(f'{PIN_FILE}: expected "schema": {PIN_SCHEMA}, got {doc.get("schema")!r}')
        return cls(
            sources=tuple(PinnedSource.of(row) for row in doc.get("sources", ())),
            extract_sha256=str(doc["extract_sha256"]),
            modeled=tuple(str(key) for key in doc.get("modeled", ())),
        )

    @classmethod
    def from_extract(
        cls, document: Mapping[str, Any], extract_text: str, *, modeled: Sequence[tuple[str, str]] = ()
    ) -> Pin:
        """The pin for an extract as :func:`~vendorfake.fidelity.extract.render_json` rendered it."""
        meta = document.get("x-vendorfake", {})
        rows = meta.get("sources", ()) if isinstance(meta, Mapping) else ()
        return cls(
            sources=tuple(PinnedSource.of(row) for row in rows),
            extract_sha256=sha256_hex(extract_text.encode("utf-8")),
            modeled=tuple(route_key(method, spec_path) for method, spec_path in modeled),
        )

    def to_json(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "schema": PIN_SCHEMA,
            "sources": [row.to_json() for row in self.sources],
            "extract_sha256": self.extract_sha256,
        }
        if self.modeled:
            doc["modeled"] = list(self.modeled)
        return doc

    def source(self, url: str) -> PinnedSource | None:
        for row in self.sources:
            if row.url == url:
                return row
        return None


def write_pin(path: Path, pin: Pin) -> None:
    path.write_text(render_json(pin.to_json()), encoding="utf-8")


def read_pin(path: Path) -> Pin:
    return Pin.of(json.loads(path.read_text(encoding="utf-8")))


def fetch(source: SpecSource, *, fetcher: Fetcher | None = None) -> bytes:
    """The upstream bytes, exactly as served. ``fetcher`` replaces the network."""
    if fetcher is not None:
        return fetcher(source.url)
    response = httpx.get(source.url, follow_redirects=True, timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """What one refresh found. ``diff_summary`` is for a terminal; the booleans are for an exit code."""

    changed_upstream: bool
    changed_extract: bool
    diff_summary: str

    @property
    def changed(self) -> bool:
        return self.changed_upstream or self.changed_extract


def refresh(
    anchor_dir: Path,
    declaration: FidelityDeclaration,
    modeled: Sequence[tuple[str, str]],
    *,
    fetcher: Fetcher | None = None,
    check: bool = False,
    fetched: str,
    cache_dir: Path | str | None = None,
) -> RefreshResult:
    """Fetch every declared source, cut the extract, compare with what is
    committed under ``anchor_dir`` and -- unless ``check`` -- write the new
    ``extract.json`` and ``pin.json`` there.

    ``anchor_dir`` is the directory holding ``declaration.json`` (for a built-in
    vendor, ``importlib.resources.files(anchor)``). ``modeled`` is the
    ``(METHOD, spec_path)`` list with aliases applied, as :func:`cut_extract`
    takes it. ``fetched`` is today's ISO date, applied per the second invariant.

    A non-vendored declaration (``vendored: false``) writes ``pin.json`` beside
    the declaration as always, but the extract goes to the cache directory
    (``cache_dir``, else the resolution in :mod:`vendorfake.fidelity.cache`)
    and never under the package; the pin then also carries ``modeled`` so the
    cut can be reproduced from the pin alone.
    """
    from vendorfake.fidelity.cache import DRIFT_FILE, cache_path

    extract_dir = anchor_dir if declaration.vendored else cache_path(declaration.anchor, cache_dir)
    extract_path = extract_dir / EXTRACT_FILE
    pin_path = anchor_dir / PIN_FILE
    old_pin = read_pin(pin_path) if pin_path.exists() else None
    old_text = extract_path.read_text(encoding="utf-8") if extract_path.exists() else None

    blobs = [(source, fetch(source, fetcher=fetcher)) for source in declaration.sources]
    document = cut_extract(
        blobs,
        modeled,
        fetched=fetched,
        extension_map=declaration.extension_map,
        error_schema=declaration.error_schema,
        annotations=declaration.annotations,
    )

    lines: list[str] = []
    changed_upstream = False
    for row in document["x-vendorfake"]["sources"]:
        pinned = old_pin.source(row["url"]) if old_pin is not None else None
        if pinned is not None and pinned.sha256 == row["sha256"]:
            row["fetched"] = pinned.fetched
            lines.append(f"upstream {row['url']}: unchanged (sha256 {row['sha256'][:12]}, fetched {pinned.fetched})")
            continue
        changed_upstream = True
        if pinned is None:
            lines.append(
                f"upstream {row['url']}: new pin (sha256 {row['sha256'][:12]}, {row['bytes']} bytes, "
                f"version {row['version']!r}, fetched {row['fetched']})"
            )
        else:
            lines.append(
                f"upstream {row['url']}: changed sha256 {pinned.sha256[:12]} -> {row['sha256'][:12]}, "
                f"{pinned.bytes} -> {row['bytes']} bytes, version {pinned.version!r} -> {row['version']!r}"
            )
    if old_pin is not None:
        for pinned in old_pin.sources:
            if not any(row["url"] == pinned.url for row in document["x-vendorfake"]["sources"]):
                changed_upstream = True
                lines.append(f"upstream {pinned.url}: no longer declared")

    new_text = render_json(document)
    new_pin = Pin.from_extract(document, new_text, modeled=() if declaration.vendored else modeled)

    # "The extract changed" means what the validator sees changed -- operations
    # and schemas -- not that the embedded source row moved with the upstream.
    # An upstream prose edit is therefore reported as changed_upstream alone,
    # which is the useful distinction: the pin moves, nothing validated moves.
    changed_extract = old_text is None or _content(json.loads(old_text)) != _content(document)
    if old_text is None:
        lines.append(f"{EXTRACT_FILE}: new ({len(new_text.encode('utf-8'))} bytes)")
    elif changed_extract:
        lines.extend(_extract_diff(json.loads(old_text), document, old_text, new_text))
    else:
        lines.append(f"{EXTRACT_FILE}: unchanged")
    if old_pin is not None and old_text is not None and sha256_hex(old_text.encode("utf-8")) != old_pin.extract_sha256:
        changed_extract = True
        lines.append(f"{PIN_FILE}: extract_sha256 does not match the committed {EXTRACT_FILE} (edited by hand?)")
    elif old_pin is not None and old_pin != new_pin and not changed_upstream and not changed_extract:
        # Same bytes and same extract can only disagree with the pin if the pin was edited by hand.
        changed_upstream = True
        lines.append(f"{PIN_FILE}: rows differ from what the committed {EXTRACT_FILE} says (edited by hand?)")

    if not check and (changed_upstream or changed_extract):
        extract_dir.mkdir(parents=True, exist_ok=True)
        extract_path.write_text(new_text, encoding="utf-8")
        write_pin(pin_path, new_pin)
        if declaration.vendored:
            lines.append(f"wrote {extract_path.name} and {pin_path.name}")
        else:
            # The pin now describes these bytes; a DRIFT note from an earlier run-time cut is settled.
            (extract_dir / DRIFT_FILE).unlink(missing_ok=True)
            lines.append(f"wrote {pin_path.name} beside the declaration and {extract_path.name} to {extract_dir}")
    elif check:
        lines.append("check mode: nothing written")

    return RefreshResult(
        changed_upstream=changed_upstream,
        changed_extract=changed_extract,
        diff_summary="\n".join(lines),
    )


def _content(document: Mapping[str, Any]) -> dict[str, Any]:
    """The extract minus its source rows: what is compared to decide ``changed_extract``."""
    content = dict(document)
    meta = content.get("x-vendorfake")
    if isinstance(meta, Mapping):
        content["x-vendorfake"] = {key: value for key, value in meta.items() if key != "sources"}
    return content


def _extract_diff(old: Mapping[str, Any], new: Mapping[str, Any], old_text: str, new_text: str) -> list[str]:
    """A structural summary of an extract change, then the unified-diff line counts."""
    out: list[str] = []
    old_meta = old.get("x-vendorfake", {}) if isinstance(old.get("x-vendorfake"), Mapping) else {}
    new_meta = new.get("x-vendorfake", {})
    for field in ("modeled", "missing", "stubbed"):
        before = set(old_meta.get(field, ()))
        after = set(new_meta.get(field, ()))
        for key in sorted(after - before):
            out.append(f"{EXTRACT_FILE}: {field} + {key}")
        for key in sorted(before - after):
            out.append(f"{EXTRACT_FILE}: {field} - {key}")
    old_components = old.get("components", {})
    old_schemas = old_components.get("schemas", {}) if isinstance(old_components, Mapping) else {}
    new_schemas = new.get("components", {}).get("schemas", {})
    for name in sorted(set(new_schemas) | set(old_schemas)):
        if name not in old_schemas:
            out.append(f"{EXTRACT_FILE}: schema + {name}")
        elif name not in new_schemas:
            out.append(f"{EXTRACT_FILE}: schema - {name}")
        elif old_schemas[name] != new_schemas[name]:
            out.append(f"{EXTRACT_FILE}: schema ~ {name}")
    old_paths = old.get("paths", {}) if isinstance(old.get("paths"), Mapping) else {}
    for path in sorted(set(new["paths"]) | set(old_paths)):
        if path in old_paths and path in new["paths"] and old_paths[path] != new["paths"][path]:
            out.append(f"{EXTRACT_FILE}: operation ~ {path}")
    added = removed = 0
    for line in difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    out.append(f"{EXTRACT_FILE}: changed (+{added} -{removed} lines)")
    return out


def verify(anchor_dir: Path, declaration: FidelityDeclaration, *, cache_dir: Path | str | None = None) -> RefreshResult:
    """The offline half of ``refresh``: is what is committed self-consistent?

    No network. This is what a pull-request run asks -- the extract on disk
    is the one the pin describes, and the pin describes the sources the
    declaration names -- and nothing more. Whether *upstream* has moved is a
    scheduled question (D-006: drift is a filed issue, not a red PR -- for a
    vendored extract; a non-vendored one validates against the cached cut and,
    once that is evicted, the fresh document), and a
    check that fetched here would make every vendor release fail every open
    pull request at once.

    For a non-vendored declaration the extract on disk is the cached one, and
    an empty cache is a pass with a note: offline cannot fetch, and the design
    accepts that. A cache cut from moved upstream (a ``DRIFT`` file beside it)
    is reported as upstream change, since the pin does not describe it.
    """
    from vendorfake.fidelity.cache import DRIFT_FILE, cache_path, drift_rows

    lines: list[str] = []
    changed_extract = False
    changed_upstream = False
    pin_path = anchor_dir / PIN_FILE
    if declaration.vendored:
        extract_path = anchor_dir / EXTRACT_FILE
    else:
        extract_dir = cache_path(declaration.anchor, cache_dir)
        extract_path = extract_dir / EXTRACT_FILE
        if not pin_path.is_file():
            return RefreshResult(True, True, f"missing under {anchor_dir}: {PIN_FILE} -- run `pin` once")
        if not extract_path.is_file():
            return RefreshResult(
                # Nothing checked is not a pass: the pin's own claims (the
                # extract digest, the source rows) are only verifiable
                # against a cut, and CI populates one before this runs.
                True,
                False,
                f"no cache at {extract_path}: the pin cannot be verified offline -- run `vendorfake-fidelity fetch` first",
            )
        for drifted in drift_rows(extract_dir):
            changed_upstream = True
            lines.append(f"{DRIFT_FILE} in {extract_dir}: {drifted.line}")
    if not extract_path.is_file() or not pin_path.is_file():
        missing = [name for name, path in ((EXTRACT_FILE, extract_path), (PIN_FILE, pin_path)) if not path.is_file()]
        return RefreshResult(True, True, f"missing under {anchor_dir}: {', '.join(missing)} -- run `pin` once")
    pin = read_pin(pin_path)
    text = extract_path.read_text(encoding="utf-8")
    actual = sha256_hex(text.encode("utf-8"))
    if actual != pin.extract_sha256:
        changed_extract = True
        lines.append(
            f"{EXTRACT_FILE}: sha256 {actual[:12]} does not match {PIN_FILE}'s {pin.extract_sha256[:12]} (edited by hand?)"
        )
    document = json.loads(text)
    embedded = Pin.from_extract(document, text)
    if embedded.sources != pin.sources:
        changed_extract = True
        lines.append(f"{EXTRACT_FILE}: its x-vendorfake.sources rows disagree with {PIN_FILE}")
    meta = document.get("x-vendorfake", {})
    stubbed = tuple(meta.get("stubbed", ())) if isinstance(meta, Mapping) else ()
    for name in sorted(set(stubbed) - set(declaration.stubs_accepted)):
        changed_extract = True
        lines.append(
            f"{EXTRACT_FILE}: schema {name!r} is stubbed to {{}} (upstream dangles) and not in stubs_accepted -- "
            f"everything it types is unvalidated until the declaration accepts it"
        )
    declared = {source.url for source in declaration.sources}
    pinned = {row.url for row in pin.sources}
    for url in sorted(declared - pinned):
        changed_upstream = True
        lines.append(f"declared source not pinned: {url}")
    for url in sorted(pinned - declared):
        changed_upstream = True
        lines.append(f"pinned source no longer declared: {url}")
    if not lines:
        for row in pin.sources:
            lines.append(
                f"upstream {row.url}: pinned sha256 {row.sha256[:12]}, fetched {row.fetched} (offline; not re-fetched)"
            )
        where = "" if declaration.vendored else f" (cached at {extract_path})"
        lines.append(f"{EXTRACT_FILE}: matches {PIN_FILE}{where}")
    return RefreshResult(changed_upstream, changed_extract, "\n".join(lines))
