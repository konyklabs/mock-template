"""Deliberately broken units, and the checks each must turn red.

Nothing here is reachable from any production path: no module under
``src/vendorfake/`` imports this package. Each mutant is applied by handing a
wrong collaborator to a constructor that already accepts one -- a vendor
definition, a control-plane factory, a fault selector, a webhook dispatcher, a
conformance client -- so what is being proved is that the *production wiring*
has a seam a real defect could enter through, not that a patched module can be
detected.

One mutant is served by a separate operating-system process
(``tests/conformance/unit_child.py``), because the contract it answers is about
processes: a scenario that hydrated differently per run is invisible to two
units built in one interpreter, which is how it went unnoticed.
"""

from __future__ import annotations

from tests.conformance.mutants import catalog as _catalog  # noqa: F401  (registration side effect)
from tests.conformance.mutants.model import (
    MUTANTS,
    NULL_MUTANT,
    Mutant,
    Provenance,
    mutant_target,
)

__all__ = ["MUTANTS", "NULL_MUTANT", "Mutant", "Provenance", "mutant_target"]
