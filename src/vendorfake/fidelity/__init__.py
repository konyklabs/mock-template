"""Fidelity: does this unit answer what the vendor's own documents say it answers?

FOR: making fidelity to the *vendor* an enforced property rather than a claim.
The conformance package proves the internal contract -- that a vendor module
composes with the core. This package proves the external one, in two legs:

* **contract** -- every response the unit produces is validated against the
  vendor's published schema for that operation and status, taken from a
  scoped extract of the vendor's own OpenAPI document (``validate``);
* **behaviour** -- documented request/response facts are asserted from a
  declarative corpus in which every case names the page it was read from and
  whether it is ``documented`` or a ``judgment`` (``corpus``).

Both are reported as one matrix per route (``report``), and the extract is
pinned to the upstream bytes it was cut from so drift is a diff (``pin``).

WHAT THIS PACKAGE MAY KNOW. Nothing about any vendor -- the same rule as
``vendorfake.conformance``, enforced by ``tools/boundary_check.py``. A vendor
declares its fidelity as *data* (``declaration.json``, ``extract.json``,
``pin.json``, ``corpus/*.json``) in a package the caller names, and this
package reads it. D-006 records why.
"""

from __future__ import annotations

from vendorfake.fidelity.types import (
    Alias,
    Annotation,
    Classified,
    Deviation,
    Excuse,
    Extract,
    FidelityDeclaration,
    Operation,
    Override,
    SpecSource,
    Surface,
    load_declaration,
    load_extract,
)

__all__ = [
    "Alias",
    "Annotation",
    "Classified",
    "Deviation",
    "Excuse",
    "Extract",
    "FidelityDeclaration",
    "Operation",
    "Override",
    "SpecSource",
    "Surface",
    "load_declaration",
    "load_extract",
]
