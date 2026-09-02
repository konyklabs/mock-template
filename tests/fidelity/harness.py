"""The repository's fidelity targets -- re-exported from the wheel's own.

``vendorfake.testing.fidelity`` is what a consumer names; CI names the same
object through this module so the two can never disagree. Anything the
repository's tests need beyond the shipped targets is added here.
"""

from __future__ import annotations

from vendorfake.testing.fidelity import square_target

__all__ = ["ANCHOR", "VENDOR", "square_target"]

VENDOR = square_target().name
ANCHOR = square_target().anchor
