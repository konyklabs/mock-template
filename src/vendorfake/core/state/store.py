"""The state engine: entities in named collections, an append-only journal of every
committed mutation, and the idempotency records that make a retried request safe.

**The journal is the event source.** An entry is appended only after the map is written
and every listener runs synchronously from :meth:`Store.append_journal`, so an event
cannot exist for a mutation that did not commit and a handler cannot forget to emit one.
A ``silent=True`` update journals nothing, the one way to change an entity quietly.

**Every read and write goes through a deep copy; a handler can never alias committed
state**, and under-copying would mutate committed state with no version bump and no
webhook. Every ordering is by code point (``sorted``), never locale collation. The
store's lock is its own, independent of the pipeline lock; journal listeners run with it
held, so **a journal listener must not block**.
"""

from __future__ import annotations

import binascii
import copy
import json
import math
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeAlias, TypeVar

from vendorfake.core.kernel.types import JournalEntry, UnitError, UnitErrorKind
from vendorfake.core.time.clock import Clock
from vendorfake.core.util.b64 import b64url_decode, b64url_encode
from vendorfake.core.util.json import MISSING, canonical_json, digest_of, dump_json, sha256_hex

__all__ = [
    "DEFAULT_CURSOR_TTL_MS",
    "IDEMPOTENCY_CAPACITY",
    "JOURNAL_CAPACITY",
    "VOLATILE_PRESENT",
    "Collection",
    "Entity",
    "IdempotencyRecord",
    "JournalListener",
    "Page",
    "Store",
    "StoreSnapshot",
    "diff_keys",
]

#: A plain dict, never a Pydantic model; ``tools/boundary.toml`` records the rule.
Entity: TypeAlias = dict[str, Any]

_T = TypeVar("_T")

DEFAULT_CURSOR_TTL_MS = 5 * 60 * 1000

JOURNAL_CAPACITY = 10_000
"""Journal entries kept, oldest evicted; ``journal_seq`` still counts every one."""

IDEMPOTENCY_CAPACITY = 10_000
"""Idempotency records kept, evicting the oldest inserted."""

_DIFF_EXCLUDED = frozenset({"version", "updated_at"})


@dataclass(frozen=True, slots=True)
class Page(Generic[_T]):
    items: list[_T]
    #: ``None`` when this page is the last; a vendor projection drops the key.
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """A response held so that a retried request replays instead of repeating."""

    scope: str
    key: str
    #: Canonical-JSON digest of the request body the key was first seen with.
    request_digest: str
    status: int
    headers: dict[str, str]
    #: The stored response body, base64url, so a snapshot survives JSON.
    body_b64: str
    stored_at: str


@dataclass(frozen=True, slots=True)
class StoreSnapshot:
    collections: dict[str, dict[str, Entity]]
    journal: list[JournalEntry]
    idempotency: list[IdempotencyRecord]
    seq: int


JournalListener = Callable[[JournalEntry], None]


@dataclass(frozen=True, slots=True)
class _CursorPayload:
    """The decoded contents of a pagination cursor: offset, query, issued-at."""

    o: int
    q: str
    t: int


def diff_keys(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Field names differing between two versions of an entity, sorted by code point and
    excluding ``version``/``updated_at``. Absence is compared through ``MISSING``, not
    ``dict.get``, so a removed key and a key set to null stay different."""
    changed: list[str] = []
    for key in set(before) | set(after):
        if key in _DIFF_EXCLUDED:
            continue
        left = before.get(key, MISSING)
        right = after.get(key, MISSING)
        if left is MISSING or right is MISSING:
            if left is not right:
                changed.append(key)
            continue
        if canonical_json(left) != canonical_json(right):
            changed.append(key)
    return sorted(changed)


def _encode_cursor(payload: _CursorPayload) -> str:
    return b64url_encode(dump_json({"o": payload.o, "q": payload.q, "t": payload.t}))


def _decode_cursor(text: str) -> _CursorPayload | None:
    """Parse a cursor, or return ``None``; every rejection funnels to one
    ``invalid_cursor`` at the call site. A negative offset is refused because slicing
    would count it from the end and return the tail of the collection."""
    try:
        raw = b64url_decode(text)
        parsed = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    offset = parsed.get("o")
    query = parsed.get("q")
    issued = parsed.get("t")
    # ``isinstance(x, int)`` is true for ``True``; a JSON boolean is not a number.
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return None
    if not isinstance(query, str):
        return None
    if isinstance(issued, bool) or not isinstance(issued, int | float):
        return None
    return _CursorPayload(o=offset, q=query, t=int(issued))


class Collection:
    """One named collection of versioned entities. Every read hands back a private copy and
    every write takes one, which makes "a rejected update leaves no trace" true."""

    __slots__ = ("_store", "name")

    def __init__(self, name: str, store: Store) -> None:
        self.name = name
        self._store = store

    @property
    def _map(self) -> dict[str, Entity]:
        return self._store.raw(self.name)

    def insert(self, entity: Mapping[str, Any], meta: Mapping[str, Any] | None = None) -> Entity:
        """Add a new entity and journal it. A duplicate id raises ``conflict``.

        ``version`` defaults to 1 and the stamps to now; all three may be supplied so a
        seed can state an older entity. ``changed`` is every key of the created entity,
        in insertion order rather than sorted."""
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or entity_id == "":
            # A missing id is a vendor defect: the pipeline turns this into `internal`.
            raise ValueError(f"{self.name}: an entity needs a non-empty string 'id', got {entity_id!r}")
        with self._store.lock:
            now = self._store.clock.iso_ms()
            if entity_id in self._map:
                raise UnitError(
                    UnitErrorKind.CONFLICT,
                    detail=f"{self.name} '{entity_id}' already exists",
                    info={"collection": self.name, "id": entity_id},
                )
            created: Entity = dict(entity)
            created["version"] = entity.get("version", 1)
            created["created_at"] = entity.get("created_at", now)
            created["updated_at"] = entity.get("updated_at", now)
            self._map[entity_id] = copy.deepcopy(created)
            self._store.append_journal(
                collection=self.name,
                entity_id=entity_id,
                op="insert",
                from_version=None,
                to_version=created["version"],
                changed=list(created),
                meta=meta,
            )
            return copy.deepcopy(created)

    def update(
        self,
        entity_id: str,
        mutate: Callable[[Entity], None],
        *,
        expect_version: int | None = None,
        meta: Mapping[str, Any] | None = None,
        silent: bool = False,
    ) -> Entity:
        """Read-modify-write under optimistic concurrency. The mutator sees a private copy,
        so nothing is committed or journalled if it raises. ``changed`` is diffed **before**
        the version bump; ``id`` and ``created_at`` are restored afterwards, so a mutator
        cannot rewrite either. ``expect_version`` is checked only when not ``None``, since
        "no opinion" and "must be version 0" differ. ``silent=True`` bumps nothing and
        journals nothing, for internal bookkeeping."""
        with self._store.lock:
            current = self._map.get(entity_id)
            if current is None:
                raise UnitError(
                    UnitErrorKind.NOT_FOUND,
                    detail=f"{self.name} '{entity_id}' not found",
                    info={"collection": self.name, "id": entity_id},
                )
            if expect_version is not None and expect_version != current["version"]:
                raise UnitError(
                    UnitErrorKind.VERSION_CONFLICT,
                    detail=(
                        f"Supplied version {expect_version} does not match the current version "
                        f"{current['version']} of {self.name} {entity_id}."
                    ),
                    info={
                        "collection": self.name,
                        "id": entity_id,
                        "supplied": expect_version,
                        "current": current["version"],
                    },
                )
            draft: Entity = copy.deepcopy(current)
            mutate(draft)
            changed = diff_keys(current, draft)
            if not silent:
                draft["version"] = current["version"] + 1
                draft["updated_at"] = self._store.clock.iso_ms()
            draft["id"] = current.get("id", entity_id)
            if "created_at" in current:
                draft["created_at"] = current["created_at"]
            else:
                # Absent stays absent: `None` would put a null in the digest and on the wire.
                draft.pop("created_at", None)
            self._map[entity_id] = copy.deepcopy(draft)
            if not silent:
                self._store.append_journal(
                    collection=self.name,
                    entity_id=entity_id,
                    op="update",
                    from_version=current["version"],
                    to_version=draft["version"],
                    changed=changed,
                    meta=meta,
                )
            return copy.deepcopy(draft)

    def delete(self, entity_id: str, meta: Mapping[str, Any] | None = None) -> bool:
        """Remove an entity. Deleting what is not there returns ``False`` and journals nothing."""
        with self._store.lock:
            current = self._map.get(entity_id)
            if current is None:
                return False
            del self._map[entity_id]
            self._store.append_journal(
                collection=self.name,
                entity_id=entity_id,
                op="delete",
                from_version=current["version"],
                to_version=None,
                changed=[],
                meta=meta,
            )
            return True

    def get(self, entity_id: str) -> Entity | None:
        with self._store.lock:
            found = self._map.get(entity_id)
            return copy.deepcopy(found) if found is not None else None

    def require(self, entity_id: str) -> Entity:
        """:meth:`get`, but a miss raises ``not_found`` instead of returning ``None``."""
        found = self.get(entity_id)
        if found is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"{self.name} '{entity_id}' not found",
                info={"collection": self.name, "id": entity_id},
            )
        return found

    def has(self, entity_id: str) -> bool:
        with self._store.lock:
            return entity_id in self._map

    def all(self) -> list[Entity]:
        with self._store.lock:
            return [copy.deepcopy(entity) for entity in self._map.values()]

    def find(self, predicate: Callable[[Entity], bool]) -> Entity | None:
        """The first entity satisfying ``predicate``, in insertion order -- load bearing
        wherever two records share a lookup key. The predicate is given a copy."""
        with self._store.lock:
            for entity in self._map.values():
                candidate = copy.deepcopy(entity)
                if predicate(candidate):
                    return candidate
            return None

    def filter(self, predicate: Callable[[Entity], bool]) -> list[Entity]:
        return [entity for entity in self.all() if predicate(entity)]

    @property
    def size(self) -> int:
        with self._store.lock:
            return len(self._map)

    def paginate(
        self,
        items: Sequence[_T],
        *,
        limit: int | None = None,
        cursor: str | None = None,
        fingerprint: object = None,
        max_limit: int = 1000,
        default_limit: int = 100,
    ) -> Page[_T]:
        """Cursor pagination, reproducing three real vendor behaviours: the cursor is opaque
        (base64url of ``{"o","q","t"}``), carries a fingerprint of the query it was issued for
        so paging with a changed filter is refused rather than silently wrong, and expires.
        ``limit`` is clamped to ``[1, max_limit]`` and a next cursor is emitted only when there
        really is a next page. ``t`` is floored to whole milliseconds to stay byte-stable."""
        resolved_limit = min(max(default_limit if limit is None else limit, 1), max_limit)
        fp = digest_of(fingerprint)[:16]
        offset = 0
        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded is None:
                raise UnitError(
                    UnitErrorKind.INVALID_CURSOR,
                    detail="The provided cursor could not be parsed.",
                    field="cursor",
                )
            if decoded.q != fp:
                raise UnitError(
                    UnitErrorKind.INVALID_CURSOR,
                    detail=(
                        "The provided cursor was issued for a different query. Repeat the original query when paging."
                    ),
                    field="cursor",
                )
            if self._store.clock.now() - decoded.t > DEFAULT_CURSOR_TTL_MS:
                raise UnitError(
                    UnitErrorKind.INVALID_CURSOR,
                    detail="The provided cursor has expired.",
                    field="cursor",
                )
            offset = decoded.o
        page = list(items[offset : offset + resolved_limit])
        next_offset = offset + len(page)
        if next_offset < len(items):
            issued = math.floor(self._store.clock.now())
            return Page(items=page, cursor=_encode_cursor(_CursorPayload(o=next_offset, q=fp, t=issued)))
        return Page(items=page)


VOLATILE_PRESENT = "<set>"
"""What a set volatile field hashes as in :meth:`Store.entity_digest`."""


def _scrub_volatile(value: Any, volatile: set[str], opaque: set[str]) -> Any:
    """``value`` with every volatile field, at any depth, reduced to whether it is set.

    A subtree under an **opaque** name (:meth:`Store.mark_opaque`) is digested verbatim,
    because a volatile name inside a caller's free-form document is the caller's key;
    opaque wins over volatile. Only a **scalar** under a volatile name becomes the marker."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            if key in opaque:
                out[key] = inner
            elif isinstance(inner, Mapping | list | tuple):
                out[key] = _scrub_volatile(inner, volatile, opaque)
            elif key in volatile:
                if inner is not None:
                    out[key] = VOLATILE_PRESENT
            else:
                out[key] = inner
        return out
    if isinstance(value, list | tuple):
        return [_scrub_volatile(inner, volatile, opaque) for inner in value]
    return value


class Store:
    __slots__ = (
        "_collections",
        "_idempotency",
        "_journal",
        "_listeners",
        "_seq",
        "_wrappers",
        "clock",
        "lock",
        "opaque_fields",
        "volatile_fields",
    )

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        #: Re-entrant: a journal listener runs under it and may read the store.
        self.lock = threading.RLock()
        self._collections: dict[str, dict[str, Entity]] = {}
        self._wrappers: dict[str, Collection] = {}
        #: Bounded rings: the newest entries are the ones a test is asking about.
        self._journal: deque[JournalEntry] = deque(maxlen=JOURNAL_CAPACITY)
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._listeners: list[JournalListener] = []
        self._seq = 0
        #: Field names whose *values* :meth:`entity_digest` ignores, at any depth.
        self.volatile_fields: set[str] = {"created_at", "updated_at"}
        #: Caller free-form subtrees the digest takes verbatim; see :meth:`mark_opaque`.
        self.opaque_fields: set[str] = set()

    def mark_volatile(self, *fields: str) -> None:
        """Ignore further fields' values in the digest, at any depth except inside an opaque subtree."""
        with self.lock:
            self.volatile_fields.update(fields)

    def mark_opaque(self, *fields: str) -> None:
        """Declare caller free-form subtrees the digest takes verbatim. Matched at any depth,
        the name stops the scrub at that key and opaque wins over volatile."""
        with self.lock:
            self.opaque_fields.update(fields)

    def raw(self, name: str) -> dict[str, Entity]:
        """The live map behind a collection, materialising it on first read -- the one place a
        live map is exposed. Reading a collection creates it, which is why the digest skips
        empty collections."""
        with self.lock:
            existing = self._collections.get(name)
            if existing is None:
                existing = {}
                self._collections[name] = existing
            return existing

    def collection(self, name: str) -> Collection:
        with self.lock:
            wrapper = self._wrappers.get(name)
            if wrapper is None:
                wrapper = Collection(name, self)
                self._wrappers[name] = wrapper
            return wrapper

    def on_journal(self, listener: JournalListener) -> None:
        """Register a listener. It is called synchronously and must not block."""
        with self.lock:
            self._listeners.append(listener)

    def append_journal(
        self,
        *,
        collection: str,
        entity_id: str,
        op: Literal["insert", "update", "delete"],
        from_version: int | None,
        to_version: int | None,
        changed: Sequence[str],
        meta: Mapping[str, Any] | None = None,
    ) -> JournalEntry:
        """Append one committed mutation and dispatch it to every listener. Called only from
        :class:`Collection`, after the map is written, so a listener always observes committed
        state. ``seq`` starts at 1 and increases strictly until :meth:`reset`."""
        with self.lock:
            self._seq += 1
            entry = JournalEntry(
                seq=self._seq,
                at=self.clock.iso_ms(),
                collection=collection,
                id=entity_id,
                op=op,
                from_version=from_version,
                to_version=to_version,
                changed=tuple(changed),
                meta=dict(meta) if meta is not None else None,
            )
            self._journal.append(entry)
            for listener in self._listeners:
                listener(entry)
            return entry

    def journal(self, since_seq: int = 0) -> list[JournalEntry]:
        """Every retained entry after ``since_seq``, each a private copy; past
        :data:`JOURNAL_CAPACITY` the oldest are gone and this selects from what is left."""
        with self.lock:
            return [copy.deepcopy(e) for e in self._journal if e.seq > since_seq]

    @property
    def journal_seq(self) -> int:
        with self.lock:
            return self._seq

    @staticmethod
    def idempotency_key(scope: str, key: str) -> str:
        """``"scope key"``, so the same key on two operations does not collide."""
        return f"{scope} {key}"

    def get_idempotent(self, scope: str, key: str) -> IdempotencyRecord | None:
        with self.lock:
            return self._idempotency.get(self.idempotency_key(scope, key))

    def put_idempotent(self, record: IdempotencyRecord) -> None:
        with self.lock:
            self._idempotency[self.idempotency_key(record.scope, record.key)] = copy.deepcopy(record)
            self._evict_idempotency()

    def _evict_idempotency(self) -> None:
        """Drop the oldest inserted records past the cap, the caller holding the lock. A dict
        keeps insertion order and re-storing a key does not move it, which is what makes
        "oldest inserted" rather than "least recently used" true."""
        while len(self._idempotency) > IDEMPOTENCY_CAPACITY:
            del self._idempotency[next(iter(self._idempotency))]

    def snapshot(self) -> StoreSnapshot:
        with self.lock:
            return StoreSnapshot(
                collections={
                    name: {k: copy.deepcopy(v) for k, v in entities.items()}
                    for name, entities in self._collections.items()
                },
                journal=[copy.deepcopy(e) for e in self._journal],
                idempotency=[copy.deepcopy(r) for r in self._idempotency.values()],
                seq=self._seq,
            )

    def restore(self, snapshot: StoreSnapshot) -> None:
        """Replace everything the store holds, an over-long journal or idempotency set being
        trimmed to the caps as a live one is. Listeners are **not** notified, so a restored
        journal does not redeliver."""
        with self.lock:
            self._collections = {
                name: {k: copy.deepcopy(v) for k, v in entities.items()}
                for name, entities in snapshot.collections.items()
            }
            self._journal = deque((copy.deepcopy(e) for e in snapshot.journal), maxlen=JOURNAL_CAPACITY)
            self._idempotency = {self.idempotency_key(r.scope, r.key): copy.deepcopy(r) for r in snapshot.idempotency}
            self._evict_idempotency()
            self._seq = snapshot.seq

    def reset(self) -> None:
        """Empty the store. Listener registrations survive."""
        with self.lock:
            self._collections.clear()
            self._journal = deque(maxlen=JOURNAL_CAPACITY)
            self._idempotency.clear()
            self._seq = 0

    def entity_digest(self) -> str:
        """A hash of entity state only, and the determinism check's evidence.

        The journal, its timestamps and every volatile field's *value* are excluded, so two
        units seeded identically hash identically. A volatile field's *presence* is not: a
        stamp is often the only record of a state transition, so a set one hashes as
        :data:`VOLATILE_PRESENT` and one set to ``None`` as absent. Empty collections are
        skipped, because reading a collection materialises it."""
        with self.lock:
            collections: dict[str, Any] = {}
            for name in sorted(self._collections):
                entities = self._collections[name]
                if not entities:
                    continue
                collections[name] = {
                    entity_id: _scrub_volatile(entities[entity_id], self.volatile_fields, self.opaque_fields)
                    for entity_id in sorted(entities)
                }
            return sha256_hex(canonical_json(collections))

    def stats(self) -> dict[str, int]:
        """Entity counts per collection, keys sorted so key order does not depend on read order."""
        with self.lock:
            return {name: len(self._collections[name]) for name in sorted(self._collections)}
