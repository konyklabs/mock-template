"""pytest plugin exposing the conformance suite to a vendor's own test run.

Registered through the ``pytest11`` entry point so that a downstream vendor
module can run the contracts against itself with no wiring.
"""

from __future__ import annotations


def pytest_configure(config: object) -> None:
    """Placeholder until the check registry lands in phase 5."""
