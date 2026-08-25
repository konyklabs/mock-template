import type { JournalEntry, PreparedEvent, RetryPolicy, SubscriberConfig, UnitContext } from '../kernel/types.js';
import type { ChaosEngine } from '../chaos/engine.js';
import type { Clock } from '../time/clock.js';
import type { Store, Entity } from '../state/store.js';
import type { DeliverySink } from './sink.js';
import { sha256Hex } from '../util/json.js';

/**
 * Webhook dispatcher.
 *
 * Events are derived from the state journal, not fired by hand from route
 * handlers, so an event can only exist if the mutation behind it committed.
 * Delivery is at-least-once: an attempt is retried until a success status or
 * schedule exhaustion, and a delivery whose acknowledgement is lost is retried
 * and therefore duplicated. Every attempt carries the same `eventId`, which is
 * the consumer's dedup handle.
 */

export const SUBSCRIPTION_COLLECTION = 'subscriptions';

export interface SubscriptionEntity extends Entity {
  name?: string;
  notificationUrl: string;
  eventTypes: string[];
  signatureKey: string;
  enabled: boolean;
  apiVersion?: string;
}

export type DeliveryStatus = 'delivered' | 'failed' | 'exhausted' | 'skipped' | 'dropped';

export interface DeliveryRecord {
  id: string;
  eventId: string;
  eventType: string;
  entityId: string;
  subscriptionId: string;
  url: string;
  attempt: number;
  /** 1 for the first send; higher values are retries, matching `square-retry-number`. */
  retryNumber: number;
  at: string;
  status: DeliveryStatus;
  responseStatus: number;
  bodyHash: string;
  bodyPreview: string;
  /** The delivered payload, parsed. Present whenever the body was JSON. */
  body?: unknown;
  headers: Record<string, string>;
  chaos?: string[];
  error?: string;
  nextAttemptInMs?: number;
}

interface Queued {
  event: PreparedEvent;
  subscription: SubscriptionEntity;
  retryNumber: number;
  initialDeliveryAt: string;
  dropAck: boolean;
  /** Reason header the vendor sends on a retry, e.g. `http_error`. */
  retryReason?: string;
}

export interface DispatcherOptions {
  store: Store;
  clock: Clock;
  chaos: ChaosEngine;
  sink: DeliverySink;
  retry: RetryPolicy;
  /** Set by the kernel once the vendor definition is known. */
  getContext: () => UnitContext;
  disabled?: boolean;
  onLog?: (rec: DeliveryRecord) => void;
}

export class WebhookDispatcher {
  private readonly log: DeliveryRecord[] = [];
  private heldForReorder: Queued | null = null;
  private inFlight = new Set<Promise<void>>();
  private eventSeq = 0;
  private deliverySeq = 0;
  private enabledFlag = true;

  constructor(private readonly opts: DispatcherOptions) {}

  get retryPolicy(): RetryPolicy {
    return this.opts.retry;
  }

  setRetryPolicy(patch: Partial<RetryPolicy>): RetryPolicy {
    Object.assign(this.opts.retry, patch);
    return this.opts.retry;
  }

  setEnabled(on: boolean): void {
    this.enabledFlag = on;
  }

  get enabled(): boolean {
    return this.enabledFlag && !this.opts.disabled;
  }

  get sinkKind(): string {
    return this.opts.sink.kind;
  }

  deliveries(): DeliveryRecord[] {
    return this.log.map((d) => structuredClone(d));
  }

  clearLog(): void {
    this.log.length = 0;
  }

  /**
   * Settle every delivery: in-flight attempts AND retries still sitting on the
   * clock. Without the second half, a test asserting on the retry schedule would
   * have to sleep for a guessed duration and hope.
   */
  async drain(): Promise<void> {
    for (let i = 0; i < 500; i++) {
      if (this.inFlight.size > 0) {
        await Promise.all([...this.inFlight]);
        continue;
      }
      const pending = this.opts.clock.pending().filter((t) => t.label.startsWith('webhook'));
      if (pending.length === 0) return;
      const next = Math.max(0, Math.min(...pending.map((p) => p.dueInMs)));
      if (this.opts.clock.mode === 'virtual') {
        await this.opts.clock.advance(next);
      } else {
        await new Promise<void>((r) => setTimeout(r, Math.max(1, Math.min(next + 1, 250))));
      }
    }
  }

  subscriptions(): SubscriptionEntity[] {
    return this.opts.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION).all();
  }

  /** Seed config subscribers into the store; API-created ones join them later. */
  loadConfigSubscribers(subs: SubscriberConfig[]): void {
    const col = this.opts.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION);
    for (const [i, s] of subs.entries()) {
      const id = s.id ?? `wbhk_cfg_${String(i + 1).padStart(2, '0')}`;
      if (col.has(id)) continue;
      col.insert(
        {
          id,
          name: s.name ?? `config subscriber ${i + 1}`,
          notificationUrl: s.notificationUrl,
          eventTypes: s.eventTypes,
          signatureKey: s.signatureKey,
          enabled: s.enabled ?? true,
        } as SubscriptionEntity,
        { source: 'config' },
      );
    }
  }

  /** Wire the dispatcher to the store journal. Called once by the kernel. */
  attach(): void {
    this.opts.store.onJournal((entry) => {
      if (!this.enabled) return;
      const ctx = this.opts.getContext();
      if (!ctx.vendor.events || !ctx.vendor.signer) return;
      if (!ctx.capabilities.isEnabled('webhooks')) return;
      if (entry.collection === SUBSCRIPTION_COLLECTION) return;
      // Seeding is not a business event: loading a scenario that contains an
      // open order must not push an order.created to every subscriber.
      if (entry.meta?.seed === true) return;
      let prepared: PreparedEvent[];
      try {
        prepared = this.prepare(entry, ctx);
      } catch (err) {
        ctx.log.error('event mapping failed', { seq: entry.seq, error: String(err) });
        return;
      }
      for (const ev of prepared) this.enqueue(ev, ctx);
    });
  }

  private prepare(entry: JournalEntry, ctx: UnitContext): PreparedEvent[] {
    const mapped = ctx.vendor.events!.map(entry, ctx);
    return mapped.map((m) => {
      const eventId = m.eventId ?? this.mintEventId(entry, m.type);
      const createdAt = ctx.clock.isoMs();
      return { type: m.type, entityId: m.entityId, eventId, createdAt, body: m.build({ eventId, createdAt }) };
    });
  }

  /**
   * Deterministic event ids: two runs of the same scenario produce the same
   * ids, which makes a webhook transcript diffable evidence rather than noise.
   */
  private mintEventId(entry: JournalEntry, type: string): string {
    const seq = ++this.eventSeq;
    const h = sha256Hex(`${type}|${entry.collection}|${entry.id}|${entry.seq}|${seq}`);
    return [h.slice(0, 8), h.slice(8, 12), h.slice(12, 16), h.slice(16, 20), h.slice(20, 32)].join('-');
  }

  /** Enqueue a vendor event for every subscription that asked for its type. */
  enqueue(event: PreparedEvent, ctx: UnitContext): void {
    const matching = this.subscriptions().filter((s) => s.enabled && matchesEventType(s.eventTypes, event.type));
    for (const subscription of matching) {
      const queued: Queued = {
        event,
        subscription,
        retryNumber: 0,
        initialDeliveryAt: ctx.clock.isoMs(),
        dropAck: false,
      };
      this.applyChaosAndSchedule(queued, ctx);
    }
  }

  private applyChaosAndSchedule(q: Queued, ctx: UnitContext): void {
    const chaosApplied: string[] = [];
    let delayMs = 0;
    let copies = 1;

    if (ctx.capabilities.isEnabled('webhooks.chaos')) {
      const decision = ctx.chaos.evaluate({
        scope: 'webhook',
        eventType: q.event.type,
        path: q.subscription.notificationUrl,
      });
      if (decision) {
        chaosApplied.push(`${decision.ruleId}:${decision.fault}`);
        switch (decision.fault) {
          case 'webhook.duplicate':
            copies = 1 + Number(decision.params.copies ?? 1);
            break;
          case 'webhook.delay':
            delayMs = Number(decision.params.delayMs ?? 50);
            break;
          case 'webhook.drop_ack':
            q.dropAck = true;
            break;
          case 'webhook.out_of_order':
            // Hold this event back until the next one has gone out.
            this.heldForReorder = { ...q, dropAck: q.dropAck };
            this.record(q, 'skipped', 0, chaosApplied, 'held for out-of-order delivery');
            return;
          case 'webhook.drop':
            // Never touches the sink: recorded so a test can see it happened,
            // but the subscriber gets nothing and no retry is scheduled.
            this.record(q, 'dropped', 0, chaosApplied, 'dropped by chaos rule (webhook.drop)');
            return;
          default:
            break;
        }
      }
    }

    const release = this.heldForReorder;
    this.heldForReorder = null;

    for (let i = 0; i < copies; i++) {
      const copy: Queued = { ...q, dropAck: q.dropAck };
      const applied = i === 0 ? chaosApplied : [...chaosApplied, 'duplicate-copy'];
      if (delayMs > 0) {
        this.opts.clock.after(delayMs, `webhook:${q.event.eventId}`, () => this.track(this.attempt(copy, ctx, applied)));
      } else {
        this.track(this.attempt(copy, ctx, applied));
      }
    }

    if (release) {
      this.track(this.attempt(release, ctx, ['released-after-reorder']));
    }
  }

  private track(p: Promise<void>): void {
    const wrapped = p.finally(() => this.inFlight.delete(wrapped));
    this.inFlight.add(wrapped);
  }

  private async attempt(q: Queued, ctx: UnitContext, chaosApplied: string[]): Promise<void> {
    const signer = ctx.vendor.signer;
    if (!signer) return;
    const bodyText = JSON.stringify(q.event.body);
    const rawBody = new TextEncoder().encode(bodyText);
    const signatureHeaders = signer.sign({
      notificationUrl: q.subscription.notificationUrl,
      rawBody,
      secret: q.subscription.signatureKey,
      attempt: q.retryNumber + 1,
      event: q.event,
    });
    const headers: Record<string, string> = {
      'content-type': 'application/json',
      ...signatureHeaders,
    };
    if (q.retryNumber > 0) {
      headers['square-retry-number'] = String(q.retryNumber);
      if (q.retryReason) headers['square-retry-reason'] = q.retryReason;
    }
    headers['square-initial-delivery-timestamp'] = q.initialDeliveryAt;

    const result = await this.opts.sink.send({
      url: q.subscription.notificationUrl,
      headers,
      body: rawBody,
      timeoutMs: this.opts.retry.timeoutMs,
    });

    const ok = !q.dropAck && result.status >= 200 && result.status < 300;
    const retryReason = result.timedOut ? 'http_timeout' : result.status === 0 ? 'other_error' : 'http_error';

    if (ok) {
      this.record(q, 'delivered', result.status, chaosApplied, undefined, headers, bodyText);
      return;
    }

    const schedule = this.opts.retry.scheduleMs;
    if (q.retryNumber >= schedule.length) {
      this.record(q, 'exhausted', result.status, chaosApplied, result.error ?? 'retry schedule exhausted', headers, bodyText);
      return;
    }
    const delay = Math.round(schedule[q.retryNumber]! * this.opts.retry.timeScale);
    this.record(q, 'failed', result.status, chaosApplied, result.error ?? (q.dropAck ? 'acknowledgement dropped by chaos rule' : undefined), headers, bodyText, delay);
    const next: Queued = { ...q, retryNumber: q.retryNumber + 1, retryReason, dropAck: false };
    this.opts.clock.after(delay, `webhook-retry:${q.event.eventId}#${next.retryNumber}`, () => this.track(this.attempt(next, ctx, ['retry'])));
  }

  private record(
    q: Queued,
    status: DeliveryStatus,
    responseStatus: number,
    chaos: string[],
    error?: string,
    headers: Record<string, string> = {},
    bodyText = '',
    nextAttemptInMs?: number,
  ): void {
    const rec: DeliveryRecord = {
      id: `dlv_${String(++this.deliverySeq).padStart(5, '0')}`,
      eventId: q.event.eventId,
      eventType: q.event.type,
      entityId: q.event.entityId,
      subscriptionId: q.subscription.id,
      url: q.subscription.notificationUrl,
      attempt: q.retryNumber + 1,
      retryNumber: q.retryNumber,
      at: this.opts.clock.isoMs(),
      status,
      responseStatus,
      bodyHash: bodyText ? sha256Hex(bodyText) : '',
      bodyPreview: bodyText.slice(0, 400),
      headers,
    };
    if (bodyText) {
      try {
        rec.body = JSON.parse(bodyText);
      } catch {
        // A vendor whose payload is not JSON keeps bodyPreview only.
      }
    }
    if (chaos.length) rec.chaos = chaos;
    if (error) rec.error = error;
    if (nextAttemptInMs !== undefined) rec.nextAttemptInMs = nextAttemptInMs;
    this.log.push(rec);
    this.opts.onLog?.(rec);
  }
}

export function matchesEventType(patterns: string[], type: string): boolean {
  return patterns.some((p) => {
    if (p === type || p === '*') return true;
    if (!p.includes('*')) return false;
    const escaped = p.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
    return new RegExp(`^${escaped}$`).test(type);
  });
}

/**
 * Square's documented retry schedule: 11 retries over 24 hours at
 * 1, 2, 4, 8, 16, 32, 60 minutes then 2, 4, 8, 8 hours.
 * https://developer.squareup.com/docs/webhooks/overview
 */
export const SQUARE_RETRY_SCHEDULE_MS = [
  60_000, 120_000, 240_000, 480_000, 960_000, 1_920_000, 3_600_000, 7_200_000, 14_400_000, 28_800_000, 28_800_000,
];
