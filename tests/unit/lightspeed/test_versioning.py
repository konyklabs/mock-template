"""The retailer-global version counter, the list envelope, and the walk.

The counter and the envelope are the two cross-cutting mechanics every list and
every entity in this vendor depends on, and neither has a seam in ``core/`` --
the store's ``version`` is per-entity optimistic concurrency and its
``paginate`` is an opaque expiring cursor, both of which are the right model
for a different vendor. See ``versioning.py``.
"""

from __future__ import annotations

import pytest

from tests.unit.lightspeed.harness import Harness
from vendorfake.core.kernel.types import UnitError
from vendorfake.lightspeed.entities import COL, OBJECT_VERSION
from vendorfake.lightspeed.seed import constants as c
from vendorfake.lightspeed.versioning import FIRST_VERSION, LightspeedVersions, envelope

LISTS = ("/outlets", "/registers", "/payment_types")


# -- the counter -------------------------------------------------------------


def test_the_counter_is_monotonic_and_starts_at_the_declared_first() -> None:
    versions = LightspeedVersions()
    assert [versions.bump() for _ in range(3)] == [FIRST_VERSION, FIRST_VERSION + 1, FIRST_VERSION + 2]


def test_resetting_restarts_the_sequence() -> None:
    versions = LightspeedVersions()
    versions.bump()
    versions.reset()
    assert versions.bump() == FIRST_VERSION


def test_one_sequence_spans_every_resource_type(h: Harness) -> None:
    """DOCUMENTED: "simply a monotonically increasing integer" -- ONE per
    retailer, not one per collection. So no outlet shares a version with a
    register."""
    store = h.unit.context.store
    numbers = [
        row[OBJECT_VERSION]
        for collection in (COL.retailer, COL.outlets, COL.registers, COL.payment_types)
        for row in store.collection(collection).all()
    ]
    assert len(numbers) == len(set(numbers))


def test_a_mutation_draws_a_new_version_above_every_existing_one(h: Harness) -> None:
    before = max(int(row["version"]) for row in h.get(h.path("/registers")).json()["data"])
    assert h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/close"), "{}").status == 200
    closed = h.get(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}")).json()["data"]
    assert closed["version"] > before


# -- the envelope ------------------------------------------------------------


@pytest.mark.parametrize("path", LISTS)
def test_every_list_answers_the_documented_envelope(h: Harness, path: str) -> None:
    body = h.get(h.path(path)).json()
    assert set(body) == {"data", "version"}
    assert set(body["version"]) == {"max", "min"}
    assert body["version"]["max"] == max(row["version"] for row in body["data"])
    assert body["version"]["min"] == min(row["version"] for row in body["data"])


@pytest.mark.parametrize("path", LISTS)
def test_rows_come_back_ascending_by_version(h: Harness, path: str) -> None:
    versions = [row["version"] for row in h.get(h.path(path)).json()["data"]]
    assert versions == sorted(versions)


def test_an_empty_page_answers_two_nulls(h: Harness) -> None:
    """DOCUMENTED: max and min are "null when the result set is empty", and
    both keys are REQUIRED -- so it is the values that carry the emptiness, not
    the absence of the keys. An empty page is what ends a caller's walk."""
    body = h.get(h.path("/outlets"), query={"after": "99999999"}).json()
    assert body == {"data": [], "version": {"max": None, "min": None}}


def test_the_envelope_helper_reads_the_projected_rows() -> None:
    assert envelope([{"id": "a", "version": 4}, {"id": "b", "version": 9}])["version"] == {"max": 9, "min": 4}
    assert envelope([])["version"] == {"max": None, "min": None}


# -- the walk ----------------------------------------------------------------


def test_after_defaults_to_zero_so_the_first_page_needs_no_parameter(h: Harness) -> None:
    """DOCUMENTED: "By default, the value of the after parameter will be
    assumed as equal 0"."""
    assert h.get(h.path("/outlets")).json() == h.get(h.path("/outlets"), query={"after": "0"}).json()


def test_a_full_walk_repeats_no_row_and_loses_none(h: Harness) -> None:
    """The documented forward sync: send the previous response's
    ``version.max`` as the next ``after`` and stop when ``data`` is empty."""
    whole = [row["id"] for row in h.get(h.path("/outlets")).json()["data"]]
    assert len(whole) >= 2

    seen: list[str] = []
    cursor: int | None = None
    for _ in range(len(whole) + 2):
        query = {"page_size": "1"}
        if cursor is not None:
            query["after"] = str(cursor)
        body = h.get(h.path("/outlets"), query=query).json()
        rows = body["data"]
        if not rows:
            break
        assert len(rows) == 1
        seen.extend(row["id"] for row in rows)
        cursor = body["version"]["max"]
    assert seen == whole


def test_before_is_an_inclusive_upper_limit(h: Harness) -> None:
    rows = h.get(h.path("/outlets")).json()["data"]
    first = rows[0]
    body = h.get(h.path("/outlets"), query={"before": str(first["version"])}).json()
    assert [row["id"] for row in body["data"]] == [first["id"]]


def test_page_size_caps_the_rows(h: Harness) -> None:
    assert len(h.get(h.path("/registers"), query={"page_size": "1"}).json()["data"]) == 1


def test_a_non_integer_cursor_is_refused_by_name(h: Harness) -> None:
    """The specification types all three as integers; a consumer sending
    ``after=abc`` has a bug this fake should show them rather than silently
    ignore."""
    answered = h.get(h.path("/outlets"), query={"after": "abc"})
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "after"


def test_a_page_size_below_one_is_refused(h: Harness) -> None:
    answered = h.get(h.path("/outlets"), query={"page_size": "0"})
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "page_size"


def test_an_over_large_page_size_is_clamped_not_refused(h: Harness) -> None:
    answered = h.get(h.path("/outlets"), query={"page_size": "100000"})
    assert answered.status == 200
    assert len(answered.json()["data"]) == 2


def test_deleted_defaults_to_excluding_deleted_rows(h: Harness) -> None:
    store = h.unit.context.store
    store.collection(COL.outlets).update(
        c.SEED_OUTLET_SECOND_ID,
        lambda draft: draft.__setitem__("deleted_at", "2026-09-04T12:00:00Z"),
        meta={"operation_id": "TestDelete"},
    )
    assert [row["id"] for row in h.get(h.path("/outlets")).json()["data"]] == [c.SEED_OUTLET_MAIN_ID]
    with_deleted = h.get(h.path("/outlets"), query={"deleted": "true"}).json()["data"]
    assert {row["id"] for row in with_deleted} == {c.SEED_OUTLET_MAIN_ID, c.SEED_OUTLET_SECOND_ID}


def test_the_read_list_query_helper_refuses_junk() -> None:
    from types import SimpleNamespace

    from vendorfake.lightspeed.versioning import read_list_query

    args = SimpleNamespace(query=lambda name: "nope" if name == "before" else None)
    with pytest.raises(UnitError):
        read_list_query(args)  # type: ignore[arg-type]
