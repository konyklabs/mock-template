"""Webhook goldens: one captured delivery, and whether the signer reproduces its headers.

FOR: the fidelity question the corpus cannot ask. A case sends a request and reads a response; a webhook goes the other way, so nothing in ``corpus/`` can say "the vendor signed *these* bytes for *this* URL with *this* key and got *this* header". A golden says exactly that, as data.

INVARIANT: **a golden is compared against the signer, never against another golden.** :func:`verify_golden` hands the vendor's own ``Signer.sign`` the same three inputs and compares the headers the golden names, case insensitively, so a divergence is a statement about the *scheme* and about nothing this package chose.

WHAT A GOLDEN IS NOT. It is not a recording unless it says so: ``source`` is the provenance block a case carries, checked against the same schema, so a stub's output is ``judgment`` with a note naming the stub and a real capture carries the five ``recorded`` fields or is refused. A fabricated recording would be worse than none, the whole point being that the bytes are evidence.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import jsonschema

from vendorfake.core.kernel.types import PreparedEvent, SignInput
from vendorfake.fidelity.corpus import Source, load_schema

__all__ = [
    "GOLDEN_SCHEMA",
    "Delivery",
    "Divergence",
    "Golden",
    "GoldenError",
    "GoldenResult",
    "Signer",
    "format_goldens",
    "load_goldens",
    "run_goldens",
    "verify_golden",
]

GOLDEN_SCHEMA = "vendorfake.webhook-golden/1"
"""The ``schema`` every golden document carries. A later shape gets a later number."""

Signer = Callable[[SignInput], Mapping[str, str]]
"""``Signer.sign`` from ``core/kernel/types.py``, bound to a vendor's signer."""

_SOURCE_CACHE: list[jsonschema.Draft202012Validator] = []


class GoldenError(ValueError):
    """A golden document this package refuses to run: malformed, or not evidence."""


def _source_validator() -> jsonschema.Draft202012Validator:
    """The corpus schema's own ``source``: ``recorded`` means the same five things here as for a case."""
    if not _SOURCE_CACHE:
        schema = load_schema().schema
        assert isinstance(schema, Mapping)
        _SOURCE_CACHE.append(jsonschema.Draft202012Validator(dict(schema)["$defs"]["source"]))
    return _SOURCE_CACHE[0]


# ---------------------------------------------------------------------------
# The document, read.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Delivery:
    """The captured POST: where it went, what it carried, what it was signed with."""

    url: str
    headers: Mapping[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


@dataclass(frozen=True, slots=True)
class Golden:
    """One delivery and the claim that a signer reproduces its signature headers."""

    vendor: str
    source: Source
    secret: str
    delivery: Delivery
    signature_headers: tuple[str, ...]
    attempt: int = 1
    event: PreparedEvent | None = None

    @classmethod
    def of(cls, doc: object, *, where: str = "<golden>") -> Golden:
        if not isinstance(doc, Mapping):
            raise GoldenError(f"{where}: a golden is an object, not {type(doc).__name__}")
        schema = doc.get("schema")
        if schema != GOLDEN_SCHEMA:
            raise GoldenError(f"{where}: schema is {schema!r}, expected {GOLDEN_SCHEMA!r}")
        for required in ("vendor", "source", "secret", "delivery", "signature_headers"):
            if required not in doc:
                raise GoldenError(f"{where}: no {required}")
        errors = sorted(_source_validator().iter_errors(doc["source"]), key=lambda e: list(e.absolute_path))
        if errors:
            lines = [f"{where}: source is not a valid provenance block ({len(errors)} problem(s)):"]
            lines.extend(
                f"  /source{'/' + '/'.join(str(p) for p in e.absolute_path) if e.absolute_path else ''}: {e.message}"
                for e in errors
            )
            raise GoldenError("\n".join(lines))
        delivery = _delivery(doc["delivery"], where=where)
        names = tuple(str(name) for name in doc["signature_headers"])
        if not names:
            raise GoldenError(f"{where}: signature_headers is empty; a golden with nothing to compare proves nothing")
        for name in names:
            if delivery.header(name) is None:
                raise GoldenError(f"{where}: signature_headers names {name!r}, which the delivery does not carry")
        return cls(
            vendor=str(doc["vendor"]),
            source=Source.of(doc["source"]),
            secret=str(doc["secret"]),
            delivery=delivery,
            signature_headers=names,
            attempt=int(doc.get("attempt", 1)),
            event=_event(doc.get("event"), delivery, where=where),
        )

    def sign_input(self) -> SignInput:
        """The three inputs the recording had, in the shape a signer takes."""
        return SignInput(
            notification_url=self.delivery.url,
            raw_body=self.delivery.body,
            secret=self.secret,
            attempt=self.attempt,
            event=self.event if self.event is not None else _blank_event(self.delivery),
        )


def _delivery(row: object, *, where: str) -> Delivery:
    if not isinstance(row, Mapping):
        raise GoldenError(f"{where}: delivery is an object, not {type(row).__name__}")
    if "url" not in row:
        raise GoldenError(f"{where}: delivery has no url; a signature is bound to where it was sent")
    has_text, has_b64 = "body" in row, "body_b64" in row
    if has_text == has_b64:
        raise GoldenError(f"{where}: delivery carries exactly one of body (UTF-8 text) and body_b64")
    if has_text:
        body = str(row["body"]).encode("utf-8")
    else:
        try:
            body = base64.b64decode(str(row["body_b64"]), validate=True)
        except ValueError as exc:
            raise GoldenError(f"{where}: delivery.body_b64 is not base64: {exc}") from exc
    return Delivery(
        url=str(row["url"]),
        headers={str(k): str(v) for k, v in dict(row.get("headers", {})).items()},
        body=body,
    )


def _event(row: object, delivery: Delivery, *, where: str) -> PreparedEvent | None:
    """The event the delivery carried, for a scheme that reads it. A signature over url, bytes and secret needs none."""
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise GoldenError(f"{where}: event is an object, not {type(row).__name__}")
    blank = _blank_event(delivery)
    return PreparedEvent(
        type=str(row.get("type", blank.type)),
        event_id=str(row.get("event_id", blank.event_id)),
        entity_id=str(row.get("entity_id", blank.entity_id)),
        created_at=str(row.get("created_at", blank.created_at)),
        body=row.get("body", blank.body),
    )


def _blank_event(delivery: Delivery) -> PreparedEvent:
    """What a golden naming no event means: the body it sent, nothing invented."""
    try:
        body: object = json.loads(delivery.body) if delivery.body else {}
    except ValueError:
        body = {}
    return PreparedEvent(type="", event_id="", entity_id="", created_at="", body=body)


# ---------------------------------------------------------------------------
# The comparison.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Divergence:
    """One header the signer did not reproduce."""

    #: ``signature`` -- produced and different; ``missing`` -- not produced at all, the scheme disagreeing about its
    #: own vocabulary rather than about a value.
    kind: Literal["signature", "missing"]
    header: str
    expected: str
    actual: str | None

    def __str__(self) -> str:
        got = "nothing" if self.actual is None else repr(self.actual)
        return f"{self.header}: expected {self.expected!r}, signer produced {got}"


def verify_golden(golden: Golden, signer: Signer) -> tuple[Divergence, ...]:
    """Every named header the signer failed to reproduce, in the golden's order. Names match case insensitively on both
    sides, values byte for byte: a signature differing only in case is a different signature."""
    produced = {str(name).lower(): str(value) for name, value in signer(golden.sign_input()).items()}
    out: list[Divergence] = []
    for name in golden.signature_headers:
        expected = golden.delivery.header(name)
        assert expected is not None  # Golden.of refuses a name the delivery lacks.
        actual = produced.get(name.lower())
        if actual is None:
            out.append(Divergence("missing", name.lower(), expected, None))
        elif actual != expected:
            out.append(Divergence("signature", name.lower(), expected, actual))
    return tuple(out)


# ---------------------------------------------------------------------------
# A directory of them.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoldenResult:
    """One golden, verified once."""

    name: str
    golden: Golden
    divergences: tuple[Divergence, ...]

    @property
    def ok(self) -> bool:
        return not self.divergences


def load_goldens(directory: str | Path) -> tuple[tuple[str, Golden], ...]:
    """Every ``*.json`` in ``directory``, sorted by file name, each validated."""
    path = Path(directory)
    if not path.is_dir():
        raise GoldenError(f"no such directory of goldens: {path}")
    out: list[tuple[str, Golden]] = []
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.suffix != ".json" or not entry.is_file():
            continue
        try:
            doc = json.loads(entry.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise GoldenError(f"{entry.name}: not JSON: {exc}") from exc
        out.append((entry.name, Golden.of(doc, where=entry.name)))
    return tuple(out)


def run_goldens(directory: str | Path, signer: Signer) -> tuple[GoldenResult, ...]:
    """Load and verify every golden in ``directory``."""
    return tuple(GoldenResult(name, golden, verify_golden(golden, signer)) for name, golden in load_goldens(directory))


def format_goldens(results: Sequence[GoldenResult]) -> str:
    """One line per golden, its divergences under it, then a total."""
    lines: list[str] = []
    for result in results:
        mark = "PASS" if result.ok else "FAIL"
        source = result.golden.source
        provenance = str(source.provenance)
        if source.provenance == "recorded":
            provenance += f" {source.environment} {source.api_version} on {source.recorded}"
        lines.append(f"[{mark}] {result.name} ({result.golden.vendor}, {provenance})")
        lines.extend(f"        {divergence}" for divergence in result.divergences)
    failed = sum(1 for result in results if not result.ok)
    lines.append(f"{len(results) - failed} passed, {failed} failed")
    lines.append("OK" if not failed else "NOT OK")
    return "\n".join(lines)
