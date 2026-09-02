"""The harness validates, and nothing can quietly stop it.

The whole "every Square response is validated against the published schema"
property (D-006) rests on one expression in ``harness.py``. The adversarial
review of konyklabs/roadmap#55 ran the deletion test on it -- replace the
validating client with the plain one and nothing went red. Now something does.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.square.harness import LEDGER, SURFACE, Harness
from tests.unit.square.harness import harness as build_harness
from vendorfake.fidelity.validate import ValidatingClient


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("full")


def test_every_harness_client_validates_against_the_square_extract(h: Harness) -> None:
    assert isinstance(h.api, ValidatingClient)
    assert h.api.surface is SURFACE
    assert h.api.ledger is LEDGER
    assert SURFACE.declaration.anchor == "vendorfake.square.fidelity"
    assert SURFACE.extract.metadata["sources"][0]["url"].startswith("https://raw.githubusercontent.com/square/")


def test_a_call_through_the_harness_is_counted_as_validated(h: Harness) -> None:
    before = LEDGER.row("GET /v2/locations").validated
    response = h.api.get("/v2/locations", headers=h.auth)
    assert response.status == 200
    assert LEDGER.row("GET /v2/locations").validated == before + 1


def test_no_vendor_route_is_undeclared(h: Harness) -> None:
    kinds = {classified.key: classified.kind for classified in SURFACE.classify_all(h.unit.routes)}
    assert "undeclared" not in kinds.values(), {k: v for k, v in kinds.items() if v == "undeclared"}
    assert sum(1 for kind in kinds.values() if kind == "operation") == 33
    assert sum(1 for kind in kinds.values() if kind == "excused") == 2
