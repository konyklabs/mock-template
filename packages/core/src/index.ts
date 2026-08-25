/**
 * @vendor-unit/core — template core.
 *
 * Everything exported here is shared by every vendor fork. A fork imports this
 * package, exports one `VendorDefinition`, and inherits the pipeline, the state
 * engine, capabilities, chaos, webhooks, the control plane and the conformance
 * suite. See README.md ("Template boundary") for the file-by-file split.
 */
export * from './kernel/types.js';
export { createUnit, makeRequest, createLogger, type Unit, type UnitOptions, type RequestInit } from './kernel/unit.js';
export { controlBindings, type ControlBinding } from './kernel/bindings.js';
export { Router, type RouteMatch, type MatchOutcome } from './kernel/router.js';
export { json, text, redirect, noContent, normalize, decodeBody, parseBody } from './kernel/reply.js';
export { extractMagic, type MagicExtraction } from './kernel/magic.js';

export { Store, Collection, DEFAULT_CURSOR_TTL_MS, type Entity, type Page, type PageQuery, type StoreSnapshot, type UpdateOptions } from './state/store.js';
export { StateMachine, type MachineDef, type StateDef } from './state/machine.js';

export { CapabilityRegistry, CONTROL_CAPABILITY, applyCapabilityDelta, type CapabilityView } from './capability/registry.js';

export { ChaosEngine, BUILTIN_FAULTS, type ChaosRule, type ChaosDecision, type ChaosMatch, type ChaosScope, type ChaosSubject, type ChaosEvent, type FaultName } from './chaos/engine.js';

export {
  WebhookDispatcher,
  SUBSCRIPTION_COLLECTION,
  SQUARE_RETRY_SCHEDULE_MS,
  matchesEventType,
  type DeliveryRecord,
  type DeliveryStatus,
  type SubscriptionEntity,
} from './webhooks/dispatcher.js';
export { HttpSink, MemorySink, FileSink, type DeliverySink, type SinkRequest, type SinkResult } from './webhooks/sink.js';

export { runConformance, formatReport, type CheckResult, type ConformanceReport, type ConformanceOptions } from './conformance/index.js';
export { serveHttp, type HttpServerHandle } from './transport/http.js';
export { inProcess, type InProcessClient, type InProcessResponse } from './transport/inprocess.js';
export { serveFileDrop, type FileDropHandle, type FileDropDocument } from './transport/filedrop.js';

export { controlRoutes } from './control/plane.js';
export { loadProfile, DEFAULT_RETRY, type ProfileDocument, type LoadedProfile, type LoadProfileOptions } from './config/profile.js';

export { Clock, sleep, type ClockMode } from './time/clock.js';
export { Rng } from './rand/rng.js';
export { canonicalJson, digestOf, sha256Hex, dotGet, compact } from './util/json.js';
