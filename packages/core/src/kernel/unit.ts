import { randomUUID } from 'node:crypto';
import {
  UnitError,
  type AuthResult,
  type HandlerArgs,
  type Logger,
  type MutableResponse,
  type RequestScope,
  type ResolvedConfig,
  type Route,
  type UnitContext,
  type UnitRequest,
  type UnitResponse,
  type VendorDefinition,
} from './types.js';
import { Router } from './router.js';
import { extractMagic } from './magic.js';
import { normalize } from './reply.js';
import { CapabilityRegistry } from '../capability/registry.js';
import { ChaosEngine, type ChaosDecision } from '../chaos/engine.js';
import { Clock, sleep } from '../time/clock.js';
import { Rng } from '../rand/rng.js';
import { Store } from '../state/store.js';
import { WebhookDispatcher } from '../webhooks/dispatcher.js';
import { HttpSink, type DeliverySink } from '../webhooks/sink.js';
import { canonicalJson, digestOf } from '../util/json.js';
import { controlRoutes } from '../control/plane.js';
import { controlBindings } from './bindings.js';

export interface UnitOptions {
  vendor: VendorDefinition;
  config: ResolvedConfig;
  /** Seed document, already read from disk (or supplied inline by a test). */
  seed?: unknown;
  sink?: DeliverySink;
  logger?: Logger;
}

export interface Unit {
  readonly name: string;
  readonly context: UnitContext;
  readonly routes: Route[];
  start(): Promise<void>;
  stop(): Promise<void>;
  handle(req: UnitRequest): Promise<UnitResponse>;
  /** Convenience for in-process callers and tests. */
  request(init: RequestInit): Promise<UnitResponse>;
}

export interface RequestInit {
  method: string;
  path: string;
  query?: Record<string, string>;
  headers?: Record<string, string>;
  body?: unknown;
  rawBody?: Uint8Array | string;
  transport?: string;
}

const decoder = new TextDecoder();

export function createLogger(level = process.env.UNIT_LOG_LEVEL ?? 'info'): Logger {
  const order = ['debug', 'info', 'warn', 'error'];
  const min = Math.max(0, order.indexOf(level));
  const emit = (lvl: string, msg: string, fields?: Record<string, unknown>) => {
    if (order.indexOf(lvl) < min) return;
    const line = { t: new Date().toISOString(), level: lvl, msg, ...(fields ?? {}) };
    process.stderr.write(`${JSON.stringify(line)}\n`);
  };
  return {
    debug: (m, f) => emit('debug', m, f),
    info: (m, f) => emit('info', m, f),
    warn: (m, f) => emit('warn', m, f),
    error: (m, f) => emit('error', m, f),
  };
}

/**
 * Build a unit from a vendor definition plus a resolved profile.
 *
 * Everything below this line is template core. A fork supplies the
 * VendorDefinition and nothing else; the pipeline, the capability gate, the
 * chaos hooks, idempotency, the control plane and the webhook wiring are shared.
 */
export function createUnit(opts: UnitOptions): Unit {
  const { vendor, config } = opts;
  const log = opts.logger ?? createLogger();
  const clock = new Clock(config.clock.mode, config.clock.start);
  const rng = new Rng(config.chaos.seed);
  const store = new Store(clock);
  const chaos = new ChaosEngine(rng, () => clock.isoMs(), config.chaos.rules);

  const allRoutes: Route[] = [...vendor.routes, ...controlRoutes()];
  const router = new Router(allRoutes);
  const capabilities = new CapabilityRegistry(vendor.capabilities, allRoutes, config.capabilities, config.profile);

  let ctx: UnitContext;
  const webhooks = new WebhookDispatcher({
    store,
    clock,
    chaos,
    sink: opts.sink ?? new HttpSink(),
    retry: config.webhooks.retry,
    getContext: () => ctx,
    disabled: config.webhooks.disableDelivery,
  });

  const scopes = new Map<string, RequestScope>();

  ctx = {
    vendor,
    config,
    store,
    capabilities,
    chaos,
    clock,
    rng,
    webhooks,
    log,
    requestScope(req: UnitRequest): RequestScope {
      let s = scopes.get(req.id);
      if (!s) {
        s = { magicFaults: [], magicParams: {}, forcedTokenExpiry: false };
        scopes.set(req.id, s);
      }
      return s;
    },
  };

  store.markVolatile(...(vendor.volatileFields ?? []));
  webhooks.attach();

  const seedDoc = opts.seed;

  function hydrate(): void {
    store.reset();
    if (vendor.hydrate) vendor.hydrate(ctx, seedDoc);
    webhooks.loadConfigSubscribers(config.webhooks.subscribers);
    webhooks.clearLog();
  }

  async function handle(req: UnitRequest): Promise<UnitResponse> {
    const started = Date.now();
    let route: Route | undefined;
    try {
      const outcome = router.match(req.method, req.path);
      if (outcome.kind === 'no-route') {
        return finish(req, applyShaped(vendor.errors.notFound(req, ctx), 'not_found'), route, started);
      }
      if (outcome.kind === 'method-not-allowed') {
        throw new UnitError('method_not_allowed', {
          detail: `${req.method} is not allowed on ${req.path}. Allowed: ${outcome.allowed.join(', ')}.`,
          info: { allowed: outcome.allowed },
        });
      }
      route = outcome.match.route;
      const args = buildArgs(req, outcome.match.params, route);
      const res = await runPipeline(req, route, args);
      return finish(req, res, route, started);
    } catch (err) {
      const unitErr = err instanceof UnitError ? err : new UnitError('internal', { detail: describeError(err) });
      if (unitErr.kind === 'internal' && !(err instanceof UnitError)) {
        log.error('unhandled error', { path: req.path, error: describeError(err) });
      }
      return finish(req, applyShaped(vendor.errors.shape(unitErr, ctx), unitErr.kind), route, started);
    } finally {
      scopes.delete(req.id);
    }
  }

  function applyShaped(shaped: { status: number; body: unknown; headers?: Record<string, string> }, kind: string): UnitResponse {
    const res = normalize({ status: shaped.status, json: shaped.body, headers: { ...(shaped.headers ?? {}), 'x-unit-error': kind } });
    return res;
  }

  function finish(req: UnitRequest, res: UnitResponse, route: Route | undefined, started: number): UnitResponse {
    const mutable: MutableResponse = { status: res.status, headers: { ...res.headers }, body: res.body };
    mutable.headers['x-unit-request-id'] = req.id;
    if (route && !route.internal) vendor.decorate?.(mutable, ctx, req);
    log.debug('request', {
      method: req.method,
      path: req.path,
      status: mutable.status,
      route: route ? `${route.method} ${route.path}` : undefined,
      ms: Date.now() - started,
    });
    return mutable;
  }

  function buildArgs(req: UnitRequest, params: Record<string, string>, route: Route): HandlerArgs {
    let cachedJson: unknown;
    let parsed = false;
    const bodyText = () => decoder.decode(req.rawBody);
    return {
      req,
      params,
      ctx,
      route,
      auth: null,
      bodyText,
      json<T = Record<string, unknown>>(): T {
        if (!parsed) {
          const t = bodyText();
          if (t.trim().length === 0) {
            cachedJson = {};
          } else {
            try {
              cachedJson = JSON.parse(t);
            } catch (e) {
              throw new UnitError('invalid_json', { detail: `Request body is not valid JSON: ${describeError(e)}` });
            }
          }
          parsed = true;
        }
        return cachedJson as T;
      },
      form(): Record<string, string> {
        return Object.fromEntries(new URLSearchParams(bodyText()));
      },
      query: (n) => req.query[n],
      header: (n) => req.headers[n.toLowerCase()],
    };
  }

  async function runPipeline(req: UnitRequest, route: Route, args: HandlerArgs): Promise<UnitResponse> {
    const routeKey = `${route.method} ${route.path}`;

    if (route.internal) {
      return normalize(await route.handler(args));
    }

    capabilities.assertEnabled(route.capability, routeKey);

    // -- fault selection: in-band magic values first, then control-plane rules --
    const scope = ctx.requestScope(req);
    const bodyForMagic = safeJson(args);
    const magic = extractMagic(vendor.magic, req, bodyForMagic);
    scope.magicFaults = magic.faults;
    scope.magicParams = magic.params;

    // A magic value is an explicit per-request instruction, so it wins over the
    // standing rules rather than competing with them.
    let decision: ChaosDecision | null = null;
    if (magic.faults.length > 0) {
      decision = { ruleId: 'magic', fault: magic.faults[0]!, params: magic.params, occurrence: 1 };
    } else {
      decision = chaos.evaluate({
        scope: 'request',
        routeKey,
        method: req.method,
        path: req.path,
        capability: route.capability,
        headers: req.headers,
        bodyText: args.bodyText(),
      });
    }

    if (decision) await applyRequestFault(decision, 'pre');

    // -- authentication + scopes -------------------------------------------
    let auth: AuthResult | null = null;
    if (route.auth) {
      auth = await vendor.auth.resolve(args, route.auth);
      if (route.scopes?.length) {
        const missing = route.scopes.filter((s) => !auth!.scopes.includes(s));
        if (missing.length > 0) {
          throw new UnitError('forbidden_scope', {
            detail: `The access token is missing the required permission(s): ${missing.join(', ')}.`,
            info: { missing, granted: auth.scopes },
          });
        }
      }
    }
    if (decision) await applyRequestFault(decision, 'post-auth');
    args.auth = auth;

    // -- idempotency --------------------------------------------------------
    const idem = route.idempotency;
    let idemKey: string | undefined;
    let requestDigest = '';
    if (idem) {
      const body = args.json<Record<string, unknown>>();
      const raw = idem.keyPath.split('.').reduce<unknown>((acc, k) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[k] : undefined), body);
      if (typeof raw === 'string' && raw.length > 0) {
        idemKey = raw;
        requestDigest = digestOf(body);
        const stored = store.getIdempotent(idem.scope, idemKey);
        if (stored) {
          const sameBody = stored.requestDigest === requestDigest;
          if (!sameBody && (idem.onMismatch ?? 'conflict') === 'conflict') {
            throw new UnitError('idempotency_conflict', {
              detail: 'The idempotency key can only be retried with the same request data.',
              field: idem.keyPath,
              info: { key: idemKey, scope: idem.scope },
            });
          }
          log.debug('idempotent replay', { scope: idem.scope, key: idemKey, sameBody });
          return {
            status: stored.status,
            headers: {
              ...stored.headers,
              'x-unit-idempotent-replay': 'true',
              ...(sameBody ? {} : { 'x-unit-idempotent-ignored-body': 'true' }),
            },
            body: Buffer.from(stored.bodyB64, 'base64'),
          };
        }
      } else if (idem.required) {
        throw new UnitError('missing_field', { detail: `${idem.keyPath} is required.`, field: idem.keyPath });
      }
    }

    const res = normalize(await route.handler(args));

    if (idem && idemKey && res.status >= 200 && res.status < 300) {
      store.putIdempotent({
        scope: idem.scope,
        key: idemKey,
        requestDigest,
        status: res.status,
        headers: { ...res.headers },
        bodyB64: Buffer.from(res.body).toString('base64'),
        storedAt: clock.isoMs(),
      });
    }
    return res;
  }

  async function applyRequestFault(d: ChaosDecision, phase: 'pre' | 'post-auth'): Promise<void> {
    const isAuthFault = d.fault === 'token_expiry';
    if (phase === 'pre' && isAuthFault) return;
    if (phase === 'post-auth' && !isAuthFault) return;
    switch (d.fault) {
      case 'rate_limit':
        throw new UnitError('rate_limited', {
          detail: 'Too many requests. Retry after a short delay.',
          info: { chaosRule: d.ruleId, retryAfterSeconds: Number(d.params.retryAfterSeconds ?? 1) },
        });
      case 'server_error':
        throw new UnitError('internal', { detail: 'Injected server error.', info: { chaosRule: d.ruleId } });
      case 'unavailable':
        throw new UnitError('unavailable', { detail: 'Injected service unavailability.', info: { chaosRule: d.ruleId } });
      case 'timeout': {
        const delayMs = Number(d.params.delayMs ?? 100);
        await sleep(delayMs);
        throw new UnitError('timeout', { detail: `Injected timeout after ${delayMs}ms.`, info: { chaosRule: d.ruleId, delayMs } });
      }
      case 'token_expiry':
        throw new UnitError('token_expired', {
          detail: 'The access token expired while the request was in flight.',
          info: { chaosRule: d.ruleId },
        });
      default:
        log.warn('unknown request-scope fault ignored', { fault: d.fault, rule: d.ruleId });
    }
  }

  function safeJson(args: HandlerArgs): unknown {
    try {
      return args.json();
    } catch {
      return undefined;
    }
  }

  const unit: Unit = {
    name: vendor.name,
    context: ctx,
    routes: allRoutes,
    async start() {
      hydrate();
      log.info('unit started', {
        vendor: vendor.name,
        profile: config.profile,
        capabilities: capabilities.enabledNames(),
        entities: store.stats(),
        stateDigest: store.entityDigest().slice(0, 16),
        chaosSeed: config.chaos.seed,
        clock: config.clock.mode,
      });
    },
    async stop() {
      await webhooks.drain();
      clock.clearAll();
    },
    handle,
    async request(init: RequestInit) {
      return handle(makeRequest(init));
    },
  };

  // The control plane needs internals that a route handler must not have.
  controlBindings.set(ctx, {
    hydrate,
    listRoutes: () =>
      router.routes().map((r) => ({
        method: r.method,
        path: r.path,
        capability: r.capability,
        auth: r.auth,
        operationId: r.operationId,
        summary: r.summary,
        internal: r.internal,
      })),
  });
  return unit;
}

const textEncoder = new TextEncoder();

export function makeRequest(init: RequestInit): UnitRequest {
  const headers: Record<string, string> = {};
  for (const [k, v] of Object.entries(init.headers ?? {})) headers[k.toLowerCase()] = v;
  let rawBody: Uint8Array;
  if (init.rawBody !== undefined) {
    rawBody = typeof init.rawBody === 'string' ? textEncoder.encode(init.rawBody) : init.rawBody;
  } else if (init.body !== undefined) {
    rawBody = textEncoder.encode(typeof init.body === 'string' ? init.body : JSON.stringify(init.body));
    headers['content-type'] = headers['content-type'] ?? 'application/json';
  } else {
    rawBody = new Uint8Array(0);
  }
  return {
    id: randomUUID(),
    method: init.method.toUpperCase(),
    path: init.path.startsWith('/') ? init.path : `/${init.path}`,
    query: init.query ?? {},
    headers,
    rawBody,
    transport: init.transport ?? 'inprocess',
    receivedAt: new Date().toISOString(),
  };
}

function describeError(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export { canonicalJson };
