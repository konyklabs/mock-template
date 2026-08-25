import { UnitError } from '../kernel/types.js';
import type { JournalEntry } from '../kernel/types.js';
import type { Clock } from '../time/clock.js';
import { canonicalJson, digestOf, sha256Hex } from '../util/json.js';

/**
 * The state engine.
 *
 * Everything a unit remembers lives here: entities in named collections, plus
 * an append-only journal of every committed mutation. The journal is not
 * decoration — it is the event source the webhook dispatcher subscribes to, so
 * a vendor surface never fires an event by hand and can never emit an event for
 * a mutation that did not commit.
 */

export interface Entity {
  id: string;
  version: number;
  createdAt: string;
  updatedAt: string;
  [key: string]: unknown;
}

export interface UpdateOptions {
  /** Optimistic concurrency: reject unless the stored version matches. */
  expectVersion?: number | null;
  meta?: Record<string, unknown>;
  /** Do not bump `version`/`updatedAt` (used for internal bookkeeping fields). */
  silent?: boolean;
}

export interface PageQuery {
  limit?: number;
  cursor?: string;
  /** Anything that must not change between pages; hashed into the cursor. */
  fingerprint?: unknown;
  maxLimit?: number;
  defaultLimit?: number;
}

export interface Page<T> {
  items: T[];
  cursor?: string;
}

interface CursorPayload {
  o: number;
  q: string;
  t: number;
}

/** Square documents a 5-minute cursor lifetime; the value is vendor-tunable. */
export const DEFAULT_CURSOR_TTL_MS = 5 * 60 * 1000;

export class Collection<T extends Entity = Entity> {
  constructor(
    readonly name: string,
    private readonly store: Store,
  ) {}

  private get map(): Map<string, T> {
    return this.store.raw(this.name) as Map<string, T>;
  }

  insert(entity: Omit<T, 'version' | 'createdAt' | 'updatedAt'> & Partial<Entity> & { id: string }, meta?: Record<string, unknown>): T {
    const now = this.store.clock.isoMs();
    if (this.map.has(entity.id)) {
      throw new UnitError('conflict', { detail: `${this.name} '${entity.id}' already exists`, info: { collection: this.name, id: entity.id } });
    }
    const created = {
      ...(entity as Record<string, unknown>),
      version: entity.version ?? 1,
      createdAt: entity.createdAt ?? now,
      updatedAt: entity.updatedAt ?? now,
    } as T;
    this.map.set(created.id, structuredClone(created));
    this.store.appendJournal({
      collection: this.name,
      id: created.id,
      op: 'insert',
      fromVersion: null,
      toVersion: created.version,
      changed: Object.keys(created),
      meta,
    });
    return structuredClone(created);
  }

  get(id: string): T | undefined {
    const found = this.map.get(id);
    return found ? structuredClone(found) : undefined;
  }

  require(id: string): T {
    const found = this.get(id);
    if (!found) {
      throw new UnitError('not_found', { detail: `${this.name} '${id}' not found`, info: { collection: this.name, id } });
    }
    return found;
  }

  has(id: string): boolean {
    return this.map.has(id);
  }

  /**
   * Read-modify-write under optimistic concurrency. The mutator sees a private
   * copy; nothing is committed and nothing is journalled if it throws.
   */
  update(id: string, opts: UpdateOptions, mutate: (draft: T) => void): T {
    const current = this.map.get(id);
    if (!current) {
      throw new UnitError('not_found', { detail: `${this.name} '${id}' not found`, info: { collection: this.name, id } });
    }
    if (opts.expectVersion !== undefined && opts.expectVersion !== null && opts.expectVersion !== current.version) {
      throw new UnitError('version_conflict', {
        detail: `Supplied version ${opts.expectVersion} does not match the current version ${current.version} of ${this.name} ${id}.`,
        info: { collection: this.name, id, supplied: opts.expectVersion, current: current.version },
      });
    }
    const draft = structuredClone(current) as T;
    mutate(draft);
    const changed = diffKeys(current, draft);
    if (!opts.silent) {
      draft.version = current.version + 1;
      draft.updatedAt = this.store.clock.isoMs();
    }
    draft.id = current.id;
    draft.createdAt = current.createdAt;
    this.map.set(id, structuredClone(draft));
    if (!opts.silent) {
      this.store.appendJournal({
        collection: this.name,
        id,
        op: 'update',
        fromVersion: current.version,
        toVersion: draft.version,
        changed,
        meta: opts.meta,
      });
    }
    return structuredClone(draft);
  }

  delete(id: string, meta?: Record<string, unknown>): boolean {
    const current = this.map.get(id);
    if (!current) return false;
    this.map.delete(id);
    this.store.appendJournal({
      collection: this.name,
      id,
      op: 'delete',
      fromVersion: current.version,
      toVersion: null,
      changed: [],
      meta,
    });
    return true;
  }

  all(): T[] {
    return [...this.map.values()].map((v) => structuredClone(v));
  }

  find(predicate: (e: T) => boolean): T | undefined {
    for (const e of this.map.values()) {
      if (predicate(e)) return structuredClone(e);
    }
    return undefined;
  }

  filter(predicate: (e: T) => boolean): T[] {
    return this.all().filter(predicate);
  }

  get size(): number {
    return this.map.size;
  }

  /**
   * Cursor pagination. The cursor is opaque, carries a fingerprint of the query
   * it was issued for, and expires — all three are real vendor behaviours that
   * consumers get wrong and that a mock therefore has to reproduce.
   */
  paginate(items: T[], q: PageQuery): Page<T> {
    const defaultLimit = q.defaultLimit ?? 100;
    const maxLimit = q.maxLimit ?? 1000;
    const limit = Math.min(Math.max(q.limit ?? defaultLimit, 1), maxLimit);
    const fp = digestOf(q.fingerprint ?? null).slice(0, 16);
    let offset = 0;
    if (q.cursor) {
      const decoded = decodeCursor(q.cursor);
      if (!decoded) throw new UnitError('invalid_cursor', { detail: 'The provided cursor could not be parsed.', field: 'cursor' });
      if (decoded.q !== fp) {
        throw new UnitError('invalid_cursor', {
          detail: 'The provided cursor was issued for a different query. Repeat the original query when paging.',
          field: 'cursor',
        });
      }
      if (this.store.clock.now() - decoded.t > DEFAULT_CURSOR_TTL_MS) {
        throw new UnitError('invalid_cursor', { detail: 'The provided cursor has expired.', field: 'cursor' });
      }
      offset = decoded.o;
    }
    const slice = items.slice(offset, offset + limit);
    const nextOffset = offset + slice.length;
    const cursor = nextOffset < items.length ? encodeCursor({ o: nextOffset, q: fp, t: this.store.clock.now() }) : undefined;
    return cursor ? { items: slice, cursor } : { items: slice };
  }
}

function encodeCursor(p: CursorPayload): string {
  return Buffer.from(JSON.stringify(p), 'utf8').toString('base64url');
}

function decodeCursor(c: string): CursorPayload | null {
  try {
    const parsed = JSON.parse(Buffer.from(c, 'base64url').toString('utf8')) as CursorPayload;
    if (typeof parsed.o !== 'number' || typeof parsed.q !== 'string' || typeof parsed.t !== 'number') return null;
    return parsed;
  } catch {
    return null;
  }
}

function diffKeys(a: Record<string, unknown>, b: Record<string, unknown>): string[] {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  const changed: string[] = [];
  for (const k of keys) {
    if (k === 'version' || k === 'updatedAt') continue;
    if (canonicalJson(a[k]) !== canonicalJson(b[k])) changed.push(k);
  }
  return changed.sort();
}

export interface IdempotencyRecord {
  scope: string;
  key: string;
  requestDigest: string;
  status: number;
  headers: Record<string, string>;
  bodyB64: string;
  storedAt: string;
}

export interface StoreSnapshot {
  collections: Record<string, Record<string, Entity>>;
  journal: JournalEntry[];
  idempotency: IdempotencyRecord[];
  seq: number;
}

export type JournalListener = (entry: JournalEntry) => void;

export class Store {
  private collections = new Map<string, Map<string, Entity>>();
  private wrappers = new Map<string, Collection<never>>();
  private journalEntries: JournalEntry[] = [];
  private idempotency = new Map<string, IdempotencyRecord>();
  private listeners: JournalListener[] = [];
  private seq = 0;
  /**
   * Fields excluded from `entityDigest`. Wall-clock stamps differ between two
   * runs of the same scenario without the state differing in any way that
   * matters, and the digest is the determinism check's evidence.
   */
  readonly volatileFields = new Set<string>(['createdAt', 'updatedAt']);

  constructor(readonly clock: Clock) {}

  markVolatile(...fields: string[]): void {
    for (const f of fields) this.volatileFields.add(f);
  }

  raw(name: string): Map<string, Entity> {
    let c = this.collections.get(name);
    if (!c) {
      c = new Map();
      this.collections.set(name, c);
    }
    return c;
  }

  collection<T extends Entity = Entity>(name: string): Collection<T> {
    let w = this.wrappers.get(name);
    if (!w) {
      w = new Collection<never>(name, this);
      this.wrappers.set(name, w);
    }
    return w as unknown as Collection<T>;
  }

  onJournal(fn: JournalListener): void {
    this.listeners.push(fn);
  }

  appendJournal(partial: Omit<JournalEntry, 'seq' | 'at'>): JournalEntry {
    const entry: JournalEntry = { seq: ++this.seq, at: this.clock.isoMs(), ...partial };
    this.journalEntries.push(entry);
    for (const l of this.listeners) l(entry);
    return entry;
  }

  journal(sinceSeq = 0): JournalEntry[] {
    return this.journalEntries.filter((e) => e.seq > sinceSeq).map((e) => structuredClone(e));
  }

  get journalSeq(): number {
    return this.seq;
  }

  // -- idempotency -----------------------------------------------------------

  idempotencyKey(scope: string, key: string): string {
    return `${scope} ${key}`;
  }

  getIdempotent(scope: string, key: string): IdempotencyRecord | undefined {
    return this.idempotency.get(this.idempotencyKey(scope, key));
  }

  putIdempotent(rec: IdempotencyRecord): void {
    this.idempotency.set(this.idempotencyKey(rec.scope, rec.key), rec);
  }

  // -- snapshot / restore ----------------------------------------------------

  snapshot(): StoreSnapshot {
    const collections: Record<string, Record<string, Entity>> = {};
    for (const [name, map] of this.collections) {
      collections[name] = Object.fromEntries([...map.entries()].map(([k, v]) => [k, structuredClone(v)]));
    }
    return {
      collections,
      journal: this.journalEntries.map((e) => structuredClone(e)),
      idempotency: [...this.idempotency.values()],
      seq: this.seq,
    };
  }

  restore(s: StoreSnapshot): void {
    this.collections.clear();
    for (const [name, entities] of Object.entries(s.collections)) {
      this.collections.set(name, new Map(Object.entries(entities).map(([k, v]) => [k, structuredClone(v)])));
    }
    this.journalEntries = s.journal.map((e) => structuredClone(e));
    this.idempotency = new Map(s.idempotency.map((r) => [this.idempotencyKey(r.scope, r.key), r]));
    this.seq = s.seq;
  }

  reset(): void {
    this.collections.clear();
    this.journalEntries = [];
    this.idempotency.clear();
    this.seq = 0;
  }

  /**
   * Digest of entity state only — the journal and its timestamps are excluded so
   * that two units seeded identically hash identically even though they were
   * started at different wall-clock instants.
   */
  entityDigest(): string {
    const collections: Record<string, unknown> = {};
    for (const [name, map] of [...this.collections].sort((a, b) => a[0].localeCompare(b[0]))) {
      // An empty collection is indistinguishable from an absent one: reading a
      // collection materializes it, and a read must not change the digest.
      if (map.size === 0) continue;
      collections[name] = Object.fromEntries(
        [...map.entries()]
          .sort((a, b) => a[0].localeCompare(b[0]))
          .map(([k, v]) => {
            const rest: Record<string, unknown> = {};
            for (const [field, value] of Object.entries(v)) {
              if (!this.volatileFields.has(field)) rest[field] = value;
            }
            return [k, rest];
          }),
      );
    }
    return sha256Hex(canonicalJson(collections));
  }

  stats(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [name, map] of this.collections) out[name] = map.size;
    return out;
  }
}
