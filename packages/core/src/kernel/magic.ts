import type { MagicTriggerSpec, UnitRequest } from './types.js';
import { dotGet } from '../util/json.js';

export interface MagicExtraction {
  faults: string[];
  params: Record<string, string>;
}

/**
 * In-band fault triggering.
 *
 * A vendor declares which ordinary request fields are scanned for a magic
 * prefix; a value of `chaos:rate_limit` or `chaos:timeout:delayMs=250` in one of
 * them arms that fault for this request only. This exists because a consumer
 * testing through a vendor SDK often cannot add a header or reach a control
 * API, but can always set a reference id. Prior art: Square's Sandbox drives
 * declines from magic values in ordinary payment fields
 * (https://developer.squareup.com/docs/devtools/sandbox/testing).
 */
export function extractMagic(spec: MagicTriggerSpec | undefined, req: UnitRequest, parsedBody: unknown): MagicExtraction {
  const out: MagicExtraction = { faults: [], params: {} };
  if (!spec) return out;

  const candidates: string[] = [];
  for (const p of spec.bodyPaths ?? []) {
    const v = dotGet(parsedBody, p);
    if (typeof v === 'string') candidates.push(v);
  }
  for (const q of spec.queryParams ?? []) {
    const v = req.query[q];
    if (typeof v === 'string') candidates.push(v);
  }
  for (const h of spec.headers ?? []) {
    const v = req.headers[h.toLowerCase()];
    if (typeof v === 'string') candidates.push(v);
  }

  for (const raw of candidates) {
    if (!raw.startsWith(spec.prefix)) continue;
    const [fault, ...rest] = raw.slice(spec.prefix.length).split(':');
    if (!fault) continue;
    out.faults.push(fault);
    for (const kv of rest) {
      const eq = kv.indexOf('=');
      if (eq > 0) out.params[kv.slice(0, eq)] = kv.slice(eq + 1);
    }
  }
  return out;
}
