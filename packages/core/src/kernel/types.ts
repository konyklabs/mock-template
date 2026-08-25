/**
 * The unit contract.
 *
 * A "unit" simulates one third-party vendor. The contract below is deliberately
 * NOT an HTTP contract: a unit consumes a `UnitRequest` and produces a
 * `UnitResponse`, and `transport` names whichever binding produced it. HTTP is
 * one binding (transport/http.ts); in-process and file-drop bindings ship in the
 * same directory and are exercised by the conformance suite, which is how the
 * "core does not hard-assume HTTP" claim is kept honest rather than aspirational.
 */

export type TransportKind = 'http' | 'inprocess' | 'filedrop' | (string & {});

/** A request as the kernel sees it, whatever transport delivered it. */
export interface UnitRequest {
  /** Unique per received request; echoed on the response for correlation. */
  readonly id: string;
  /**
   * Verb. HTTP methods for the HTTP binding; other bindings supply their own
   * (the file-drop binding reads the verb out of the request document).
   */
  readonly method: string;
  /** Logical resource path, always starting with `/`. */
  readonly path: string;
  readonly query: Readonly<Record<string, string>>;
  /** Header names are lowercased by every binding before the kernel sees them. */
  readonly headers: Readonly<Record<string, string>>;
  /**
   * Exact received bytes. Kept as bytes (never a re-serialized object) because
   * webhook signature schemes sign the raw body, and a re-serialization would
   * silently change the bytes under test.
   */
  readonly rawBody: Uint8Array;
  readonly transport: TransportKind;
  readonly receivedAt: string;
}

export interface UnitResponse {
  readonly status: number;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: Uint8Array;
}

/** What a handler may return; the kernel normalizes it into a UnitResponse. */
export interface ReplyInit {
  status?: number;
  headers?: Record<string, string>;
  /** Serialized as JSON unless `raw`/`text` is used. */
  json?: unknown;
  text?: string;
  raw?: Uint8Array;
}

export interface CapabilityDecl {
  /**
   * Dotted name. A child capability (`webhooks.chaos`) is only usable when its
   * parent (`webhooks`) is enabled; the registry enforces that.
   */
  name: string;
  summary: string;
  /** Capabilities that must also be enabled for this one to function. */
  requires?: string[];
  /**
   * `surface` capabilities own routes and answer a disabled call explicitly.
   * `behavior` capabilities gate conduct with no surface of their own (chaos
   * injection, for instance); conformance checks them differently.
   */
  kind?: 'surface' | 'behavior';
}

export type AuthMode = string;

export interface AuthResult {
  /** Vendor-side identity the token resolves to (a merchant, a tenant, ...). */
  principalId: string;
  scopes: string[];
  tokenId?: string;
  meta?: Record<string, unknown>;
}

export interface IdempotencySpec {
  /** Dot path into the parsed JSON body, e.g. `idempotency_key`. */
  keyPath: string;
  /** Namespace so the same key on two operations does not collide. */
  scope: string;
  /** When true, a missing key is an error rather than "not idempotent". */
  required?: boolean;
  /**
   * What a reused key with a DIFFERENT body does. `conflict` is the usual REST
   * contract; `replay` returns the stored response and drops the new request on
   * the floor, which some vendors really do — Square's UpdateOrder is
   * documented that way ("you get a 200 response but the returned order doesn't
   * reflect any of your updates").
   */
  onMismatch?: 'conflict' | 'replay';
}

export interface Route {
  method: string;
  /** `/v2/orders/:order_id` — `:name` segments become `params`. */
  path: string;
  /** Every route belongs to exactly one capability. Enforced by conformance. */
  capability: string;
  /** Passed verbatim to the vendor auth adapter. Omit for unauthenticated routes. */
  auth?: AuthMode;
  /** Scopes the token must carry; checked by the kernel, not by the vendor. */
  scopes?: string[];
  idempotency?: IdempotencySpec;
  /** Stable identifier used by the spec-freshness inventory. */
  operationId?: string;
  summary?: string;
  /** Control-plane routes: no auth, no chaos, no idempotency, never in the vendor surface report. */
  internal?: boolean;
  handler: Handler;
}

export interface HandlerArgs {
  req: UnitRequest;
  params: Readonly<Record<string, string>>;
  ctx: UnitContext;
  route: Route;
  /** Resolved by the vendor auth adapter when `route.auth` is set. */
  auth: AuthResult | null;
  /** Parsed JSON body; throws a `bad_request` UnitError when unparseable. */
  json<T = Record<string, unknown>>(): T;
  /** Parsed body for `application/x-www-form-urlencoded`. */
  form(): Record<string, string>;
  bodyText(): string;
  query(name: string): string | undefined;
  header(name: string): string | undefined;
}

export type Handler = (args: HandlerArgs) => Promise<ReplyInit | UnitResponse> | ReplyInit | UnitResponse;

/**
 * Core-generic failure kinds. The kernel and every core subsystem only ever
 * raise these; the vendor's ErrorShaper turns them into that vendor's wire
 * format. This split is why a fork's entire error story is one lookup table.
 */
export type UnitErrorKind =
  | 'bad_request'
  | 'invalid_json'
  | 'missing_field'
  | 'invalid_value'
  | 'not_found'
  | 'method_not_allowed'
  | 'unauthorized'
  | 'token_expired'
  | 'token_revoked'
  | 'forbidden_scope'
  | 'capability_disabled'
  | 'version_conflict'
  | 'idempotency_conflict'
  | 'invalid_cursor'
  | 'invalid_transition'
  | 'conflict'
  | 'rate_limited'
  | 'timeout'
  | 'unavailable'
  | 'internal';

export interface UnitErrorDetail {
  detail?: string;
  /** Request field the error is about, in vendor dot notation. */
  field?: string;
  /** Extra machine-readable context surfaced under `x-unit-error` / the sidecar. */
  info?: Record<string, unknown>;
}

export class UnitError extends Error {
  readonly kind: UnitErrorKind;
  readonly detail?: string;
  readonly field?: string;
  readonly info?: Record<string, unknown>;

  constructor(kind: UnitErrorKind, d: UnitErrorDetail = {}) {
    super(d.detail ?? kind);
    this.name = 'UnitError';
    this.kind = kind;
    this.detail = d.detail;
    this.field = d.field;
    this.info = d.info;
  }
}

export interface ShapedError {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
}

export interface ErrorShaper {
  /** Turn a core error into the vendor's wire representation. */
  shape(err: UnitError, ctx: UnitContext): ShapedError;
  /** Body for a path that matched no route at all. */
  notFound(req: UnitRequest, ctx: UnitContext): ShapedError;
}

export interface AuthAdapter {
  /** Human description used by `/__unit/info`. */
  describe(): Record<string, string>;
  /** Resolve the principal, or throw a UnitError. `mode` is `route.auth`. */
  resolve(args: Omit<HandlerArgs, 'auth'>, mode: AuthMode): Promise<AuthResult> | AuthResult;
}

/**
 * What a signing scheme actually depends on.
 *
 * Square's HMAC covers the notification URL and the body; a vendor that sends a
 * static shared header (Clover's `X-Clover-Auth`) depends on neither. This is a
 * property of the scheme, not a law, so the signer declares it and conformance
 * checks what is true for that vendor instead of what was true for the first one.
 */
export interface SignerProperties {
  /** Signature changes when the subscriber's notification URL changes. */
  urlBound?: boolean;
  /** Signature changes when the body changes. */
  bodyBound?: boolean;
  /** Signature changes when the subscription's secret changes. */
  secretBound?: boolean;
}

export interface WebhookSigner {
  /** Declared dependencies of the scheme; all default to true. */
  properties?: SignerProperties;
  /**
   * Headers to attach to an outbound delivery. Receives the subscriber's
   * notification URL and the exact bytes about to be sent, because real vendors
   * sign one or both.
   */
  sign(input: {
    notificationUrl: string;
    rawBody: Uint8Array;
    secret: string;
    attempt: number;
    event: PreparedEvent;
  }): Record<string, string>;
  describe(): Record<string, string>;
}

/** A vendor event ready to serialize; `body` is the vendor's own envelope. */
export interface PreparedEvent {
  /** Vendor event type used for subscription matching, e.g. `order.created`. */
  type: string;
  eventId: string;
  entityId: string;
  createdAt: string;
  body: unknown;
}

export interface MappedEvent {
  /** Vendor event type used for subscription matching, e.g. `order.created`. */
  type: string;
  entityId: string;
  /**
   * Build the vendor's envelope once the dispatcher has assigned an id. Two
   * phases, because the id belongs to the dispatcher (it must be stable across
   * retries) while its position in the envelope belongs to the vendor.
   */
  build(meta: { eventId: string; createdAt: string }): unknown;
  /** Override the assigned id — used by test fixtures that pin one. */
  eventId?: string;
}

export interface EventMapper {
  /** Vendor events produced by one committed state mutation. */
  map(entry: JournalEntry, ctx: UnitContext): MappedEvent[];
}

export interface JournalEntry {
  seq: number;
  at: string;
  collection: string;
  id: string;
  op: 'insert' | 'update' | 'delete';
  fromVersion: number | null;
  toVersion: number | null;
  changed: string[];
  /** Free-form provenance, e.g. `{ operationId: 'CreateOrder' }`. */
  meta?: Record<string, unknown>;
}

export interface MagicTriggerSpec {
  /**
   * In-band fault triggering. Prior art: Square's sandbox uses magic values in
   * ordinary request fields (`cnon:card-nonce-declined`) rather than a control
   * channel, so a consumer's own client library can drive a fault.
   * https://developer.squareup.com/docs/devtools/sandbox/testing
   */
  prefix: string;
  /** Dot paths into the JSON body that are scanned for the prefix. */
  bodyPaths?: string[];
  queryParams?: string[];
  headers?: string[];
}

export interface VendorDefinition {
  /** Slug used in ids, config and reports. */
  name: string;
  displayName: string;
  /** Vendor's own API version string, surfaced by `/__unit/info`. */
  apiVersion?: string;
  capabilities: CapabilityDecl[];
  routes: Route[];
  errors: ErrorShaper;
  auth: AuthAdapter;
  signer?: WebhookSigner;
  events?: EventMapper;
  magic?: MagicTriggerSpec;
  /** Load a seed document into an empty store. */
  hydrate?(ctx: UnitContext, seed: unknown): void;
  /**
   * Entity fields excluded from the state digest because they carry wall-clock
   * time. `createdAt`/`updatedAt` are excluded already.
   */
  volatileFields?: string[];
  /** Last chance to add vendor-wide response headers (API version, request id). */
  decorate?(res: MutableResponse, ctx: UnitContext, req: UnitRequest): void;
}

export interface MutableResponse {
  status: number;
  headers: Record<string, string>;
  body: Uint8Array;
}

// ---------------------------------------------------------------------------
// Context: everything a handler is allowed to touch.
// ---------------------------------------------------------------------------

export interface UnitContext {
  readonly vendor: VendorDefinition;
  readonly config: ResolvedConfig;
  readonly store: import('../state/store.js').Store;
  readonly capabilities: import('../capability/registry.js').CapabilityRegistry;
  readonly chaos: import('../chaos/engine.js').ChaosEngine;
  readonly clock: import('../time/clock.js').Clock;
  readonly rng: import('../rand/rng.js').Rng;
  readonly webhooks: import('../webhooks/dispatcher.js').WebhookDispatcher;
  readonly log: Logger;
  /** Per-request scratch set by the kernel (magic triggers, forced faults). */
  requestScope(req: UnitRequest): RequestScope;
}

export interface RequestScope {
  magicFaults: string[];
  magicParams: Record<string, string>;
  forcedTokenExpiry: boolean;
}

export interface Logger {
  info(msg: string, fields?: Record<string, unknown>): void;
  warn(msg: string, fields?: Record<string, unknown>): void;
  error(msg: string, fields?: Record<string, unknown>): void;
  debug(msg: string, fields?: Record<string, unknown>): void;
}

export interface RetryPolicy {
  /** Delay before attempt N+1, in milliseconds, before scaling. */
  scheduleMs: number[];
  /**
   * Multiplier applied to every delay. Real vendors retry over hours; a test
   * suite cannot wait. Scaling keeps the SHAPE of the documented schedule while
   * making it observable in a test.
   */
  timeScale: number;
  /** Milliseconds to wait for a subscriber response before calling it a timeout. */
  timeoutMs: number;
}

export interface ResolvedConfig {
  profile: string;
  capabilities: string[];
  seedPath?: string;
  vendorConfig: Record<string, unknown>;
  webhooks: {
    retry: RetryPolicy;
    /** Subscribers declared in config, merged with any created through the API. */
    subscribers: SubscriberConfig[];
    /** Fail fast instead of retrying — used by conformance to stay quick. */
    disableDelivery?: boolean;
  };
  chaos: {
    seed: number;
    rules: import('../chaos/engine.js').ChaosRule[];
  };
  clock: { mode: 'real' | 'virtual'; start?: string };
  transport: { kind: TransportKind; port?: number; dir?: string };
}

export interface SubscriberConfig {
  id?: string;
  name?: string;
  notificationUrl: string;
  eventTypes: string[];
  signatureKey: string;
  enabled?: boolean;
}
