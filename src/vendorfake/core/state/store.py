"""The state engine.

FOR: holding everything a unit remembers -- entities in named collections, an
append-only journal of every committed mutation, and the idempotency records
that make a retried request safe -- behind an interface no vendor and no
transport can get around.

INVARIANT: **the journal is the event source, not decoration.** An entry is
appended only after the map has been written, and every listener is invoked
synchronously and inline from inside ``append_journal``. Two consequences, and
both are the point: an event can never exist for a mutation that did not
commit, and a vendor handler can never forget to emit one, because it was
never the handler's job. A ``silent=True`` update writes the entity and
journals nothing, which is how internal bookkeeping avoids firing a webhook --
the one deliberate hole, and it has a name.

Copy discipline, enumerated
---------------------------
The reference calls ``structuredClone`` at **thirteen** sites in
``packages/core/src/state/store.ts`` -- lines 76, 86, 91, 121, 130, 142, 162,
167, 305, 331, 335, 344 and 346 -- and every one of them is load-bearing. They
map here as:

===========================================  =====================================
``store.ts``                                 this module
===========================================  =====================================
76   ``insert`` -- the stored copy           :meth:`Collection.insert`
86   ``insert`` -- the returned copy         :meth:`Collection.insert`
91   ``get``                                 :meth:`Collection.get`
121  ``update`` -- the mutator's draft       :meth:`Collection.update`
130  ``update`` -- the stored copy           :meth:`Collection.update`
142  ``update`` -- the returned copy         :meth:`Collection.update`
162  ``all``                                 :meth:`Collection.all`
167  ``find``                                :meth:`Collection.find`
305  ``journal``                             :meth:`Store.journal`
331  ``snapshot`` -- collections             :meth:`Store.snapshot`
335  ``snapshot`` -- journal                 :meth:`Store.snapshot`
344  ``restore`` -- collections              :meth:`Store.restore`
346  ``restore`` -- journal                  :meth:`Store.restore`
===========================================  =====================================

A count is not the gate; the aliasing test is. But the count is worth stating
because under-copying fails *silently* in exactly the way this project exists
to prevent: if :meth:`Collection.get` returned the live dict, a handler that
mutated the object it was handed would change committed state with no version
bump, no ``updated_at``, no journal entry -- and therefore **no webhook**.
Neither a journal-monotonicity check nor a two-fresh-units digest comparison
can see that, because both units would have the same bug.

Three sites are copied here that the reference does not copy, each because
Python's shape differs rather than because the reference is wrong:

* ``find`` and ``filter`` evaluate the caller's predicate against a copy. The
  reference hands ``find``'s predicate the live object (``filter`` already got
  a copy for free by going through ``all``); a predicate that wrote to it
  would corrupt the store through a hole nothing watches. In an in-memory fake
  the copy costs nothing worth measuring.
* ``snapshot`` and ``restore`` copy idempotency records. The reference spreads
  the values shallowly; a record's ``headers`` mapping is mutable here, so the
  shallow spread would alias.

Sort order
----------
Two orderings are observable from this module and both are code point
(Python's ``sorted``), never locale collation: the collection names and the
entity ids inside :meth:`Store.entity_digest`, plus :meth:`Store.stats`. The
reference sorts the first two with ``localeCompare`` while hashing object keys
by UTF-16 code unit -- two different orderings inside one hash, and
``localeCompare`` with no locale argument is environment-dependent, so the
reference is the non-deterministic side. ``stats()`` is sorted here and
unsorted there: the reference iterates collections in *materialisation* order,
so two units read in a different order publish a different key order at
``/__unit/info``, which is harmless for a digest that sorts and fatal for a
byte comparison.

Locking
-------
The store holds a re-entrant lock of its own, independent of the pipeline
lock, because the control plane can read it from a route that does not take
the pipeline lock. Journal listeners run **with that lock held**, which is
what makes listener dispatch order equal journal order under concurrency; the
matching obligation, stated here because it cannot be enforced from here, is
that **a journal listener must not block**. The webhook dispatcher's listener
mints, builds, signs and hands off to a queue, and returns; anything that
waits on another thread belongs behind that queue, not in the listener.
"""

from __future__ import annotations

import binascii
import copy
import json
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeAlias, TypeVar

from vendorfake.core.kernel.types import JournalEntry, UnitError, UnitErrorKind
from vendorfake.core.time.clock import Clock
from vendorfake.core.util.b64 import b64url_decode, b64url_encode
from vendorfake.core.util.json import MISSING, canonical_json, digest_of, dump_json, sha256_hex

__all__ = [
    "DEFAULT_CURSOR_TTL_MS",
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

#: An entity is a plain dict. Deliberately not a Pydantic model: entities are
#: produced internally and never parsed from an external document, they are
#: deep-copied on every read and write, and a vendor projects them through its
#: own models at the wire edge. ``tools/boundary.toml`` records the same rule.
Entity: TypeAlias = dict[str, Any]

_T = TypeVar("_T")

#: The reference vendor's documentation gives cursors a five-minute lifetime;
#: the value is vendor-tunable and is a real behaviour consumers get wrong.
DEFAULT_CURSOR_TTL_MS = 5 * 60 * 1000

#: Journal entry fields that :func:`diff_keys` never reports as changed. They
#: move on every non-silent update by construction, so reporting them would
#: make ``changed`` say nothing.
_DIFF_EXCLUDED = frozenset({"version", "updated_at"})


@dataclass(frozen=True, slots=True)
class Page(Generic[_T]):
    """One page of results, plus the cursor for the next one if there is one."""

    items: list[_T]
    #: ``None`` when this page is the last: the reference omits the key
    #: entirely rather than sending a null, and a vendor projection drops it
    #: through ``compact()``.
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """A response held so that a retried request replays instead of repeating."""

    #: Namespace, so the same key on two operations does not collide.
    scope: str
    key: str
    #: Canonical-JSON digest of the request body the key was first seen with.
    request_digest: str
    status: int
    headers: dict[str, str]
    #: The stored response body, base64url, because a journal or a snapshot
    #: must survive a round trip through JSON and a body is arbitrary bytes.
    body_b64: str
    stored_at: str


@dataclass(frozen=True, slots=True)
class StoreSnapshot:
    """Everything the store holds, detached from it."""

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
    """Field names whose value differs between two versions of an entity.

    Sorted by code point, and ``version``/``updated_at`` are excluded because
    a non-silent update moves both of them every time.

    Absence is compared through :data:`~vendorfake.core.util.json.MISSING`, not
    through ``dict.get``. The reference gets this free: ``a[k]`` for a missing
    key is ``undefined``, ``JSON.stringify(undefined)`` is itself ``undefined``
    and ``JSON.stringify(null)`` is the string ``"null"``, so a key that was
    removed and a key that was set to null are correctly *different*. A Python
    port written with ``a.get(k)`` collapses the two and publishes a journal
    entry at ``/__unit/journal`` that disagrees with the mutation it describes.
    """
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
    """Parse a cursor, or return ``None`` for anything that is not one.

    Every rejection funnels to one ``invalid_cursor`` at the call site, exactly
    as the reference's ``try { ... } catch { return null }`` does.

    One rejection is stricter than the reference: a negative offset. The
    reference type-checks ``typeof o === 'number'`` and then hands the value to
    ``Array.prototype.slice``, where a negative offset counts from the end and
    a forged cursor silently returns the tail of the collection. Python's
    slicing would do the same. A negative offset cannot be produced by
    :func:`_encode_cursor`, so refusing it costs nothing and closes the hole.
    """
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
    # ``isinstance(x, int)`` is true for ``True``; JSON booleans are not
    # numbers and ``typeof true === 'boolean'`` rejects them in the reference.
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return None
    if not isinstance(query, str):
        return None
    if isinstance(issued, bool) or not isinstance(issued, int | float):
        return None
    return _CursorPayload(o=offset, q=query, t=int(issued))


class Collection:
    """One named collection of versioned entities.

    Every read hands back a private copy and every write takes one, so a
    caller cannot reach committed state by holding on to an object it was
    given. That is not defensive style; it is what makes "a rejected update
    leaves no trace" true by construction rather than by care.
    """

    __slots__ = ("_store", "name")

    def __init__(self, name: str, store: Store) -> None:
        self.name = name
        self._store = store

    @property
    def _map(self) -> dict[str, Entity]:
        return self._store.raw(self.name)

    # -- writes -------------------------------------------------------------

    def insert(self, entity: Mapping[str, Any], meta: Mapping[str, Any] | None = None) -> Entity:
        """Add a new entity and journal it. Duplicate id raises ``conflict``.

        ``version`` defaults to 1 and ``created_at``/``updated_at`` to now, but
        all three may be supplied: seeding a scenario has to be able to state
        that an order was created last Tuesday at version 4.

        The journal entry's ``changed`` list is every key of the created
        entity, in insertion order -- not sorted. For an insert the whole
        entity is the change, and its own field order is more useful to a
        reader than an alphabetised one.
        """
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or entity_id == "":
            # A vendor handler that omits the id is a defect in the vendor, not
            # a bad request, so this raises the plain Python error the pipeline
            # turns into `internal` rather than a shaped 4xx that would invite
            # a consumer to think they caused it.
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
            self._map[entity_id] = copy.deepcopy(created)  # store.ts:76
            self._store.append_journal(
                collection=self.name,
                entity_id=entity_id,
                op="insert",
                from_version=None,
                to_version=created["version"],
                changed=list(created),
                meta=meta,
            )
            return copy.deepcopy(created)  # store.ts:86

    def update(
        self,
        entity_id: str,
        mutate: Callable[[Entity], None],
        *,
        expect_version: int | None = None,
        meta: Mapping[str, Any] | None = None,
        silent: bool = False,
    ) -> Entity:
        """Read-modify-write under optimistic concurrency.

        The mutator sees a private copy, so nothing is committed and nothing is
        journalled if it raises. ``changed`` is diffed **before** the version
        bump; ``id`` and ``created_at`` are restored from the stored record
        afterwards, so a mutator cannot rewrite either.

        ``expect_version`` is checked only when it is not ``None``, matching
        the reference's ``!== undefined && !== null`` -- "no opinion" and "must
        be version 0" are different requests and a falsy test would merge them.

        ``silent=True`` writes the entity, bumps nothing and journals nothing.
        It exists for internal bookkeeping -- marking a superseded token, say --
        and because the journal is the event source, a silent write is also the
        only way to change an entity without emitting a webhook.
        """
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
            draft: Entity = copy.deepcopy(current)  # store.ts:121
            mutate(draft)
            changed = diff_keys(current, draft)
            if not silent:
                draft["version"] = current["version"] + 1
                draft["updated_at"] = self._store.clock.iso_ms()
            draft["id"] = current.get("id", entity_id)
            if "created_at" in current:
                draft["created_at"] = current["created_at"]
            else:
                # Absent stays absent: the reference assigns `undefined`, which
                # vanishes from the object. Writing `None` here would put a
                # null into the digest and onto the wire.
                draft.pop("created_at", None)
            self._map[entity_id] = copy.deepcopy(draft)  # store.ts:130
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
            return copy.deepcopy(draft)  # store.ts:142

    def delete(self, entity_id: str, meta: Mapping[str, Any] | None = None) -> bool:
        """Remove an entity. Deleting what is not there returns ``False`` and
        journals nothing -- a delete that removed nothing is not a mutation."""
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

    # -- reads --------------------------------------------------------------

    def get(self, entity_id: str) -> Entity | None:
        """A private copy of the entity, or ``None``."""
        with self._store.lock:
            found = self._map.get(entity_id)
            return copy.deepcopy(found) if found is not None else None  # store.ts:91

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
        """Every entity, each a private copy, in insertion order."""
        with self._store.lock:
            return [copy.deepcopy(entity) for entity in self._map.values()]  # store.ts:162

    def find(self, predicate: Callable[[Entity], bool]) -> Entity | None:
        """The first entity satisfying ``predicate``, in insertion order.

        First, not "one of" -- ``find``'s insertion-order guarantee is load
        bearing wherever two records can share a lookup key.

        The predicate is given a copy, unlike the reference, which hands it the
        live object. See the module docstring.
        """
        with self._store.lock:
            for entity in self._map.values():
                candidate = copy.deepcopy(entity)  # store.ts:167
                if predicate(candidate):
                    return candidate
            return None

    def filter(self, predicate: Callable[[Entity], bool]) -> list[Entity]:
        """Every entity satisfying ``predicate``, each a private copy."""
        return [entity for entity in self.all() if predicate(entity)]

    @property
    def size(self) -> int:
        with self._store.lock:
            return len(self._map)

    # -- pagination ---------------------------------------------------------

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
        """Cursor pagination, reproducing three real vendor behaviours.

        The cursor is **opaque** (base64url of ``{"o","q","t"}``), it carries a
        **fingerprint** of the query it was issued for so that paging with a
        changed filter is refused rather than silently wrong, and it
        **expires**. Consumers get all three wrong against real vendors, which
        is exactly why a fake that omitted them would let a broken integration
        pass.

        ``limit`` is clamped to ``[1, max_limit]``. A next cursor is emitted
        only when there is genuinely a next page, so a consumer looping until
        the cursor is absent terminates.

        ``t`` is floored to whole milliseconds. The clock returns a float here
        where ``Date.now()`` returns an integer, and ``dump_json`` would write
        ``1.5`` into a token that must be byte-comparable with the oracle's.
        """
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
"""What a set volatile field hashes as in :meth:`Store.entity_digest`. The
value is arbitrary -- every set volatile field becomes it, so it cannot be
confused with a real value -- and is a string so the canonical JSON stays
plain."""


def _scrub_volatile(value: Any, volatile: set[str], opaque: set[str]) -> Any:
    """``value`` with every volatile field, at any depth, reduced to whether it
    is set. Dicts and lists are walked; everything else is returned as is.

    Two refinements keep "at any depth" honest, both because volatile names
    are the unit's vocabulary and an entity can hold documents that are not:

    * A subtree under an **opaque** name (:meth:`Store.mark_opaque`) is
      digested verbatim, never descended into. Those are caller free-form
      documents -- Square's ``metadata`` allows any ``[a-zA-Z0-9_-]`` key, so
      ``created_at`` inside it is a caller's key that happens to share a
      volatile name, and blanking it would hide caller state from the digest.
      Opaque wins over volatile, whatever the value's shape.
    * Only a **scalar** under a volatile name becomes the marker. A dict or
      list under a volatile name is recursed into like any other container --
      "volatile" describes a wall-clock value, and a subtree that merely
      shares the name is not one.
    """
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
    """Named collections, the journal, and the idempotency table."""

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
        #: Re-entrant, because a journal listener runs under it and may read
        #: the store back -- the webhook dispatcher looks up subscriptions from
        #: inside the listener it registered.
        self.lock = threading.RLock()
        self._collections: dict[str, dict[str, Entity]] = {}
        self._wrappers: dict[str, Collection] = {}
        self._journal: list[JournalEntry] = []
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._listeners: list[JournalListener] = []
        self._seq = 0
        #: Field names whose *values* :meth:`entity_digest` ignores, at any
        #: depth. Wall-clock stamps differ between two runs of the same
        #: scenario without the state differing in any way that matters, and
        #: the digest is the determinism evidence.
        self.volatile_fields: set[str] = {"created_at", "updated_at"}
        #: Names of caller free-form subtrees the digest takes verbatim; see
        #: :meth:`mark_opaque`. Empty in the core: free-form documents are a
        #: vendor's vocabulary.
        self.opaque_fields: set[str] = set()

    def mark_volatile(self, *fields: str) -> None:
        """Ignore the values of further fields in the digest. A vendor declares
        its own; the name matches at any depth, so ``created_at`` on a nested
        tender is covered by the same declaration as ``created_at`` on the
        order -- except inside an opaque subtree (:meth:`mark_opaque`)."""
        with self.lock:
            self.volatile_fields.update(fields)

    def mark_opaque(self, *fields: str) -> None:
        """Declare caller free-form subtrees the digest takes verbatim.

        A name marked opaque -- matched at any depth, like a volatile one --
        stops the scrub at that key: the subtree beneath it is digested
        exactly as stored, and volatile names inside it are a caller's own
        keys, not the unit's stamps. Opaque wins over volatile. A vendor
        declares its own through ``VendorDefinition.opaque_fields``.
        """
        with self.lock:
            self.opaque_fields.update(fields)

    # -- collections --------------------------------------------------------

    def raw(self, name: str) -> dict[str, Entity]:
        """The live map behind a collection, materialising it on first read.

        The one place a live map is exposed, and only :class:`Collection` and
        the digest use it. Note that reading a collection *creates* it -- which
        is precisely why :meth:`entity_digest` skips empty collections.
        """
        with self.lock:
            existing = self._collections.get(name)
            if existing is None:
                existing = {}
                self._collections[name] = existing
            return existing

    def collection(self, name: str) -> Collection:
        """The wrapper for a named collection, created once and cached."""
        with self.lock:
            wrapper = self._wrappers.get(name)
            if wrapper is None:
                wrapper = Collection(name, self)
                self._wrappers[name] = wrapper
            return wrapper

    # -- journal ------------------------------------------------------------

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
        """Append one committed mutation and dispatch it to every listener.

        Called only from :class:`Collection`, and only after the map has been
        written, so a listener always observes committed state. ``seq`` starts
        at 1 and is strictly increasing for the lifetime of the store; a
        :meth:`reset` puts it back to 0, which is what makes a fresh unit and a
        reset unit produce comparable journals.
        """
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
        """Every entry after ``since_seq``, each a private copy."""
        with self.lock:
            return [copy.deepcopy(e) for e in self._journal if e.seq > since_seq]  # store.ts:305

    @property
    def journal_seq(self) -> int:
        with self.lock:
            return self._seq

    # -- idempotency --------------------------------------------------------

    @staticmethod
    def idempotency_key(scope: str, key: str) -> str:
        """``"scope key"`` -- one space, so the same key on two operations does
        not collide."""
        return f"{scope} {key}"

    def get_idempotent(self, scope: str, key: str) -> IdempotencyRecord | None:
        with self.lock:
            return self._idempotency.get(self.idempotency_key(scope, key))

    def put_idempotent(self, record: IdempotencyRecord) -> None:
        with self.lock:
            self._idempotency[self.idempotency_key(record.scope, record.key)] = copy.deepcopy(record)

    # -- snapshot / restore -------------------------------------------------

    def snapshot(self) -> StoreSnapshot:
        """Detach everything the store holds, copied all the way down."""
        with self.lock:
            return StoreSnapshot(
                collections={
                    name: {k: copy.deepcopy(v) for k, v in entities.items()}  # store.ts:331
                    for name, entities in self._collections.items()
                },
                journal=[copy.deepcopy(e) for e in self._journal],  # store.ts:335
                idempotency=[copy.deepcopy(r) for r in self._idempotency.values()],
                seq=self._seq,
            )

    def restore(self, snapshot: StoreSnapshot) -> None:
        """Replace everything the store holds. Listeners are **not** notified:
        a restore is not a mutation of the modelled world, and replaying a
        restored journal to the webhook dispatcher would deliver every event in
        it a second time."""
        with self.lock:
            self._collections = {
                name: {k: copy.deepcopy(v) for k, v in entities.items()}  # store.ts:344
                for name, entities in snapshot.collections.items()
            }
            self._journal = [copy.deepcopy(e) for e in snapshot.journal]  # store.ts:346
            self._idempotency = {self.idempotency_key(r.scope, r.key): copy.deepcopy(r) for r in snapshot.idempotency}
            self._seq = snapshot.seq

    def reset(self) -> None:
        """Empty the store. Listener registrations survive: a listener belongs
        to the unit, not to the data."""
        with self.lock:
            self._collections.clear()
            self._journal = []
            self._idempotency.clear()
            self._seq = 0

    # -- evidence -----------------------------------------------------------

    def entity_digest(self) -> str:
        """A hash of entity state only, and the determinism check's evidence.

        The journal and its timestamps are excluded so that two units seeded
        identically hash identically even though they were started at different
        wall-clock instants. A volatile field's *value* is excluded for the
        same reason -- but its *presence* is not. A wall-clock stamp is often
        the only record of a state transition: an authorization code is
        "spent" exactly when ``used_at`` is set, a refresh token "rotated" when
        ``refresh_used_at`` is. Dropping the key outright, as an earlier
        version did, made "spent" and "fresh" the same absent key to the
        digest, so a mutant that stopped marking the transition would not
        have moved it. Here a set volatile field hashes as
        :data:`VOLATILE_PRESENT`, and one set to ``None`` hashes as absent,
        because ``None`` is how a model spells "not yet" before its projection
        compacts the key away.

        The name matches **at any depth** -- a dict inside a list inside the
        entity is scrubbed the same way -- because a tender's ``created_at``
        or a fulfillment's ``placed_at`` is the same kind of stamp as the
        order's own, and a top-level-only rule left every nested one in the
        digest. Depth-matching stops at an **opaque** subtree
        (:meth:`mark_opaque`): a caller free-form document is digested
        verbatim, because a volatile name inside it is the caller's key, not
        the unit's stamp. See :func:`_scrub_volatile` for both rules.

        **Empty collections are skipped entirely.** Reading a collection
        materialises it, and a read must never change the digest -- without
        this rule, a unit that had been asked "how many orders?" would hash
        differently from one that had not.
        """
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
        """Entity counts per collection, keys sorted.

        Sorted where the reference is not: it iterates in materialisation
        order, so two units read in a different order publish a different key
        order at ``/__unit/info`` and a byte comparison between them fails for
        no reason anybody can see.
        """
        with self.lock:
            return {name: len(self._collections[name]) for name in sorted(self._collections)}
