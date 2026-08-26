from __future__ import annotations

import pytest

from tests.conformance.harness import target
from vendorfake.conformance import ConformanceTarget


@pytest.fixture(scope="session")
def conformance_target() -> ConformanceTarget:
    return target()
