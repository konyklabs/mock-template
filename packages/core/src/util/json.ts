import { createHash } from 'node:crypto';

/**
 * Canonical JSON: keys sorted at every depth. Used ONLY for hashing (snapshot
 * digests, idempotency request fingerprints, cursor query fingerprints) — never
 * for responses, which must keep the vendor's own field order.
 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(canonicalize);
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(value as Record<string, unknown>).sort()) {
    out[key] = canonicalize((value as Record<string, unknown>)[key]);
  }
  return out;
}

export function sha256Hex(input: string | Uint8Array): string {
  return createHash('sha256').update(input).digest('hex');
}

export function digestOf(value: unknown): string {
  return sha256Hex(canonicalJson(value));
}

/** Read `a.b.c` / `a.b[0]` out of a parsed body. Returns undefined on any miss. */
export function dotGet(obj: unknown, path: string): unknown {
  if (obj === null || obj === undefined) return undefined;
  let cur: unknown = obj;
  for (const seg of path.split('.')) {
    const m = /^([^[\]]+)((\[[^\]]+\])*)$/.exec(seg);
    if (!m) return undefined;
    if (cur === null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[m[1]!];
    const brackets = m[2] ?? '';
    for (const b of brackets.matchAll(/\[([^\]]+)\]/g)) {
      if (cur === null || typeof cur !== 'object') return undefined;
      const key = b[1]!;
      cur = Array.isArray(cur) ? cur[Number(key)] : (cur as Record<string, unknown>)[key];
    }
  }
  return cur;
}

/** Remove undefined values so response bodies match vendor examples exactly. */
export function compact<T extends Record<string, unknown>>(obj: T): T {
  for (const k of Object.keys(obj)) {
    if (obj[k] === undefined) delete obj[k];
  }
  return obj;
}
