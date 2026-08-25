import { UnitError, type UnitErrorKind } from '../kernel/types.js';
import type { Unit } from '../kernel/unit.js';
import { inProcess } from '../transport/inprocess.js';
import { serveHttp } from '../transport/http.js';
import { CONTROL_CAPABILITY } from '../capability/registry.js';

/**
 * Template conformance suite.
 *
 * This is the fork-update story made executable. A fork does not read the
 * template's changelog and hope; it runs this suite, which asserts the
 * contracts the core relies on the fork to honour (and vice versa). When the
 * core is upgraded, a green run here is the evidence that the fork still
 * composes with it, and a red check names the file to fix.
 *
 * Every check is vendor-agnostic — it discovers the surface through
 * `/__unit/routes` and `/__unit/info` rather than knowing any vendor's paths.
 */
export interface CheckResult {
  name: string;
  ok: boolean;
  detail: string;
  /** Set when a check could not run (missing optional feature), not a failure. */
  skipped?: boolean;
}

export interface ConformanceReport {
  passed: number;
  failed: number;
  skipped: number;
  ok: boolean;
  results: CheckResult[];
}

export interface ConformanceOptions {
  /** Must return a freshly started unit each call; determinism checks need two. */
  makeUnit: () => Promise<Unit>;
}

interface RouteInfo {
  method: string;
  path: string;
  capability: string;
  auth?: string;
  operationId?: string;
  internal?: boolean;
}

const ALL_ERROR_KINDS: UnitErrorKind[] = [
  'bad_request',
  'invalid_json',
  'missing_field',
  'invalid_value',
  'not_found',
  'method_not_allowed',
  'unauthorized',
  'token_expired',
  'token_revoked',
  'forbidden_scope',
  'capability_disabled',
  'version_conflict',
  'idempotency_conflict',
  'invalid_cursor',
  'invalid_transition',
  'conflict',
  'rate_limited',
  'timeout',
  'unavailable',
  'internal',
];

export async function runConformance(opts: ConformanceOptions): Promise<ConformanceReport> {
  const results: CheckResult[] = [];
  const record = async (name: string, fn: () => Promise<string | { detail: string; skipped: true }>): Promise<void> => {
    try {
      const out = await fn();
      if (typeof out === 'string') results.push({ name, ok: true, detail: out });
      else results.push({ name, ok: true, detail: out.detail, skipped: true });
    } catch (err) {
      results.push({ name, ok: false, detail: err instanceof Error ? err.message : String(err) });
    }
  };

  const unit = await opts.makeUnit();
  const api = inProcess(unit);

  await record('control-plane: health, info and routes respond', async () => {
    const health = await api.get<{ status: string }>('/__unit/health');
    assert(health.status === 200 && health.body.status === 'ok', `health returned ${health.status}`);
    const info = await api.get<Record<string, unknown>>('/__unit/info');
    assert(info.status === 200, `info returned ${info.status}`);
    for (const key of ['vendor', 'profile', 'capabilities', 'chaos', 'webhooks', 'clock', 'state']) {
      assert(key in info.body, `/__unit/info is missing '${key}'`);
    }
    const routes = await api.get<{ routes: RouteInfo[] }>('/__unit/routes');
    assert(routes.body.routes.length > 0, 'no routes registered');
    return `${routes.body.routes.filter((r) => !r.internal).length} vendor routes, ${routes.body.routes.filter((r) => r.internal).length} control routes`;
  });

  await record('capabilities: every route is owned, every capability is used', async () => {
    const { body: routeBody } = await api.get<{ routes: RouteInfo[] }>('/__unit/routes');
    const { body: capBody } = await api.get<{ capabilities: Array<{ name: string; routes: string[]; kind: string }> }>('/__unit/capabilities');
    const declared = new Set(capBody.capabilities.map((c) => c.name));
    const vendorRoutes = routeBody.routes.filter((r) => !r.internal);
    const orphans = vendorRoutes.filter((r) => r.capability !== CONTROL_CAPABILITY && !declared.has(r.capability));
    assert(orphans.length === 0, `routes with undeclared capabilities: ${orphans.map((r) => `${r.method} ${r.path}`).join(', ')}`);
    const unused = capBody.capabilities.filter((c) => c.kind !== 'behavior' && c.routes.length === 0);
    assert(
      unused.length === 0,
      `surface capabilities with no routes: ${unused.map((c) => c.name).join(', ')} (a surface capability a consumer cannot call is not observable)`,
    );
    const misdeclared = capBody.capabilities.filter((c) => c.kind === 'behavior' && c.routes.length > 0);
    assert(
      misdeclared.length === 0,
      `behavior capabilities that own routes: ${misdeclared.map((c) => c.name).join(', ')} (declare them as surface capabilities instead)`,
    );
    const behavior = capBody.capabilities.filter((c) => c.kind === 'behavior').map((c) => c.name);
    return `${vendorRoutes.length} routes across ${declared.size} capabilities${behavior.length ? ` (behavior-only: ${behavior.join(', ')})` : ''}`;
  });

  await record('capabilities: a disabled capability answers explicitly, never 404', async () => {
    const { body: routeBody } = await api.get<{ routes: RouteInfo[] }>('/__unit/routes');
    const { body: capBody } = await api.get<{ capabilities: Array<{ name: string; enabled: boolean; kind: string; requires: string[] }> }>(
      '/__unit/capabilities',
    );
    const originally = capBody.capabilities.filter((c) => c.enabled).map((c) => c.name);
    const checked: string[] = [];
    const behaviorOnly: string[] = [];

    for (const cap of capBody.capabilities) {
      const route = routeBody.routes.find((r) => !r.internal && r.capability === cap.name);
      if (!route) {
        if (cap.kind === 'behavior') behaviorOnly.push(cap.name);
        continue;
      }
      const path = concretePath(route.path);

      // Off: the capability and anything that depends on it are removed.
      await api.post('/__unit/capabilities', { set: originally.filter((n) => n !== cap.name && !n.startsWith(`${cap.name}.`)) });
      const off = await api.call({ method: route.method, path, body: {} });
      assert(off.status !== 404, `${cap.name}: disabled route returned 404, which a consumer cannot distinguish from "endpoint does not exist"`);
      assert(
        off.headers['x-unit-error'] === 'capability_disabled',
        `${cap.name}: expected x-unit-error=capability_disabled, got '${off.headers['x-unit-error'] ?? '(none)'}' with status ${off.status}`,
      );
      assert(off.text.includes(cap.name), `${cap.name}: the error body does not name the disabled capability`);

      // On: turn it on even if this profile ships it off, together with
      // whatever it needs, so the check works on a narrow profile too.
      const withCap = [...new Set([...originally, cap.name, ...cap.requires, ...ancestors(cap.name)])];
      await api.post('/__unit/capabilities', { set: withCap });
      const on = await api.call({ method: route.method, path, body: {} });
      assert(on.headers['x-unit-error'] !== 'capability_disabled', `${cap.name}: still reported disabled after enabling it`);
      checked.push(cap.name);
    }
    await api.post('/__unit/capabilities', { set: originally });
    assert(checked.length > 0, 'no capability had a route to probe');
    const suffix = behaviorOnly.length ? `; behavior-only (no surface to probe): ${behaviorOnly.join(', ')}` : '';
    return `probed ${checked.length} surface capabilities: ${checked.join(', ')}${suffix}`;
  });

  await record('errors: unknown path and wrong method are vendor-shaped', async () => {
    const missing = await api.get('/definitely/not/a/real/path');
    assert(missing.status === 404, `expected 404 for an unknown path, got ${missing.status}`);
    assert(missing.headers['x-unit-error'] === 'not_found', 'missing x-unit-error=not_found');
    assert(missing.text.trim().length > 0, 'the 404 body is empty; a consumer gets nothing to log');
    JSON.parse(missing.text);

    const { body: routeBody } = await api.get<{ routes: RouteInfo[] }>('/__unit/routes');
    const target = routeBody.routes.find((r) => !r.internal && r.method !== 'PATCH');
    assert(!!target, 'no vendor route to probe');
    const wrong = await api.call({ method: 'PATCH', path: concretePath(target!.path), body: {} });
    assert(wrong.headers['x-unit-error'] === 'method_not_allowed', `expected method_not_allowed, got '${wrong.headers['x-unit-error']}'`);
    return `404 -> ${missing.status}, wrong method -> ${wrong.status}`;
  });

  await record('errors: the vendor shaper covers every core error kind', async () => {
    const uncovered: string[] = [];
    for (const kind of ALL_ERROR_KINDS) {
      const shaped = unit.context.vendor.errors.shape(new UnitError(kind, { detail: `conformance probe for ${kind}` }), unit.context);
      if (!(shaped.status >= 400 && shaped.status <= 599)) uncovered.push(`${kind}: status ${shaped.status}`);
      const text = JSON.stringify(shaped.body);
      if (!text || text === '{}' || text === 'null') uncovered.push(`${kind}: empty body`);
    }
    assert(uncovered.length === 0, `error kinds not mapped by the vendor shaper: ${uncovered.join('; ')}`);
    return `${ALL_ERROR_KINDS.length} core error kinds all map to a 4xx/5xx vendor error`;
  });

  await record('state: the seed scenario is deterministic across units', async () => {
    // Two FRESH units: the unit driving the suite has already been probed, and
    // this check is about the seed, not about leaving state untouched.
    const first = await opts.makeUnit();
    const second = await opts.makeUnit();
    const a = first.context.store.entityDigest();
    const b = second.context.store.entityDigest();
    const stats = first.context.store.stats();
    await first.stop();
    await second.stop();
    assert(a === b, `two freshly seeded units disagree: ${a.slice(0, 16)} vs ${b.slice(0, 16)}`);
    assert(Object.values(stats).some((n) => n > 0), 'the seed scenario loaded no entities');
    return `digest ${a.slice(0, 16)}… over ${JSON.stringify(stats)}`;
  });

  await record('state: the journal is append-only and versions never go backwards', async () => {
    const { body } = await api.get<{ entries: Array<{ seq: number; collection: string; id: string; toVersion: number | null }> }>('/__unit/journal');
    let prev = 0;
    const lastVersion = new Map<string, number>();
    for (const e of body.entries) {
      assert(e.seq > prev, `journal sequence went ${prev} -> ${e.seq}`);
      prev = e.seq;
      if (e.toVersion !== null) {
        const key = `${e.collection}/${e.id}`;
        const before = lastVersion.get(key) ?? 0;
        assert(e.toVersion > before, `${key} version went ${before} -> ${e.toVersion}`);
        lastVersion.set(key, e.toVersion);
      }
    }
    return `${body.entries.length} journal entries, all monotonic`;
  });

  await record('chaos: identical rules and traffic produce identical outcomes', async () => {
    const { body: routeBody } = await api.get<{ routes: RouteInfo[] }>('/__unit/routes');
    const { body: capBody } = await api.get<{ capabilities: Array<{ name: string; enabled: boolean }> }>('/__unit/capabilities');
    const enabled = new Set(capBody.capabilities.filter((c) => c.enabled).map((c) => c.name));
    // The capability gate runs before chaos, so a probe route in a disabled
    // capability would never reach the rule.
    const target = routeBody.routes.find((r) => !r.internal && enabled.has(r.capability));
    assert(!!target, 'no enabled vendor route to probe');
    const routeKey = `${target!.method} ${target!.path}`;
    const rule = { id: 'conformance-determinism', scope: 'request', fault: 'rate_limit', match: { route: routeKey }, when: { nth: [2, 4] } };

    const sequence = async (u: Unit): Promise<string> => {
      const c = inProcess(u);
      await c.post('/__unit/chaos/rules', rule);
      const statuses: string[] = [];
      for (let i = 0; i < 5; i++) {
        const r = await c.call({ method: target!.method, path: concretePath(target!.path), body: {} });
        statuses.push(`${r.status}:${r.headers['x-unit-error'] ?? 'ok'}`);
      }
      return statuses.join(' ');
    };

    const first = await sequence(unit);
    const other = await opts.makeUnit();
    const second = await sequence(other);
    await other.stop();
    await api.post('/__unit/chaos/reset', {});
    assert(first === second, `two units diverged under the same rule:\n  A: ${first}\n  B: ${second}`);
    const fired = first.split(' ').filter((s) => s.includes('rate_limited')).length;
    assert(fired === 2, `rule specified 2 fires (nth 2 and 4) but ${fired} happened: ${first}`);
    return `both units: ${first}`;
  });

  await record('webhooks: signing is deterministic and matches the declared scheme', async () => {
    const signer = unit.context.vendor.signer;
    if (!signer) return { detail: 'vendor declares no signer', skipped: true as const };
    const props = signer.properties ?? {};
    const urlBound = props.urlBound ?? true;
    const bodyBound = props.bodyBound ?? true;
    const secretBound = props.secretBound ?? true;

    const event = { type: 'conformance.probe', eventId: 'evt_probe', entityId: 'ent_probe', createdAt: '2020-01-01T00:00:00.000Z', body: { probe: true } };
    const encode = (o: unknown) => new TextEncoder().encode(JSON.stringify(o));
    const input = { notificationUrl: 'https://example.test/hooks', rawBody: encode(event.body), secret: 'k1', attempt: 1, event };

    const a = signer.sign(input);
    const b = signer.sign(input);
    assert(Object.keys(a).length > 0, 'the signer produced no headers');
    assert(JSON.stringify(a) === JSON.stringify(b), 'signing the same input twice produced different headers');

    // Each dependency is asserted in the direction the vendor declared, so a
    // static-header scheme is conformant rather than merely tolerated.
    const differs = (patch: Record<string, unknown>) => JSON.stringify(signer.sign({ ...input, ...patch })) !== JSON.stringify(a);
    const checks: Array<[string, boolean, boolean]> = [
      ['secret', secretBound, differs({ secret: 'k2' })],
      ['notification URL', urlBound, differs({ notificationUrl: 'https://example.test/other' })],
      ['body', bodyBound, differs({ rawBody: encode({ probe: false }) })],
    ];
    for (const [name, declared, observed] of checks) {
      if (declared) assert(observed, `the signer declares it is bound to the ${name}, but changing it did not change the signature`);
      else assert(!observed, `the signer declares it is NOT bound to the ${name}, but changing it changed the signature`);
    }
    const bound = checks.filter(([, declared]) => declared).map(([name]) => name);
    return `headers: ${Object.keys(a).join(', ')}; bound to ${bound.length ? bound.join(', ') : 'nothing (static scheme)'}`;
  });

  await record('transport: HTTP and in-process bindings agree byte for byte', async () => {
    const server = await serveHttp(unit, { port: 0, host: '127.0.0.1' });
    try {
      const viaProcess = await api.get('/__unit/routes');
      const res = await fetch(`${server.url}/__unit/routes`);
      const viaHttp = await res.text();
      assert(res.status === viaProcess.status, `status differs: http ${res.status} vs in-process ${viaProcess.status}`);
      assert(viaHttp === viaProcess.text, 'response bodies differ between transports');
      return `identical ${viaHttp.length}-byte body over both bindings`;
    } finally {
      await server.close();
    }
  });

  await unit.stop();

  const passed = results.filter((r) => r.ok && !r.skipped).length;
  const skipped = results.filter((r) => r.skipped).length;
  const failed = results.filter((r) => !r.ok).length;
  return { passed, failed, skipped, ok: failed === 0, results };
}

export function formatReport(report: ConformanceReport): string {
  const lines = report.results.map((r) => {
    const mark = r.skipped ? 'SKIP' : r.ok ? 'PASS' : 'FAIL';
    return `  [${mark}] ${r.name}\n         ${r.detail}`;
  });
  lines.push('');
  lines.push(`  ${report.passed} passed, ${report.failed} failed, ${report.skipped} skipped`);
  return lines.join('\n');
}

function assert(cond: boolean, message: string): asserts cond {
  if (!cond) throw new Error(message);
}

/** `a.b.c` -> `['a', 'a.b']`, the capabilities a dotted name depends on. */
function ancestors(name: string): string[] {
  const parts = name.split('.');
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join('.'));
}

/** Turn `/v2/orders/:order_id` into a callable path for a probe request. */
function concretePath(template: string): string {
  return template
    .split('/')
    .map((s) => (s.startsWith(':') ? 'conformance-probe' : s))
    .join('/');
}
