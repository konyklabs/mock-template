"""What ``pytest --pyargs vendorfake.fidelity -p vendorfake.fidelity.plugin`` collects: one test, parametrised by :func:`vendorfake.fidelity.plugin.pytest_generate_tests`, holding no assertion of its own so this rendering can never disagree with the standalone runner."""

from __future__ import annotations

from vendorfake.fidelity.plugin import PluginCase, run_case


def test_case(fidelity_case: PluginCase | None) -> None:
    run_case(fidelity_case)
