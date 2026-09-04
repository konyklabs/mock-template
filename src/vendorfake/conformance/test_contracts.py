"""What ``pytest --pyargs vendorfake.conformance -p vendorfake.conformance.plugin`` collects: one test, parametrized by :func:`vendorfake.conformance.plugin.pytest_generate_tests`, holding no assertion of its own so this rendering can never disagree with the standalone runner."""

from __future__ import annotations

from vendorfake.conformance.plugin import Case, run_case


def test_contract(conformance_case: Case | None) -> None:
    run_case(conformance_case)
