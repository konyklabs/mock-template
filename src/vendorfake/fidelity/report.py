"""What a corpus run said, and the one matrix that joins it to the contract leg.

FOR: one page per vendor that answers, route by route, "what is this route
in spec terms, how many of its responses were validated against the schema,
and how many documented facts about it hold?" -- and a per-case rendering
for the ``run`` subcommand that names the step, the pointer and both values
of the first expectation that failed.

INVARIANT: **an UNDECLARED route makes the matrix NOT OK, whatever the corpus
said.** A route the declaration neither maps to an operation nor excuses is a
route nothing checked, and a green corpus over it would be a claim about
behaviour with no contract behind it. The report prints the word in capitals
and the CLI exits non-zero on it.

The ledger the contract leg produces is read through a small protocol rather
than imported as a class: this module needs a route key and a count per row,
and nothing else about how the validating client keeps them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from vendorfake.core.kernel.types import Route
from vendorfake.fidelity.corpus import PROVENANCES
from vendorfake.fidelity.types import Surface, route_key

__all__ = [
    "KINDS",
    "CaseResult",
    "CorpusReport",
    "DivergenceKind",
    "LedgerLike",
    "LedgerRow",
    "StepFailure",
    "format_cases",
    "format_matrix",
]

DivergenceKind = Literal["status", "value", "missing", "unexpected", "schema", "header", "capture", "request"]
"""How a case diverged, not merely that it did. ``status`` and ``header`` are the envelope; ``value`` a field that
answered something else, ``missing`` one the vendor promises and the unit did not send, ``unexpected`` one the case said
would be absent; ``schema`` the contract leg refusing the body; ``capture`` a later step's input that never arrived;
``request`` a case this run could not ask at all."""

KINDS: tuple[DivergenceKind, ...] = (
    "status",
    "value",
    "missing",
    "unexpected",
    "schema",
    "header",
    "capture",
    "request",
)
"""Tally order: envelope, then body, then the run's own failures."""


class LedgerRow(Protocol):
    """One route's counters from the contract leg's ledger."""

    @property
    def key(self) -> str: ...

    @property
    def validated(self) -> int: ...

    @property
    def undeclared_status(self) -> int: ...


class LedgerLike(Protocol):
    """What the report needs of ``vendorfake.fidelity.validate.Ledger``."""

    def rows(self) -> Sequence[LedgerRow]: ...

    def summary(self) -> str: ...

    def absorbed(self) -> Sequence[tuple[str, int]]: ...


@dataclass(frozen=True, slots=True)
class StepFailure:
    """The first expectation of a case that did not hold."""

    step: str
    #: Where: a JSON pointer into the response body, or ``status``,
    #: ``headers/<name>``, ``request/headers/$auth``, ``capture/<name>``.
    pointer: str
    expected: Any
    actual: Any
    #: Evidence beyond the two values -- a body excerpt on a status mismatch.
    detail: str = ""
    #: Which class of divergence this is. ``value`` by default: what a caller building a failure by hand usually means.
    kind: DivergenceKind = "value"

    def lines(self) -> tuple[str, ...]:
        out = [f"step {self.step!r} at {self.pointer or '/'}: expected {self.expected!r}, got {self.actual!r}"]
        if self.detail:
            out.extend(f"  {line}" for line in self.detail.splitlines())
        return tuple(out)


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One case, run once against one fresh unit."""

    id: str
    title: str
    provenance: str
    routes: tuple[str, ...]
    profile: str
    passed: bool
    failure: StepFailure | None = None
    steps_run: int = 0
    #: The routes the case's requests actually matched, when the run could
    #: observe them (in-process). Empty over HTTP, where the declared list stands.
    observed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusReport:
    """Every case of one run, plus what the run could and could not check."""

    target: str
    results: tuple[CaseResult, ...]
    #: Whether every response also went through the schema validator.
    validated: bool
    #: A unit somebody else is running, reached over HTTP: shared, unvalidated.
    remote: bool = False
    #: The contract leg's ledger, when this run validated with the default client.
    ledger: LedgerLike | None = None
    caveats: tuple[str, ...] = field(default=())

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if not result.passed)

    @property
    def failures(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if not result.passed)

    def by_provenance(self) -> dict[str, tuple[int, int]]:
        """provenance -> (passed, total). Every known provenance is a key even with nothing behind it: ``recorded
        0/0`` says nothing was recorded, where an absent column would read as the question never asked."""
        out: dict[str, list[int]] = {name: [0, 0] for name in PROVENANCES}
        for result in self.results:
            row = out.setdefault(result.provenance, [0, 0])
            row[1] += 1
            if result.passed:
                row[0] += 1
        return {name: (row[0], row[1]) for name, row in out.items()}

    def by_kind(self) -> dict[str, int]:
        """divergence kind -> how many failing cases diverged that way, in :data:`KINDS` order, zeros included."""
        out: dict[str, int] = dict.fromkeys(KINDS, 0)
        for result in self.failures:
            kind = result.failure.kind if result.failure is not None else "value"
            out[kind] = out.get(kind, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return self.failed == 0


def format_cases(report: CorpusReport) -> str:
    """The ``run`` rendering: one line per case, the failure under it."""
    lines: list[str] = []
    for caveat in report.caveats:
        lines.append(f"note: {caveat}")
    if report.caveats:
        lines.append("")
    for result in report.results:
        mark = "PASS" if result.passed else f"FAIL {result.failure.kind if result.failure is not None else 'value'}"
        lines.append(f"[{mark}] {result.id} ({result.provenance}, {result.profile}) {result.title}")
        if result.failure is not None:
            lines.extend(f"        {line}" for line in result.failure.lines())
    lines.append(
        f"{report.passed} passed, {report.failed} failed ({_provenance_counts(report)})"
        f"{'' if report.validated else '; responses NOT validated against the schema'}"
    )
    if report.failed:
        lines.append("divergence: " + ", ".join(f"{kind} {n}" for kind, n in report.by_kind().items()))
    lines.append("OK" if report.ok else "NOT OK")
    return "\n".join(lines)


def _provenance_counts(report: CorpusReport) -> str:
    """``documented 5/5, judgment 1/1, recorded 0/0`` -- one column per provenance."""
    counts = report.by_provenance()
    return ", ".join(f"{name} {counts[name][0]}/{counts[name][1]}" for name in sorted(counts))


def format_matrix(
    surface: Surface,
    routes: Sequence[Route],
    ledger: LedgerLike | None,
    corpus_report: CorpusReport,
) -> str:
    """One row per vendor route joining the contract leg and the behaviour leg.

    ``METHOD path | spec: operation <opId> / EXCUSED (reason) / UNDECLARED |
    validated: n | documented: n | judgment: n | recorded: n``, then totals,
    then the pin line from the extract's ``x-vendorfake`` block.
    """
    validated: Mapping[str, int] = {row.key: row.validated for row in ledger.rows()} if ledger is not None else {}
    covered: dict[str, dict[str, list[CaseResult]]] = {}
    for result in corpus_report.results:
        for key in result.observed or result.routes:
            covered.setdefault(key, {}).setdefault(result.provenance, []).append(result)

    classified = [c for c in surface.classify_all(routes) if c.kind != "internal"]
    rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    undeclared: list[str] = []
    counts = {"operation": 0, "excused": 0, "undeclared": 0}
    for item in classified:
        key = route_key(item.route.method, item.route.path)
        counts[item.kind] += 1
        if item.kind == "operation" and item.operation is not None:
            spec = f"operation {item.operation.raw.get('operationId') or item.operation.key}"
            if item.alias is not None:
                spec += f" (alias of {item.alias.spec_path})"
        elif item.kind == "excused":
            spec = f"EXCUSED ({item.reason})"
        else:
            spec = "UNDECLARED"
            undeclared.append(key)
        per_route = covered.get(key, {})
        rows.append(
            (
                key,
                spec,
                str(validated.get(key, 0)) if ledger is not None else "-",
                tuple(_count(per_route.get(name, [])) for name in PROVENANCES),
            )
        )

    width = max((len(row[0]) for row in rows), default=0)
    lines = [
        f"{key.ljust(width)} | spec: {spec} | validated: {v} | "
        + " | ".join(f"{name}: {count}" for name, count in zip(PROVENANCES, per_provenance, strict=True))
        for key, spec, v, per_provenance in rows
    ]
    lines.append("")
    lines.append(
        f"routes: {len(rows)} ({counts['operation']} operation, {counts['excused']} excused, "
        f"{counts['undeclared']} UNDECLARED)"
    )
    for key in undeclared:
        lines.append(f"UNDECLARED {key}: neither an operation of the extract nor excused in the declaration")
    lines.append(
        f"cases: {corpus_report.passed} passed, {corpus_report.failed} failed ({_provenance_counts(corpus_report)})"
    )
    if corpus_report.failed:
        lines.append("divergence: " + ", ".join(f"{kind} {n}" for kind, n in corpus_report.by_kind().items()))
    for result in corpus_report.failures:
        lines.append(f"FAILED {result.id} ({result.provenance}): {result.title}")
        if result.failure is not None:
            lines.extend(f"    {line}" for line in result.failure.lines())
    if ledger is not None:
        lines.append(f"contract: {ledger.summary()}")
    else:
        lines.append("contract: responses were NOT validated against the schema in this run")
    if ledger is not None:
        for label, count in ledger.absorbed():
            lines.append(f"deviation absorbed {count} error(s): {label}")
        for row in ledger.rows():
            if row.undeclared_status:
                lines.append(
                    f"undeclared status: {row.key} answered {row.undeclared_status} response(s) with a status the "
                    f"document does not declare for it (shape checked against error_schema; the status is the unit's judgment)"
                )
    for override in surface.declaration.overrides:
        lines.append(
            f"override: {override.route} {override.status} validated against {override.schema} -- {override.url}"
        )
    for caveat in corpus_report.caveats:
        lines.append(f"note: {caveat}")
    lines.extend(_pin_lines(surface))
    lines.append("OK" if corpus_report.ok and not undeclared else "NOT OK")
    return "\n".join(lines)


def _count(results: Sequence[CaseResult]) -> str:
    failed = sum(1 for result in results if not result.passed)
    return f"{len(results)}" if not failed else f"{len(results)} ({failed} FAILED)"


def _pin_lines(surface: Surface) -> list[str]:
    sources = surface.extract.metadata.get("sources")
    if not isinstance(sources, list) or not sources:
        return ["pin: the extract carries no x-vendorfake.sources block"]
    out: list[str] = []
    for row in sources:
        if not isinstance(row, Mapping):
            continue
        sha = str(row.get("sha256", ""))
        out.append(
            f"pin: {row.get('url', '?')} version {row.get('version', '?')} "
            f"sha256 {sha[:12] or '?'} fetched {row.get('fetched', '?')}"
        )
    stubbed = surface.extract.metadata.get("stubbed")
    if isinstance(stubbed, list) and stubbed:
        out.append(
            "stubbed schemas, validated as {} because upstream dangles there: "
            + ", ".join(str(name) for name in stubbed)
            + " (each accepted by name in the declaration)"
        )
    return out
