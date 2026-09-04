"""What ``pytest --pyargs vendorfake.conformance -p vendorfake.conformance.plugin``
collects.

One test, because the matrix is already the registry: every contract, on every
profile the target declares, arrives as a parameter from
:func:`vendorfake.conformance.plugin.pytest_generate_tests` -- which is why
the ``-p`` flag is part of the command: ``--pyargs`` only selects this file,
and nothing here loads the plugin that parametrizes it. A red run names
``test_contract[C13-full-inprocess]`` and prints the contract's own prose.

This module is shipped inside the package rather than kept beside it, because
``--pyargs`` collects the test files a package *contains*; that is the whole
mechanism by which a downstream vendor gets the contracts without copying
anything. It holds no assertion of its own -- an assertion here would be one
the standalone runner did not make, and the two renderings must never be able
to disagree about what conformance means.
"""

from __future__ import annotations

from vendorfake.conformance.plugin import Case, run_case


def test_contract(conformance_case: Case | None) -> None:
    run_case(conformance_case)
