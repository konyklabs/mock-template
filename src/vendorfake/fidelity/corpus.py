"""The behaviour corpus: documented facts as data, and how they are read and matched.

FOR: asserting what a vendor's documentation says a request answers, without
a line of vendor code in this package. A case is a JSON file naming the page
it was read from, whether the fact is ``documented`` or a ``judgment``, the
unit routes it exercises, and an ordered list of steps -- request, expected
subset of the response, captures for later steps. This module reads those
files, checks them against the shipped schema, and provides the three
operations the runner applies to them: interpolation of ``${...}``
references, RFC 6901 pointer resolution, and the subset match.

INVARIANT: **every case is validated against ``corpus.schema.json`` before it
is run, and two cases may not share an id.** A corpus that loads is a corpus
whose every file is well-formed; a malformed case is refused at load with the
file name and the pointer the schema rejected, never discovered half-way
through a run as a ``KeyError``.

THE MATCH IS A SUBSET MATCH, by design. A documented fact names the fields the
documentation promises; the unit is free to answer more. So an expected object
requires every expected key to be present and to match recursively, an expected
list requires equal length and element-wise matches (a list is an ordered
promise), and an expected scalar requires equality -- with two escapes for the
values documentation cannot pin: ``${any}`` matches any present value and
``${re:<pattern>}`` full-matches a scalar rendered as text. What must *not* be
present is said separately, as ``absent`` pointers, because "this key is
missing" cannot be spelled inside a subset.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Literal

import jsonschema

__all__ = [
    "ANY_TOKEN",
    "AUTH_HEADER_KEY",
    "CORPUS_DIR",
    "MISSING",
    "RE_PREFIX",
    "SCHEMA_FILE",
    "Case",
    "CorpusError",
    "Expect",
    "InterpolationError",
    "Mismatch",
    "Request",
    "Source",
    "Step",
    "absent_violations",
    "interpolate",
    "load_corpus",
    "load_schema",
    "match",
    "match_headers",
    "parse_case",
    "resolve_pointer",
    "validate_case",
]

CORPUS_DIR = "corpus"
SCHEMA_FILE = "corpus.schema.json"

AUTH_HEADER_KEY = "$auth"
"""A request header whose value is a ``Route.auth`` mode rather than a header
value. The runner replaces it with the headers of the first credential of
that mode published at ``GET /__unit/auth``."""

ANY_TOKEN = "${any}"
RE_PREFIX = "${re:"

Provenance = Literal["documented", "judgment"]

_REFERENCE = re.compile(r"\$\{(vars|cap)\.([A-Za-z0-9_.-]+)\}|\$\{uuid\}")


class CorpusError(ValueError):
    """A case file this package refuses to run: malformed, or a duplicate id."""


class InterpolationError(KeyError):
    """A ``${vars.x}`` or ``${cap.x}`` reference with nothing to resolve it."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


class _Missing:
    """The one value a JSON document can never contain; what ``resolve_pointer``
    returns for a pointer that does not resolve. Distinct from ``None``, which
    is a value."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<missing>"


MISSING = _Missing()


# ---------------------------------------------------------------------------
# The case, read.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Source:
    """Where a case's fact came from."""

    url: str
    fetched: str
    provenance: Provenance
    note: str = ""

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Source:
        provenance: Provenance
        if row["provenance"] == "documented":
            provenance = "documented"
        elif row["provenance"] == "judgment":
            provenance = "judgment"
        else:
            raise CorpusError(f"provenance must be documented or judgment, got {row['provenance']!r}")
        return cls(
            url=str(row["url"]), fetched=str(row["fetched"]), provenance=provenance, note=str(row.get("note", ""))
        )


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    body: Any = None
    #: Whether the case gave a body at all. ``null`` is a body; absence is not.
    has_body: bool = False

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Request:
        return cls(
            method=str(row["method"]).upper(),
            path=str(row["path"]),
            headers={str(k): str(v) for k, v in dict(row.get("headers", {})).items()},
            query={str(k): str(v) for k, v in dict(row.get("query", {})).items()},
            body=row.get("body"),
            has_body="body" in row,
        )


@dataclass(frozen=True, slots=True)
class Expect:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None
    has_body: bool = False
    absent: tuple[str, ...] = ()

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Expect:
        return cls(
            status=int(row["status"]),
            headers={str(k): str(v) for k, v in dict(row.get("headers", {})).items()},
            body=row.get("body"),
            has_body="body" in row,
            absent=tuple(str(p) for p in row.get("absent", ())),
        )


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    request: Request
    expect: Expect
    capture: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Step:
        return cls(
            name=str(row["name"]),
            request=Request.of(row["request"]),
            expect=Expect.of(row["expect"]),
            capture={str(k): str(v) for k, v in dict(row.get("capture", {})).items()},
        )


@dataclass(frozen=True, slots=True)
class Case:
    """One file of ``corpus/``, validated and typed."""

    id: str
    title: str
    source: Source
    routes: tuple[str, ...]
    steps: tuple[Step, ...]
    profile: str | None = None

    @property
    def provenance(self) -> Provenance:
        return self.source.provenance

    @classmethod
    def of(cls, doc: Mapping[str, Any]) -> Case:
        profile = doc.get("profile")
        return cls(
            id=str(doc["id"]),
            title=str(doc["title"]),
            source=Source.of(doc["source"]),
            routes=tuple(str(r) for r in doc["routes"]),
            steps=tuple(Step.of(row) for row in doc["steps"]),
            profile=None if profile is None else str(profile),
        )


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------

_SCHEMA_CACHE: list[jsonschema.Draft202012Validator] = []


def load_schema() -> jsonschema.Draft202012Validator:
    """The shipped case schema, compiled once."""
    if not _SCHEMA_CACHE:
        text = (resources.files("vendorfake.fidelity") / SCHEMA_FILE).read_text(encoding="utf-8")
        schema = json.loads(text)
        jsonschema.Draft202012Validator.check_schema(schema)
        _SCHEMA_CACHE.append(jsonschema.Draft202012Validator(schema))
    return _SCHEMA_CACHE[0]


def validate_case(doc: object, *, where: str = "<case>") -> None:
    """Refuse a document the schema rejects, naming every violation by pointer."""
    errors = sorted(load_schema().iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f"{where}: not a valid corpus case ({len(errors)} problem(s)):"]
        for err in errors:
            pointer = "/" + "/".join(str(p) for p in err.absolute_path)
            lines.append(f"  {pointer}: {err.message}")
        raise CorpusError("\n".join(lines))


def parse_case(doc: object, *, where: str = "<case>") -> Case:
    """Validate, then type, one case document."""
    validate_case(doc, where=where)
    assert isinstance(doc, Mapping)
    return Case.of(doc)


def load_corpus(anchor: str) -> tuple[Case, ...]:
    """Every ``corpus/*.json`` in the package named by ``anchor``, sorted by
    file name, each validated. An anchor with no ``corpus`` directory has an
    empty corpus, which is a fact the report prints rather than an error."""
    try:
        directory = resources.files(anchor) / CORPUS_DIR
    except ModuleNotFoundError as exc:
        raise FileNotFoundError(f"no package {anchor!r} to read a corpus from: {exc}") from exc
    if not directory.is_dir():
        return ()
    cases: list[Case] = []
    seen: dict[str, str] = {}
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".json") or not entry.is_file():
            continue
        where = f"{anchor}/{CORPUS_DIR}/{entry.name}"
        try:
            doc = json.loads(entry.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CorpusError(f"{where}: not JSON: {exc}") from exc
        case = parse_case(doc, where=where)
        if case.id in seen:
            raise CorpusError(f"{where}: duplicate case id {case.id!r}, first defined in {seen[case.id]}")
        seen[case.id] = where
        cases.append(case)
    return tuple(cases)


# ---------------------------------------------------------------------------
# Interpolation.
# ---------------------------------------------------------------------------


def interpolate(
    value: Any,
    *,
    variables: Mapping[str, str],
    captures: Mapping[str, Any],
    uuid: Callable[[], str],
) -> Any:
    """Replace ``${vars.x}``, ``${cap.x}`` and ``${uuid}`` in every string of ``value``.

    A string that is exactly one reference takes the referenced value with its
    type intact (a captured number stays a number); a reference embedded in a
    longer string is rendered as text. ``${any}`` and ``${re:...}`` are not
    references and pass through untouched for the matcher. A reference nothing
    resolves raises :class:`InterpolationError` naming it.
    """
    if isinstance(value, str):
        return _interpolate_str(value, variables, captures, uuid)
    if isinstance(value, Mapping):
        return {str(k): interpolate(v, variables=variables, captures=captures, uuid=uuid) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [interpolate(v, variables=variables, captures=captures, uuid=uuid) for v in value]
    return value


def _interpolate_str(
    text: str, variables: Mapping[str, str], captures: Mapping[str, Any], uuid: Callable[[], str]
) -> Any:
    def lookup(m: re.Match[str]) -> Any:
        kind, name = m.group(1), m.group(2)
        if kind is None:
            return uuid()
        if kind == "vars":
            if name not in variables:
                raise InterpolationError(f"${{vars.{name}}}: the declaration defines no variable {name!r}")
            return variables[name]
        if name not in captures:
            raise InterpolationError(f"${{cap.{name}}}: no earlier step captured {name!r}")
        return captures[name]

    whole = _REFERENCE.fullmatch(text)
    if whole is not None:
        return lookup(whole)
    return _REFERENCE.sub(lambda m: str(lookup(m)), text)


# ---------------------------------------------------------------------------
# Pointers.
# ---------------------------------------------------------------------------


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901: the value at ``pointer``, or :data:`MISSING` when it does not resolve."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise ValueError(f"a JSON pointer starts with '/' or is empty, got {pointer!r}")
    current = doc
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                return MISSING
            current = current[int(token)]
        else:
            return MISSING
    return current


# ---------------------------------------------------------------------------
# The subset match.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mismatch:
    """The first place an expectation and a response part ways."""

    pointer: str
    expected: Any
    actual: Any

    def __str__(self) -> str:
        return f"at {self.pointer or '/'}: expected {self.expected!r}, got {self.actual!r}"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _scalar_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str):
        if expected == ANY_TOKEN:
            return actual is not MISSING
        if expected.startswith(RE_PREFIX) and expected.endswith("}"):
            pattern = expected[len(RE_PREFIX) : -1]
            if not _is_scalar(actual) or actual is None:
                return False
            return re.fullmatch(pattern, str(actual)) is not None
    if isinstance(expected, bool) is not isinstance(actual, bool):
        return False
    return bool(expected == actual)


def match(expected: Any, actual: Any, pointer: str = "") -> Mismatch | None:
    """``None`` when ``actual`` contains ``expected`` as a subset; else the first mismatch."""
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return Mismatch(pointer, expected, actual)
        for key, want in expected.items():
            child = f"{pointer}/{_escape(str(key))}"
            if key not in actual:
                return Mismatch(child, want, MISSING)
            found = match(want, actual[key], child)
            if found is not None:
                return found
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return Mismatch(pointer, expected, actual)
        if len(expected) != len(actual):
            return Mismatch(pointer, f"a list of {len(expected)}", f"a list of {len(actual)}")
        for index, (want, got) in enumerate(zip(expected, actual, strict=True)):
            found = match(want, got, f"{pointer}/{index}")
            if found is not None:
                return found
        return None
    if _scalar_matches(expected, actual):
        return None
    return Mismatch(pointer, expected, actual)


def match_headers(expected: Mapping[str, str], actual: Mapping[str, str]) -> Mismatch | None:
    """Case-insensitive subset match on header names; values match as scalars."""
    lowered = {name.lower(): value for name, value in actual.items()}
    for name, want in expected.items():
        key = name.lower()
        pointer = f"/headers/{_escape(key)}"
        if key not in lowered:
            return Mismatch(pointer, want, MISSING)
        if not _scalar_matches(want, lowered[key]):
            return Mismatch(pointer, want, lowered[key])
    return None


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def absent_violations(body: Any, pointers: Sequence[str]) -> Mismatch | None:
    """The first ``absent`` pointer that resolves, as a mismatch."""
    for pointer in pointers:
        found = resolve_pointer(body, pointer)
        if found is not MISSING:
            return Mismatch(pointer, MISSING, found)
    return None
