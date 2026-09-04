"""What ``pytest --pyargs vendorfake.fidelity -p vendorfake.fidelity.plugin`` collects.

One test, parametrised by :func:`vendorfake.fidelity.plugin.pytest_generate_tests`
with every case of the corpus the ``--fidelity-target`` names. Shipped inside
the package for the same reason ``conformance/test_contracts.py`` is: a
downstream vendor gets the runner without copying anything. No assertion of
its own -- the standalone runner and this rendering must never disagree.
"""

from __future__ import annotations

from vendorfake.fidelity.plugin import PluginCase, run_case


def test_case(fidelity_case: PluginCase | None) -> None:
    run_case(fidelity_case)
