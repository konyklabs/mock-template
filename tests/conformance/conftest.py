from __future__ import annotations

import pytest

from tests.conformance.harness import clover_target, target
from vendorfake.conformance import ConformanceTarget

_TARGETS = {"square": target, "clover": clover_target}


@pytest.fixture(scope="session", params=sorted(_TARGETS), ids=sorted(_TARGETS))
def conformance_target(request: pytest.FixtureRequest) -> ConformanceTarget:
    """Every built-in vendor, so the plain ``pytest`` run CI executes drives
    both matrices: a target only the self-test script named would let that
    vendor regress green."""
    return _TARGETS[str(request.param)]()
