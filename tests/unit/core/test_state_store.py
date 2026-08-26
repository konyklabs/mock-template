"""Semantics of the state engine.

Weighted towards the three things a reviewer could disagree about and a
coverage-shaped suite would miss: the copy discipline (mutate what you were
handed, prove committed state did not move), the journal's exact contents
(``changed`` is published at ``/__unit/journal``), and the cursor grammar.
"""

from __future__ import annotations

import base64
import json

import pytest

from vendorfake.core.kernel.types import JournalEntry, UnitError, UnitErrorKind
from vendorfake.core.state.store import (
    DEFAULT_CURSOR_TTL_MS,
    Entity,
    IdempotencyRecord,
    Store,
    diff_keys,
)
from vendorfake.core.time.clock import Clock

START = "2024-01-01T00:00:00Z"


def make_store() -> Store:
    """A store on a virtual clock, so every timestamp in a test is a decision."""
    return Store(Clock(mode="virtual", start=START))


def seeded() -> Store:
    store = make_store()
    orders = store.collection("orders")
    orders.insert({"id": "o1", "state": "OPEN", "total": 500})
    orders.insert({"id": "o2", "state": "DRAFT", "total": 0})
    return store


# ---------------------------------------------------------------------------
# The copy discipline. This is the gate: a count of deepcopy sites proves
# nothing, a mutation that fails to reach committed state proves everything.
# ---------------------------------------------------------------------------


def test_mutating_the_result_of_get_does_not_change_committed_state() -> None:
    """The named failure: if `get` returned the live dict, a handler writing to
    the object it was handed would commit with no version bump, no updated_at,
    no journal entry and therefore no webhook."""
    store = seeded()
    before = store.entity_digest()
    got = store.collection("orders").get("o1")
    assert got is not None
    got["total"] = 999_999
    got["smuggled"] = True
    assert store.entity_digest() == before
    assert store.collection("orders").require("o1")["total"] == 500
    assert store.journal_seq == 2


def test_mutating_the_result_of_every_read_leaves_the_digest_alone() -> None:
    store = seeded()
    before = store.entity_digest()

    for entity in store.collection("orders").all():
        entity["total"] = -1
    found = store.collection("orders").find(lambda e: e["id"] == "o1")
    assert found is not None
    found["state"] = "MANGLED"
    for entity in store.collection("orders").filter(lambda e: True):
        entity.clear()
    required = store.collection("orders").require("o2")
    required["total"] = 42

    assert store.entity_digest() == before


def test_mutating_the_result_of_journal_leaves_the_journal_alone() -> None:
    store = make_store()
    store.collection("orders").insert({"id": "o1"}, meta={"operation_id": "CreateOrder"})
    for entry in store.journal():
        assert entry.meta is not None
        entry.meta["operation_id"] = "Forged"
    assert store.journal()[0].meta == {"operation_id": "CreateOrder"}


def test_mutating_a_snapshot_after_taking_it_leaves_the_store_alone() -> None:
    store = seeded()
    before = store.entity_digest()
    snap = store.snapshot()
    snap.collections["orders"]["o1"]["total"] = 1
    snap.collections["orders"]["o3"] = {"id": "o3"}
    assert store.entity_digest() == before


def test_mutating_a_snapshot_after_restoring_from_it_leaves_the_store_alone() -> None:
    store = seeded()
    snap = store.snapshot()
    fresh = make_store()
    fresh.restore(snap)
    after_restore = fresh.entity_digest()
    snap.collections["orders"]["o1"]["total"] = 1
    assert fresh.entity_digest() == after_restore


def test_the_mutator_argument_is_a_private_draft_until_it_returns() -> None:
    store = seeded()
    seen: list[Entity] = []

    def mutate(draft: Entity) -> None:
        draft["total"] = 700
        # Committed state must still be the old value while the draft is open.
        live = store.collection("orders").require("o1")
        seen.append(live)

    store.collection("orders").update("o1", mutate)
    assert seen[0]["total"] == 500
    assert store.collection("orders").require("o1")["total"] == 700


def test_a_predicate_cannot_reach_stored_state() -> None:
    """The reference hands `find`'s predicate the live object; this does not."""
    store = seeded()
    before = store.entity_digest()

    def greedy(entity: Entity) -> bool:
        entity["total"] = 0
        return False

    assert store.collection("orders").find(greedy) is None
    assert store.entity_digest() == before


def test_the_entity_passed_to_insert_is_not_aliased() -> None:
    store = make_store()
    supplied: Entity = {"id": "o1", "tags": ["a"]}
    store.collection("orders").insert(supplied)
    supplied["tags"].append("b")
    assert store.collection("orders").require("o1")["tags"] == ["a"]


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


def test_insert_defaults_version_and_timestamps() -> None:
    store = make_store()
    created = store.collection("orders").insert({"id": "o1"})
    assert created["version"] == 1
    assert created["created_at"] == "2024-01-01T00:00:00.000Z"
    assert created["updated_at"] == "2024-01-01T00:00:00.000Z"


def test_insert_honours_supplied_version_and_timestamps_for_seeding() -> None:
    store = make_store()
    created = store.collection("orders").insert(
        {"id": "o1", "version": 4, "created_at": "2023-05-02T10:00:00.000Z", "updated_at": "2023-05-03T10:00:00.000Z"}
    )
    assert created["version"] == 4
    assert created["created_at"] == "2023-05-02T10:00:00.000Z"
    assert store.journal()[0].to_version == 4


def test_insert_journals_every_key_in_insertion_order_not_sorted() -> None:
    store = make_store()
    store.collection("orders").insert({"id": "o1", "zeta": 1, "alpha": 2})
    entry = store.journal()[0]
    assert list(entry.changed) == ["id", "zeta", "alpha", "version", "created_at", "updated_at"]
    assert entry.op == "insert"
    assert entry.from_version is None
    assert entry.to_version == 1


def test_duplicate_insert_is_a_conflict_and_journals_nothing() -> None:
    store = make_store()
    store.collection("orders").insert({"id": "o1"})
    with pytest.raises(UnitError) as raised:
        store.collection("orders").insert({"id": "o1"})
    assert raised.value.kind is UnitErrorKind.CONFLICT
    assert raised.value.detail == "orders 'o1' already exists"
    assert raised.value.info == {"collection": "orders", "id": "o1"}
    assert store.journal_seq == 1


def test_insert_without_a_usable_id_is_a_programming_error_not_a_shaped_one() -> None:
    store = make_store()
    with pytest.raises(ValueError, match="non-empty string 'id'"):
        store.collection("orders").insert({"state": "OPEN"})
    with pytest.raises(ValueError):
        store.collection("orders").insert({"id": ""})


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_bumps_the_version_by_exactly_one_and_restamps() -> None:
    store = seeded()
    store.clock.advance(1500)
    updated = store.collection("orders").update("o1", lambda d: d.__setitem__("total", 600))
    assert updated["version"] == 2
    assert updated["updated_at"] == "2024-01-01T00:00:01.500Z"
    assert updated["created_at"] == "2024-01-01T00:00:00.000Z"


def test_changed_excludes_version_and_updated_at_and_is_sorted() -> None:
    store = seeded()
    store.collection("orders").update("o1", lambda d: d.update({"total": 600, "note": "x", "aaa": 1}))
    entry = store.journal(since_seq=2)[0]
    assert list(entry.changed) == ["aaa", "note", "total"]
    assert entry.from_version == 1
    assert entry.to_version == 2
    assert entry.op == "update"


def test_a_mutator_that_raises_commits_nothing_and_journals_nothing() -> None:
    store = seeded()
    before = store.entity_digest()

    def boom(draft: Entity) -> None:
        draft["total"] = 1
        raise RuntimeError("handler blew up")

    with pytest.raises(RuntimeError):
        store.collection("orders").update("o1", boom)
    assert store.entity_digest() == before
    assert store.journal_seq == 2


def test_expect_version_mismatch_is_a_version_conflict_and_leaves_no_trace() -> None:
    store = seeded()
    before = store.entity_digest()
    with pytest.raises(UnitError) as raised:
        store.collection("orders").update("o1", lambda d: d.__setitem__("total", 1), expect_version=7)
    err = raised.value
    assert err.kind is UnitErrorKind.VERSION_CONFLICT
    assert err.detail == "Supplied version 7 does not match the current version 1 of orders o1."
    assert err.info == {"collection": "orders", "id": "o1", "supplied": 7, "current": 1}
    assert store.entity_digest() == before
    assert store.journal_seq == 2


def test_expect_version_none_means_no_opinion_and_zero_means_zero() -> None:
    """`None` and `0` are different requests; a falsy test would merge them."""
    store = make_store()
    store.collection("orders").insert({"id": "o1", "version": 0})
    store.collection("orders").update("o1", lambda d: d.__setitem__("total", 1), expect_version=None)
    assert store.collection("orders").require("o1")["version"] == 1
    store.collection("orders").insert({"id": "o2", "version": 0})
    store.collection("orders").update("o2", lambda d: d.__setitem__("total", 1), expect_version=0)
    assert store.collection("orders").require("o2")["version"] == 1


def test_silent_writes_without_bumping_journalling_or_notifying() -> None:
    """The one deliberate hole, and it has a name: no journal entry means no
    webhook, which is what internal bookkeeping needs."""
    store = seeded()
    heard: list[JournalEntry] = []
    store.on_journal(heard.append)
    updated = store.collection("orders").update(
        "o1", lambda d: d.__setitem__("superseded_at", "2024-01-01T00:00:00.000Z"), silent=True
    )
    assert updated["version"] == 1
    assert updated["updated_at"] == "2024-01-01T00:00:00.000Z"
    assert store.collection("orders").require("o1")["superseded_at"] == "2024-01-01T00:00:00.000Z"
    assert heard == []
    assert store.journal_seq == 2


def test_a_mutator_cannot_rewrite_id_or_created_at() -> None:
    store = seeded()

    def tamper(draft: Entity) -> None:
        draft["id"] = "o-forged"
        draft["created_at"] = "1999-01-01T00:00:00.000Z"

    updated = store.collection("orders").update("o1", tamper)
    assert updated["id"] == "o1"
    assert updated["created_at"] == "2024-01-01T00:00:00.000Z"
    assert store.collection("orders").get("o-forged") is None


def test_update_of_a_missing_entity_is_not_found() -> None:
    store = make_store()
    with pytest.raises(UnitError) as raised:
        store.collection("orders").update("nope", lambda d: None)
    assert raised.value.kind is UnitErrorKind.NOT_FOUND
    assert raised.value.detail == "orders 'nope' not found"


def test_clearing_a_field_with_pop_reports_it_changed_and_removes_it() -> None:
    """Absent is absent: the reference deletes the key, and so must this."""
    store = make_store()
    store.collection("orders").insert({"id": "o1", "reference_id": "R"})
    updated = store.collection("orders").update("o1", lambda d: d.pop("reference_id"))
    assert "reference_id" not in updated
    assert list(store.journal(since_seq=1)[0].changed) == ["reference_id"]


# ---------------------------------------------------------------------------
# diff_keys: absence is not null
# ---------------------------------------------------------------------------


def test_diff_keys_tells_an_absent_key_from_one_explicitly_set_to_none() -> None:
    """`a.get(k)` would call these equal and publish a journal entry that
    disagrees with the mutation it describes."""
    assert diff_keys({"a": 1}, {"a": 1, "note": None}) == ["note"]
    assert diff_keys({"a": 1, "note": None}, {"a": 1}) == ["note"]
    assert diff_keys({"a": 1, "note": None}, {"a": 1, "note": None}) == []


def test_diff_keys_ignores_version_and_updated_at() -> None:
    before = {"version": 1, "updated_at": "a", "total": 1}
    after = {"version": 2, "updated_at": "b", "total": 1}
    assert diff_keys(before, after) == []


def test_diff_keys_compares_by_value_not_identity_and_sorts_by_code_point() -> None:
    before = {"Zb": [1, 2], "aA": {"x": 1}}
    after = {"Zb": [1, 2], "aA": {"x": 2}, "b": 1}
    assert diff_keys(before, after) == ["aA", "b"]
    assert diff_keys({"Zb": 1, "aA": 1}, {"Zb": 2, "aA": 2}) == ["Zb", "aA"]


def test_diff_keys_compares_nested_values_canonically() -> None:
    """Key order inside a nested object is not a change."""
    assert diff_keys({"m": {"a": 1, "b": 2}}, {"m": {"b": 2, "a": 1}}) == []


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_journals_and_reports_true() -> None:
    store = seeded()
    assert store.collection("orders").delete("o1", meta={"why": "test"}) is True
    entry = store.journal(since_seq=2)[0]
    assert entry.op == "delete"
    assert entry.from_version == 1
    assert entry.to_version is None
    assert list(entry.changed) == []
    assert entry.meta == {"why": "test"}


def test_deleting_what_is_not_there_journals_nothing() -> None:
    store = seeded()
    assert store.collection("orders").delete("nope") is False
    assert store.journal_seq == 2


# ---------------------------------------------------------------------------
# The journal as event source
# ---------------------------------------------------------------------------


def test_a_listener_runs_synchronously_inside_the_mutation_and_sees_it_committed() -> None:
    """An event cannot exist for a mutation that did not commit, because the
    listener is called after the map is written and before insert() returns."""
    store = make_store()
    observed: list[tuple[int, Entity | None]] = []

    def listener(entry: JournalEntry) -> None:
        observed.append((entry.seq, store.collection("orders").get(entry.id)))

    store.on_journal(listener)
    store.collection("orders").insert({"id": "o1", "state": "OPEN"})
    assert len(observed) == 1
    seq, committed = observed[0]
    assert seq == 1
    assert committed is not None
    assert committed["state"] == "OPEN"


def test_listeners_fire_in_registration_order_once_per_entry() -> None:
    store = make_store()
    calls: list[str] = []
    store.on_journal(lambda e: calls.append(f"first:{e.seq}"))
    store.on_journal(lambda e: calls.append(f"second:{e.seq}"))
    store.collection("orders").insert({"id": "o1"})
    store.collection("orders").update("o1", lambda d: d.__setitem__("x", 1))
    assert calls == ["first:1", "second:1", "first:2", "second:2"]


def test_journal_seq_starts_at_one_and_is_strictly_increasing() -> None:
    store = make_store()
    orders = store.collection("orders")
    orders.insert({"id": "o1"})
    orders.insert({"id": "o2"})
    orders.update("o1", lambda d: d.__setitem__("x", 1))
    orders.delete("o2")
    assert [e.seq for e in store.journal()] == [1, 2, 3, 4]
    assert store.journal_seq == 4


def test_journal_since_seq_returns_only_what_follows() -> None:
    store = seeded()
    assert [e.id for e in store.journal(since_seq=1)] == ["o2"]
    assert store.journal(since_seq=99) == []


def test_journal_entries_carry_the_clock_not_the_wall() -> None:
    store = make_store()
    store.clock.advance(2000)
    store.collection("orders").insert({"id": "o1"})
    assert store.journal()[0].at == "2024-01-01T00:00:02.000Z"


def test_meta_is_copied_so_a_caller_cannot_rewrite_history() -> None:
    store = make_store()
    meta = {"operation_id": "CreateOrder"}
    store.collection("orders").insert({"id": "o1"}, meta=meta)
    meta["operation_id"] = "Forged"
    assert store.journal()[0].meta == {"operation_id": "CreateOrder"}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_require_raises_not_found_where_get_returns_none() -> None:
    store = make_store()
    assert store.collection("orders").get("o1") is None
    with pytest.raises(UnitError) as raised:
        store.collection("orders").require("o1")
    assert raised.value.kind is UnitErrorKind.NOT_FOUND
    assert raised.value.info == {"collection": "orders", "id": "o1"}


def test_find_returns_the_first_match_in_insertion_order() -> None:
    """Load-bearing wherever two records can share a lookup key -- a superseded
    OAuth token and its replacement, for one."""
    store = make_store()
    tokens = store.collection("tokens")
    tokens.insert({"id": "t1", "refresh": "R", "superseded": True})
    tokens.insert({"id": "t2", "refresh": "R", "superseded": False})
    found = tokens.find(lambda e: e["refresh"] == "R")
    assert found is not None
    assert found["id"] == "t1"


def test_has_and_size_read_committed_state() -> None:
    store = seeded()
    assert store.collection("orders").has("o1") is True
    assert store.collection("orders").has("nope") is False
    assert store.collection("orders").size == 2


def test_all_preserves_insertion_order() -> None:
    store = seeded()
    assert [e["id"] for e in store.collection("orders").all()] == ["o1", "o2"]


def test_the_collection_wrapper_is_cached_per_name() -> None:
    store = make_store()
    assert store.collection("orders") is store.collection("orders")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def rows(n: int) -> list[Entity]:
    return [{"id": f"r{i}"} for i in range(n)]


def test_limit_is_clamped_into_one_to_max() -> None:
    store = make_store()
    page = store.collection("orders")
    assert len(page.paginate(rows(50), limit=0).items) == 1
    assert len(page.paginate(rows(50), limit=-9).items) == 1
    assert len(page.paginate(rows(5000), limit=9999).items) == 1000
    assert len(page.paginate(rows(500)).items) == 100


def test_a_next_cursor_appears_only_when_there_is_a_next_page() -> None:
    store = make_store()
    coll = store.collection("orders")
    assert coll.paginate(rows(3), limit=3).cursor is None
    assert coll.paginate(rows(4), limit=3).cursor is not None
    assert coll.paginate([], limit=3).cursor is None


def test_paging_walks_every_row_exactly_once() -> None:
    store = make_store()
    coll = store.collection("orders")
    data = rows(7)
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = coll.paginate(data, limit=3, cursor=cursor, fingerprint={"q": "x"})
        seen.extend(row["id"] for row in page.items)
        cursor = page.cursor
        if cursor is None:
            break
    assert seen == [row["id"] for row in data]


def test_the_cursor_is_unpadded_base64url_of_o_q_t_in_that_order() -> None:
    store = make_store()
    page = store.collection("orders").paginate(rows(10), limit=4, fingerprint={"q": "x"})
    assert page.cursor is not None
    assert "=" not in page.cursor
    decoded = base64.urlsafe_b64decode(page.cursor + "=" * (-len(page.cursor) % 4))
    assert decoded.startswith(b'{"o":4,"q":"')
    payload = json.loads(decoded)
    assert set(payload) == {"o", "q", "t"}
    assert len(payload["q"]) == 16
    assert isinstance(payload["t"], int)


def test_a_cursor_from_a_different_query_is_refused() -> None:
    store = make_store()
    coll = store.collection("orders")
    first = coll.paginate(rows(10), limit=4, fingerprint={"state": "OPEN"})
    assert first.cursor is not None
    with pytest.raises(UnitError) as raised:
        coll.paginate(rows(10), limit=4, cursor=first.cursor, fingerprint={"state": "CLOSED"})
    assert raised.value.kind is UnitErrorKind.INVALID_CURSOR
    assert raised.value.field == "cursor"
    assert "issued for a different query" in (raised.value.detail or "")


def test_a_cursor_expires_after_the_documented_lifetime_and_not_before() -> None:
    store = make_store()
    coll = store.collection("orders")
    issued = coll.paginate(rows(10), limit=4, fingerprint=None).cursor
    assert issued is not None
    store.clock.advance(DEFAULT_CURSOR_TTL_MS)
    coll.paginate(rows(10), limit=4, cursor=issued)  # exactly at the limit: still good
    store.clock.advance(1)
    with pytest.raises(UnitError) as raised:
        coll.paginate(rows(10), limit=4, cursor=issued)
    assert raised.value.detail == "The provided cursor has expired."


@pytest.mark.parametrize(
    "bad",
    [
        "not-base64-!!!",
        base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode(),
        base64.urlsafe_b64encode(b'{"o":"2","q":"a","t":1}').rstrip(b"=").decode(),
        base64.urlsafe_b64encode(b'{"o":true,"q":"a","t":1}').rstrip(b"=").decode(),
        base64.urlsafe_b64encode(b'{"o":-5,"q":"a","t":1}').rstrip(b"=").decode(),
        base64.urlsafe_b64encode(b'{"o":2,"q":1,"t":1}').rstrip(b"=").decode(),
        base64.urlsafe_b64encode(b'{"o":2,"q":"a"}').rstrip(b"=").decode(),
        base64.urlsafe_b64encode(b"[1,2,3]").rstrip(b"=").decode(),
    ],
)
def test_an_unparseable_or_forged_cursor_is_invalid_cursor(bad: str) -> None:
    store = make_store()
    with pytest.raises(UnitError) as raised:
        store.collection("orders").paginate(rows(10), limit=4, cursor=bad)
    assert raised.value.kind is UnitErrorKind.INVALID_CURSOR
    assert raised.value.detail == "The provided cursor could not be parsed."


def test_a_fingerprint_of_none_and_an_omitted_fingerprint_are_the_same_query() -> None:
    store = make_store()
    coll = store.collection("orders")
    issued = coll.paginate(rows(10), limit=4).cursor
    assert issued is not None
    assert coll.paginate(rows(10), limit=4, cursor=issued, fingerprint=None).items[0]["id"] == "r4"


# ---------------------------------------------------------------------------
# entity_digest
# ---------------------------------------------------------------------------


def test_two_identically_seeded_stores_agree_despite_different_start_instants() -> None:
    a = Store(Clock(mode="virtual", start="2024-01-01T00:00:00Z"))
    b = Store(Clock(mode="virtual", start="1999-12-31T23:59:59Z"))
    for store in (a, b):
        store.collection("orders").insert({"id": "o1", "state": "OPEN"})
    assert a.entity_digest() == b.entity_digest()


def test_reading_a_collection_materialises_it_but_does_not_change_the_digest() -> None:
    store = seeded()
    before = store.entity_digest()
    assert store.collection("payments").all() == []
    assert "payments" in store.stats()
    assert store.entity_digest() == before


def test_a_collection_emptied_by_deletion_stops_counting_towards_the_digest() -> None:
    store = make_store()
    empty = store.entity_digest()
    store.collection("orders").insert({"id": "o1"})
    store.collection("orders").delete("o1")
    assert store.entity_digest() == empty


def test_volatile_fields_are_excluded_and_vendors_can_add_more() -> None:
    """Two units that differ only in a wall-clock stamp must hash the same;
    `version` is not volatile, so the comparison is made between two stores
    rather than across an update, which would move the version too."""

    def token_store(expires_at: str, scope: str) -> Store:
        store = make_store()
        store.mark_volatile("expires_at")
        store.collection("tokens").insert({"id": "t1", "expires_at": expires_at, "scope": scope})
        return store

    baseline = token_store("2024-01-01T01:00:00Z", "READ")
    assert token_store("2030-06-06T06:06:06Z", "READ").entity_digest() == baseline.entity_digest()
    assert token_store("2024-01-01T01:00:00Z", "WRITE").entity_digest() != baseline.entity_digest()

    # created_at/updated_at are excluded without any vendor declaring them.
    unmarked = make_store()
    unmarked.collection("tokens").insert({"id": "t1", "expires_at": "2024-01-01T01:00:00Z", "scope": "READ"})
    later = make_store()
    later.clock.advance(90_000)
    later.collection("tokens").insert({"id": "t1", "expires_at": "2024-01-01T01:00:00Z", "scope": "READ"})
    assert unmarked.entity_digest() == later.entity_digest()


def test_the_digest_sorts_by_code_point_not_by_locale() -> None:
    """`["Zb", "aA"]` is the fixture because ICU puts "aA" first and code point
    puts "Zb" first, so a locale-collating port produces a different hash."""
    store = make_store()
    store.collection("orders").insert({"id": "aA"})
    store.collection("orders").insert({"id": "Zb"})

    mirror = make_store()
    mirror.collection("orders").insert({"id": "Zb"})
    mirror.collection("orders").insert({"id": "aA"})

    assert store.entity_digest() == mirror.entity_digest()

    expected = {"orders": {"Zb": {"id": "Zb", "version": 1}, "aA": {"id": "aA", "version": 1}}}
    assert store.entity_digest() == _digest_of_literal(expected)


def _digest_of_literal(value: object) -> str:
    from vendorfake.core.util.json import canonical_json, sha256_hex

    return sha256_hex(canonical_json(value))


def test_collection_names_sort_by_code_point_too() -> None:
    store = make_store()
    store.collection("Zb").insert({"id": "x"})
    store.collection("aA").insert({"id": "y"})
    expected = {"Zb": {"x": {"id": "x", "version": 1}}, "aA": {"y": {"id": "y", "version": 1}}}
    assert store.entity_digest() == _digest_of_literal(expected)


def test_the_journal_is_not_part_of_the_digest() -> None:
    store = make_store()
    store.collection("orders").insert({"id": "o1", "n": 0})
    store.collection("orders").update("o1", lambda d: d.__setitem__("n", 1))
    store.collection("orders").update("o1", lambda d: d.__setitem__("n", 0))
    settled = store.entity_digest()

    fresh = make_store()
    fresh.collection("orders").insert({"id": "o1", "n": 0})
    fresh.collection("orders").update("o1", lambda d: d.__setitem__("n", 0))

    # Same field values, different version numbers -- version IS state, so
    # these must differ; the point is that the four extra journal entries are
    # not what makes them differ.
    assert settled != fresh.entity_digest()
    fresh.collection("orders").update("o1", lambda d: d.__setitem__("n", 0))
    assert settled == fresh.entity_digest()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_keys_are_sorted_regardless_of_materialisation_order() -> None:
    """Unsorted in the reference, which makes two units publish a different key
    order at /__unit/info for no reason a byte comparison can forgive."""
    a = make_store()
    a.collection("zeta").insert({"id": "1"})
    a.collection("alpha").insert({"id": "2"})
    b = make_store()
    b.collection("alpha").insert({"id": "2"})
    b.collection("zeta").insert({"id": "1"})
    assert list(a.stats()) == ["alpha", "zeta"]
    assert a.stats() == b.stats()
    assert a.stats() == {"alpha": 1, "zeta": 1}


# ---------------------------------------------------------------------------
# Idempotency records
# ---------------------------------------------------------------------------


def record(key: str = "k1") -> IdempotencyRecord:
    return IdempotencyRecord(
        scope="CreateOrder",
        key=key,
        request_digest="abc",
        status=200,
        headers={"content-type": "application/json"},
        body_b64="e30",
        stored_at="2024-01-01T00:00:00.000Z",
    )


def test_idempotency_is_scoped_so_one_key_on_two_operations_does_not_collide() -> None:
    store = make_store()
    assert Store.idempotency_key("CreateOrder", "k1") == "CreateOrder k1"
    store.put_idempotent(record())
    assert store.get_idempotent("CreateOrder", "k1") is not None
    assert store.get_idempotent("PayOrder", "k1") is None


def test_a_stored_idempotency_record_is_not_aliased_to_the_caller() -> None:
    store = make_store()
    rec = record()
    store.put_idempotent(rec)
    rec.headers["content-type"] = "text/plain"
    stored = store.get_idempotent("CreateOrder", "k1")
    assert stored is not None
    assert stored.headers == {"content-type": "application/json"}


# ---------------------------------------------------------------------------
# snapshot / restore / reset
# ---------------------------------------------------------------------------


def test_snapshot_restore_round_trips_entities_journal_idempotency_and_seq() -> None:
    store = seeded()
    store.put_idempotent(record())
    snap = store.snapshot()

    fresh = make_store()
    fresh.restore(snap)
    assert fresh.entity_digest() == store.entity_digest()
    assert [e.seq for e in fresh.journal()] == [1, 2]
    assert fresh.journal_seq == 2
    assert fresh.get_idempotent("CreateOrder", "k1") is not None
    # The restored seq continues rather than colliding.
    fresh.collection("orders").insert({"id": "o3"})
    assert fresh.journal_seq == 3


def test_restore_does_not_replay_the_journal_to_listeners() -> None:
    """Replaying would deliver every event in the snapshot a second time."""
    store = seeded()
    snap = store.snapshot()
    fresh = make_store()
    heard: list[JournalEntry] = []
    fresh.on_journal(heard.append)
    fresh.restore(snap)
    assert heard == []


def test_reset_empties_everything_and_keeps_listeners() -> None:
    store = seeded()
    store.put_idempotent(record())
    heard: list[JournalEntry] = []
    store.on_journal(heard.append)
    store.reset()
    assert store.journal() == []
    assert store.journal_seq == 0
    assert store.stats() == {}
    assert store.get_idempotent("CreateOrder", "k1") is None
    assert store.entity_digest() == make_store().entity_digest()
    store.collection("orders").insert({"id": "o9"})
    assert [e.seq for e in heard] == [1]


def test_restore_replaces_rather_than_merges() -> None:
    store = seeded()
    snap = store.snapshot()
    store.collection("payments").insert({"id": "p1"})
    store.restore(snap)
    assert store.stats() == {"orders": 2}
