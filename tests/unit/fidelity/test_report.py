"""The matrix and the per-case rendering, over the synthetic vendor's surface."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tests.unit.fidelity.test_runner import DECLARATION, EXTRACT, open_synthetic_unit
from vendorfake.fidelity.report import CaseResult, CorpusReport, StepFailure, format_cases, format_matrix
from vendorfake.fidelity.types import Extract, FidelityDeclaration, Surface


@dataclass(frozen=True)
class _Row:
    key: str
    validated: int


class _Ledger:
    def __init__(self, **validated: int) -> None:
        self._rows = tuple(_Row(key.replace("_", " ", 1), n) for key, n in validated.items())

    def rows(self) -> Sequence[_Row]:
        return self._rows

    def summary(self) -> str:
        return "fidelity: 4 validated over 2 routes"

    def absorbed(self) -> Sequence[tuple[str, int]]:
        return ()


def _surface() -> Surface:
    return Surface(FidelityDeclaration.of("synthetic", DECLARATION), Extract(EXTRACT))


FAILURE = StepFailure("create", "/order/state", "COMPLETED", "OPEN")
RESULTS = (
    CaseResult("orders.create", "creates", "documented", ("POST /v2/orders",), "test", True),
    CaseResult(
        "orders.state", "state", "documented", ("POST /v2/orders", "GET /v2/orders/{order_id}"), "test", False, FAILURE
    ),
    CaseResult("orders.judged", "judged", "judgment", ("POST /v2/orders",), "test", True),
)


def _matrix(results: tuple[CaseResult, ...] = RESULTS, *, ledger: _Ledger | None = None, drop: str = "") -> str:
    report = CorpusReport(target="synthetic", results=results, validated=ledger is not None, ledger=ledger)
    with open_synthetic_unit(None) as unit:
        routes = tuple(r for r in unit.routes if r.path != drop)
        return format_matrix(_surface(), routes, ledger, report)


def test_the_matrix_has_one_row_per_vendor_route_and_no_control_plane_rows() -> None:
    text = _matrix(ledger=_Ledger(POST_="/v2/orders", **{"POST_/v2/orders": 3, "GET_/v2/whoami": 1}))
    lines = text.splitlines()
    assert "/__unit/" not in text
    assert any(
        line.startswith("POST /v2/orders")
        and line.endswith("| spec: operation CreateOrder | validated: 3 | documented: 2 (1 FAILED) | judgment: 1")
        for line in lines
    ), text
    assert any(
        "GET /v2/orders/{order_id}" in line and "operation RetrieveOrder" in line and "documented: 1 (1 FAILED)" in line
        for line in lines
    )
    assert any("GET /v2/whoami" in line and "validated: 1 | documented: 0 | judgment: 0" in line for line in lines)
    assert any("GET /v2/plain" in line and "spec: EXCUSED (a text route the spec never had)" in line for line in lines)
    assert any(line.startswith("GET /v2/undeclared") and "| spec: UNDECLARED |" in line for line in lines)
    assert "routes: 5 (3 operation, 1 excused, 1 UNDECLARED)" in text
    assert "UNDECLARED GET /v2/undeclared:" in text
    assert "cases: 2 passed, 1 failed (documented 1/2, judgment 1/1)" in text
    assert "FAILED orders.state (documented): state" in text
    assert "step 'create' at /order/state: expected 'COMPLETED', got 'OPEN'" in text
    assert "contract: fidelity: 4 validated over 2 routes" in text
    assert "pin: https://example.test/api.json version 2.0 sha256 abcdef012345 fetched 2026-09-02" in text
    assert text.endswith("\nNOT OK")


def test_an_undeclared_route_alone_makes_the_matrix_not_ok() -> None:
    passing = tuple(r for r in RESULTS if r.passed)
    assert _matrix(passing, ledger=_Ledger()).endswith("\nNOT OK")
    assert _matrix(passing, ledger=_Ledger(), drop="/v2/undeclared").endswith("\nOK")


def test_without_a_ledger_the_matrix_says_nothing_was_validated() -> None:
    text = _matrix(ledger=None, drop="/v2/undeclared")
    assert "| validated: - |" in text
    assert "contract: responses were NOT validated against the schema in this run" in text


def test_an_extract_without_metadata_prints_that_it_has_no_pin() -> None:
    bare = {k: v for k, v in EXTRACT.items() if k != "x-vendorfake"}
    surface = Surface(FidelityDeclaration.of("synthetic", DECLARATION), Extract(bare))
    report = CorpusReport(target="synthetic", results=(), validated=False)
    assert "pin: the extract carries no x-vendorfake.sources block" in format_matrix(surface, (), None, report)


def test_format_cases_lists_every_case_with_the_failure_under_it() -> None:
    report = CorpusReport(target="synthetic", results=RESULTS, validated=False, caveats=("shared unit",))
    text = format_cases(report)
    assert text.startswith("note: shared unit\n\n[PASS] orders.create (documented, test) creates")
    assert "[FAIL] orders.state (documented, test) state\n        step 'create' at /order/state" in text
    assert "2 passed, 1 failed (documented 1/2, judgment 1/1); responses NOT validated against the schema" in text
    assert text.endswith("\nNOT OK")
    assert report.by_provenance() == {"documented": (1, 2), "judgment": (1, 1)}


def test_a_failure_detail_is_indented_under_the_step_line() -> None:
    failure = StepFailure("create", "status", 200, 400, detail='body: {"errors": []}')
    assert failure.lines() == (
        "step 'create' at status: expected 200, got 400",
        '  body: {"errors": []}',
    )
