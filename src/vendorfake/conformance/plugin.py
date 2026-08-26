"""pytest plugin exposing the conformance suite to a vendor's own test run.

Registered through the ``pytest11`` entry point so that a downstream vendor
module can run the contracts against itself with no wiring.

STATE. The registry, the client, the checks and the report have landed and are
runnable two ways today: ``run_conformance(target)`` and
``python -m vendorfake.conformance --target module:attribute``. What is not
here yet is the collection layer that expands the registry into one test per
(check x profile x transport) from an installed wheel -- ``pytest --pyargs
vendorfake.conformance``. In this repository the same expansion is done by
``tests/conformance/test_conformance.py``, so the per-check test ids exist; the
gap is only that an external vendor must call ``run_check`` from its own test
module rather than getting the parametrisation for free.

Deliberately still a no-op rather than half a plugin: a ``pytest11`` entry
point is loaded into every pytest run on the machine, including runs that have
nothing to do with this package, so it earns its hooks or it has none.
"""

from __future__ import annotations


def pytest_configure(config: object) -> None:
    """No hooks yet. See the module docstring for what lands here and why."""
