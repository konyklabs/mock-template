import { UnitError, type Route } from '../kernel/types.js';
import { json } from '../kernel/reply.js';
import { controlBindings } from '../kernel/bindings.js';
import { applyCapabilityDelta } from '../capability/registry.js';
import { BUILTIN_FAULTS, type ChaosRule } from '../chaos/engine.js';
import { SUBSCRIPTION_COLLECTION, type SubscriptionEntity } from '../webhooks/dispatcher.js';

/**
 * Control plane — template core, identical in every fork.
 *
 * Namespaced under `/__unit/` so it cannot collide with a vendor surface: no
 * real vendor serves a path segment starting with a double underscore, and
 * keeping it inside the same unit (rather than on a second port) means a
 * consumer's existing base URL reaches it with no extra plumbing.
 *
 * Every route here is `internal: true`, which makes the kernel skip auth,
 * chaos and idempotency — a control call must never be the thing that trips
 * the fault it is trying to configure.
 */
export function controlRoutes(): Route[] {
  const c = (method: string, path: string, summary: string, handler: Route['handler']): Route => ({
    method,
    path,
    capability: '__control',
    internal: true,
    summary,
    handler,
  });

  return [
    c('GET', '/__unit/health', 'Liveness probe.', ({ ctx }) =>
      json({ status: 'ok', vendor: ctx.vendor.name, profile: ctx.config.profile, uptimeMs: Math.round(process.uptime() * 1000) }),
    ),

    c('GET', '/__unit/info', 'Everything needed to reproduce this unit run.', ({ ctx }) =>
      json({
        vendor: { name: ctx.vendor.name, displayName: ctx.vendor.displayName, apiVersion: ctx.vendor.apiVersion },
        profile: ctx.config.profile,
        capabilities: ctx.capabilities.view(),
        auth: ctx.vendor.auth.describe(),
        signer: ctx.vendor.signer?.describe() ?? null,
        magic: ctx.vendor.magic ?? null,
        chaos: { seed: ctx.config.chaos.seed, enabled: ctx.chaos.isEnabled, rules: ctx.chaos.status(), faults: BUILTIN_FAULTS },
        webhooks: {
          enabled: ctx.webhooks.enabled,
          sink: ctx.webhooks.sinkKind,
          retry: ctx.webhooks.retryPolicy,
          subscriptions: ctx.webhooks.subscriptions().length,
        },
        clock: { mode: ctx.clock.mode, now: ctx.clock.isoMs(), pendingTimers: ctx.clock.pending() },
        state: { entities: ctx.store.stats(), journalSeq: ctx.store.journalSeq, digest: ctx.store.entityDigest() },
      }),
    ),

    c('GET', '/__unit/routes', 'The unit surface, for docs and drift checks.', ({ ctx }) => {
      const binding = controlBindings.get(ctx);
      return json({ routes: binding?.listRoutes() ?? [] });
    }),

    c('GET', '/__unit/capabilities', 'Capability state.', ({ ctx }) =>
      json({ profile: ctx.config.profile, capabilities: ctx.capabilities.view() }),
    ),

    c('POST', '/__unit/capabilities', 'Toggle capabilities at runtime.', ({ ctx, json: readJson }) => {
      const body = readJson<{ set?: string[]; enable?: string[]; disable?: string[]; delta?: string }>();
      if (body.set) ctx.capabilities.setEnabled(body.set);
      if (body.delta) ctx.capabilities.setEnabled(applyCapabilityDelta(ctx.capabilities.enabledNames(), body.delta));
      for (const n of body.enable ?? []) ctx.capabilities.enable(n);
      for (const n of body.disable ?? []) ctx.capabilities.disable(n);
      return json({ capabilities: ctx.capabilities.view() });
    }),

    c('GET', '/__unit/chaos', 'Active chaos rules with their counters and fire history.', ({ ctx }) =>
      json({ enabled: ctx.chaos.isEnabled, seed: ctx.config.chaos.seed, rules: ctx.chaos.status(), events: ctx.chaos.events(), faults: BUILTIN_FAULTS }),
    ),

    c('POST', '/__unit/chaos/rules', 'Add one rule, or replace the whole set.', ({ ctx, json: readJson }) => {
      const body = readJson<{ rules?: ChaosRule[]; enabled?: boolean } & Partial<ChaosRule>>();
      if (typeof body.enabled === 'boolean') ctx.chaos.setEnabled(body.enabled);
      if (body.rules) {
        for (const r of body.rules) validateRule(r, ctx);
        ctx.chaos.replace(body.rules);
      } else if (body.id) {
        const rule = body as ChaosRule;
        validateRule(rule, ctx);
        ctx.chaos.add(rule);
      }
      return json({ rules: ctx.chaos.status() });
    }),

    c('DELETE', '/__unit/chaos/rules/:id', 'Remove one rule.', ({ ctx, params }) => {
      const removed = ctx.chaos.remove(params.id!);
      if (!removed) throw new UnitError('not_found', { detail: `chaos rule '${params.id}' not found` });
      return json({ rules: ctx.chaos.status() });
    }),

    c('POST', '/__unit/chaos/reset', 'Drop all rules and counters.', ({ ctx, json: readJson }) => {
      const body = readJson<{ keepRules?: boolean }>();
      if (body.keepRules) ctx.chaos.resetCounters();
      else ctx.chaos.reset();
      return json({ rules: ctx.chaos.status() });
    }),

    c('GET', '/__unit/journal', 'Append-only log of committed state mutations.', ({ ctx, query }) => {
      const since = Number(query('since') ?? 0);
      return json({ seq: ctx.store.journalSeq, entries: ctx.store.journal(Number.isFinite(since) ? since : 0) });
    }),

    c('GET', '/__unit/state', 'Entity counts and the state digest.', ({ ctx }) =>
      json({ entities: ctx.store.stats(), journalSeq: ctx.store.journalSeq, digest: ctx.store.entityDigest() }),
    ),

    c('GET', '/__unit/state/snapshot', 'Full state, restorable into another unit.', ({ ctx }) =>
      json({ digest: ctx.store.entityDigest(), snapshot: ctx.store.snapshot() }),
    ),

    c('POST', '/__unit/state/restore', 'Replace state with a previous snapshot.', ({ ctx, json: readJson }) => {
      const body = readJson<{ snapshot?: Parameters<typeof ctx.store.restore>[0] }>();
      if (!body.snapshot) throw new UnitError('missing_field', { detail: 'snapshot is required', field: 'snapshot' });
      ctx.store.restore(body.snapshot);
      return json({ entities: ctx.store.stats(), digest: ctx.store.entityDigest() });
    }),

    c('POST', '/__unit/state/reset', 'Wipe state and re-apply the seed scenario.', ({ ctx }) => {
      const binding = controlBindings.get(ctx);
      if (!binding) throw new UnitError('internal', { detail: 'control binding missing' });
      binding.hydrate();
      return json({ entities: ctx.store.stats(), digest: ctx.store.entityDigest(), journalSeq: ctx.store.journalSeq });
    }),

    c('GET', '/__unit/webhooks/subscriptions', 'Subscribers the dispatcher knows about.', ({ ctx }) =>
      json({ subscriptions: ctx.webhooks.subscriptions() }),
    ),

    c('POST', '/__unit/webhooks/subscriptions', 'Register a subscriber without using the vendor API.', ({ ctx, json: readJson }) => {
      const body = readJson<{ id?: string; name?: string; notificationUrl?: string; eventTypes?: string[]; signatureKey?: string; enabled?: boolean }>();
      if (!body.notificationUrl) throw new UnitError('missing_field', { detail: 'notificationUrl is required', field: 'notificationUrl' });
      const col = ctx.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION);
      const id = body.id ?? `wbhk_ctl_${(col.size + 1).toString().padStart(2, '0')}`;
      const entity = col.insert({
        id,
        name: body.name ?? 'control-plane subscriber',
        notificationUrl: body.notificationUrl,
        eventTypes: body.eventTypes ?? ['*'],
        signatureKey: body.signatureKey ?? 'unit-signature-key',
        enabled: body.enabled ?? true,
      } as SubscriptionEntity);
      return json({ subscription: entity }, 201);
    }),

    c('DELETE', '/__unit/webhooks/subscriptions/:id', 'Remove a subscriber.', ({ ctx, params }) => {
      const ok = ctx.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION).delete(params.id!);
      if (!ok) throw new UnitError('not_found', { detail: `subscription '${params.id}' not found` });
      return json({ deleted: params.id });
    }),

    c('GET', '/__unit/webhooks/deliveries', 'Every delivery attempt, with headers and signature.', ({ ctx, query }) => {
      const all = ctx.webhooks.deliveries();
      const type = query('eventType');
      const filtered = type ? all.filter((d) => d.eventType === type) : all;
      return json({ count: filtered.length, deliveries: filtered });
    }),

    c('POST', '/__unit/webhooks/drain', 'Wait for in-flight deliveries to settle.', async ({ ctx }) => {
      await ctx.webhooks.drain();
      return json({ deliveries: ctx.webhooks.deliveries().length });
    }),

    c('POST', '/__unit/webhooks/retry-policy', 'Adjust the retry schedule scaling at runtime.', ({ ctx, json: readJson }) => {
      const body = readJson<{ timeScale?: number; timeoutMs?: number; scheduleMs?: number[] }>();
      return json({ retry: ctx.webhooks.setRetryPolicy(body) });
    }),

    c('POST', '/__unit/clock/advance', 'Virtual clock only: jump forward and fire due timers.', async ({ ctx, json: readJson }) => {
      const body = readJson<{ ms?: number }>();
      const ms = Number(body.ms ?? 0);
      if (!Number.isFinite(ms) || ms < 0) throw new UnitError('invalid_value', { detail: 'ms must be a non-negative number', field: 'ms' });
      if (ctx.clock.mode !== 'virtual') {
        throw new UnitError('bad_request', {
          detail: 'The clock is in real mode. Start the unit with clock.mode="virtual" (UNIT_CLOCK=virtual) to control time.',
        });
      }
      const fired = await ctx.clock.advance(ms);
      await ctx.webhooks.drain();
      return json({ now: ctx.clock.isoMs(), firedTimers: fired, pending: ctx.clock.pending() });
    }),
  ];
}

function validateRule(r: ChaosRule, ctx: import('../kernel/types.js').UnitContext): void {
  if (!r.id) throw new UnitError('missing_field', { detail: 'chaos rule requires an id', field: 'id' });
  if (!r.fault) throw new UnitError('missing_field', { detail: 'chaos rule requires a fault', field: 'fault' });
  if (r.scope !== 'request' && r.scope !== 'webhook') {
    throw new UnitError('invalid_value', { detail: "chaos rule scope must be 'request' or 'webhook'", field: 'scope' });
  }
  // A behavior capability has no surface of its own, so this is where a
  // consumer meets its "disabled" answer.
  if (r.scope === 'webhook') ctx.capabilities.assertEnabled('webhooks.chaos', 'POST /__unit/chaos/rules');
}
