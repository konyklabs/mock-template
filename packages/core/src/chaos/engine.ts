import type { Rng } from '../rand/rng.js';

/**
 * Chaos engine.
 *
 * Design choice (justified in README): triggering is DETERMINISTIC by default.
 * A rule fires on a counter (`nth`, `every`, `times`, `after`), never on a coin
 * flip, so "the third create fails" is a fact rather than a flake. Two escape
 * hatches exist:
 *
 *   - `probability`, which does use the seeded RNG. The seed lives in the
 *     profile and is reported by /__unit/info, so the run is still replayable.
 *   - magic values in ordinary request fields (kernel/magic.ts), for consumers
 *     that drive the unit through a vendor SDK and cannot reach the control API.
 */

export type FaultName =
  // request-scope faults
  | 'rate_limit'
  | 'server_error'
  | 'unavailable'
  | 'timeout'
  | 'token_expiry'
  // webhook-scope faults
  | 'webhook.duplicate'
  | 'webhook.out_of_order'
  | 'webhook.drop_ack'
  | 'webhook.delay'
  | 'webhook.drop'
  | (string & {});

export type ChaosScope = 'request' | 'webhook';

export interface ChaosMatch {
  /** `POST /v2/orders`; `*` wildcards allowed, e.g. `POST /v2/orders*`. */
  route?: string;
  path?: string;
  method?: string;
  capability?: string;
  /** Webhook scope: match on the vendor event type, e.g. `order.*`. */
  eventType?: string;
  header?: Record<string, string>;
  bodyContains?: string;
}

export interface ChaosWhen {
  /** Fire on these 1-based occurrences of a match. */
  nth?: number[];
  /** Fire on every Nth match. */
  every?: number;
  /** Fire only after N matches have already passed cleanly. */
  after?: number;
  /** Stop firing after this many fires. */
  times?: number;
  always?: boolean;
  /** Seeded-random firing; see the note at the top of this file. */
  probability?: number;
}

export interface ChaosRule {
  id: string;
  scope: ChaosScope;
  fault: FaultName;
  match?: ChaosMatch;
  when?: ChaosWhen;
  params?: Record<string, unknown>;
  /** Free text shown in the control-plane listing. */
  note?: string;
}

export interface ChaosSubject {
  scope: ChaosScope;
  routeKey?: string;
  method?: string;
  path?: string;
  capability?: string;
  eventType?: string;
  headers?: Record<string, string>;
  bodyText?: string;
}

export interface ChaosDecision {
  ruleId: string;
  fault: FaultName;
  params: Record<string, unknown>;
  /** 1-based count of matches for this rule, including this one. */
  occurrence: number;
}

export interface ChaosEvent extends ChaosDecision {
  at: string;
  subject: string;
}

interface RuleState {
  matches: number;
  fires: number;
}

function globMatch(pattern: string, value: string): boolean {
  if (pattern === value) return true;
  if (!pattern.includes('*')) return false;
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${escaped}$`).test(value);
}

export class ChaosEngine {
  private rules: ChaosRule[] = [];
  private state = new Map<string, RuleState>();
  private history: ChaosEvent[] = [];
  private enabled = true;

  constructor(
    private readonly rng: Rng,
    private readonly nowIso: () => string,
    rules: ChaosRule[] = [],
  ) {
    this.replace(rules);
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
  }

  get isEnabled(): boolean {
    return this.enabled;
  }

  list(): ChaosRule[] {
    return this.rules.map((r) => structuredClone(r));
  }

  status(): Array<ChaosRule & { matches: number; fires: number }> {
    return this.rules.map((r) => ({ ...structuredClone(r), matches: this.state.get(r.id)?.matches ?? 0, fires: this.state.get(r.id)?.fires ?? 0 }));
  }

  replace(rules: ChaosRule[]): void {
    this.rules = rules.map((r) => structuredClone(r));
    this.state = new Map(this.rules.map((r) => [r.id, { matches: 0, fires: 0 }]));
  }

  add(rule: ChaosRule): ChaosRule {
    this.rules = this.rules.filter((r) => r.id !== rule.id);
    this.rules.push(structuredClone(rule));
    this.state.set(rule.id, { matches: 0, fires: 0 });
    return structuredClone(rule);
  }

  remove(id: string): boolean {
    const before = this.rules.length;
    this.rules = this.rules.filter((r) => r.id !== id);
    this.state.delete(id);
    return this.rules.length !== before;
  }

  /** Clear rules, counters and history. Restores a pristine unit. */
  reset(): void {
    this.rules = [];
    this.state.clear();
    this.history = [];
    this.rng.reset();
    this.enabled = true;
  }

  /** Reset only the counters, keeping the rules — for repeating a scenario. */
  resetCounters(): void {
    for (const k of this.state.keys()) this.state.set(k, { matches: 0, fires: 0 });
    this.history = [];
    this.rng.reset();
  }

  events(): ChaosEvent[] {
    return this.history.map((e) => structuredClone(e));
  }

  /**
   * At most one fault per subject: the first eligible rule in insertion order.
   *
   * Every matching rule's counter advances, whether or not it fires, so
   * `when.nth: [2]` means "the second request this rule matched" rather than
   * "the second request no earlier rule claimed". Without that, adding a rule
   * would silently re-number every rule below it.
   */
  evaluate(subject: ChaosSubject): ChaosDecision | null {
    if (!this.enabled) return null;
    let decision: ChaosDecision | null = null;
    for (const rule of this.rules) {
      if (rule.scope !== subject.scope) continue;
      if (!this.matches(rule, subject)) continue;
      const st = this.state.get(rule.id) ?? { matches: 0, fires: 0 };
      st.matches++;
      if (decision === null && this.shouldFire(rule, st)) {
        st.fires++;
        decision = { ruleId: rule.id, fault: rule.fault, params: rule.params ?? {}, occurrence: st.matches };
      }
      this.state.set(rule.id, st);
    }
    if (decision) {
      this.history.push({
        ...decision,
        at: this.nowIso(),
        subject: subject.routeKey ?? subject.eventType ?? subject.path ?? '(unknown)',
      });
    }
    return decision;
  }

  private matches(rule: ChaosRule, s: ChaosSubject): boolean {
    const m = rule.match;
    if (!m) return true;
    if (m.route && !(s.routeKey && globMatch(m.route, s.routeKey))) return false;
    if (m.path && !(s.path && globMatch(m.path, s.path))) return false;
    if (m.method && m.method.toUpperCase() !== (s.method ?? '').toUpperCase()) return false;
    if (m.capability && m.capability !== s.capability) return false;
    if (m.eventType && !(s.eventType && globMatch(m.eventType, s.eventType))) return false;
    if (m.bodyContains && !(s.bodyText ?? '').includes(m.bodyContains)) return false;
    if (m.header) {
      for (const [k, v] of Object.entries(m.header)) {
        if ((s.headers ?? {})[k.toLowerCase()] !== v) return false;
      }
    }
    return true;
  }

  /**
   * Conditions are ANDed, and an absent condition is not a veto: a rule with no
   * `when` fires on every match. `times` is checked first so an exhausted rule
   * costs nothing, and `probability` last so it draws from the RNG only for a
   * match that has already satisfied every deterministic condition — otherwise
   * the seeded stream would depend on traffic the rule was never going to fire on.
   */
  private shouldFire(rule: ChaosRule, st: RuleState): boolean {
    const w = rule.when ?? {};
    if (w.times !== undefined && st.fires >= w.times) return false;
    if (w.nth && !w.nth.includes(st.matches)) return false;
    if (w.after !== undefined && st.matches <= w.after) return false;
    if (w.every !== undefined && st.matches % w.every !== 0) return false;
    if (w.probability !== undefined && this.rng.next() >= w.probability) return false;
    return true;
  }
}

/** Faults a fork gets without writing any code. Documented by /__unit/info. */
export const BUILTIN_FAULTS: Array<{ name: FaultName; scope: ChaosScope; summary: string; params?: string }> = [
  { name: 'rate_limit', scope: 'request', summary: 'Reject the request as rate limited.', params: 'retryAfterSeconds?' },
  { name: 'server_error', scope: 'request', summary: 'Fail the request with a vendor-shaped 5xx.' },
  { name: 'unavailable', scope: 'request', summary: 'Fail the request as temporarily unavailable.' },
  { name: 'timeout', scope: 'request', summary: 'Stall the request, then fail it.', params: 'delayMs (default 100)' },
  { name: 'token_expiry', scope: 'request', summary: 'Treat the caller token as expired mid-flow, without touching stored state.' },
  { name: 'webhook.duplicate', scope: 'webhook', summary: 'Deliver the same event body more than once.', params: 'copies (default 1 extra)' },
  { name: 'webhook.out_of_order', scope: 'webhook', summary: 'Hold this event until the next one has been delivered.' },
  { name: 'webhook.drop_ack', scope: 'webhook', summary: 'Ignore a successful subscriber response so the retry schedule runs.' },
  { name: 'webhook.delay', scope: 'webhook', summary: 'Delay delivery.', params: 'delayMs' },
  { name: 'webhook.drop', scope: 'webhook', summary: 'Silently swallow the delivery: recorded as dropped, never sent to the subscriber. Filter with match.eventType.' },
];
