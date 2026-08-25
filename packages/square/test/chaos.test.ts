import { describe, expect, it } from 'vitest';
import { SEED_OPEN_ORDER, harness, orderBody, subscribe, type Harness } from './helpers.js';

/**
 * Slice element 4: chaos, toggleable at runtime and reproducible.
 *
 * Two triggering mechanisms, both deterministic:
 *   - control-plane rules, counter-based (`nth`, `every`, `after`, `times`);
 *   - magic values in ordinary request fields, for a consumer driving the unit
 *     through an SDK that cannot reach the control plane.
 * Neither consults a random number generator. See README "Chaos".
 */
async function addRule(h: Harness, rule: Record<string, unknown>): Promise<void> {
  const res = await h.api.post('/__unit/chaos/rules', rule);
  expect(res.status).toBe(200);
}

async function deliveries(h: Harness): Promise<Array<Record<string, any>>> {
  await h.api.post('/__unit/webhooks/drain', {});
  const res = await h.api.get<{ deliveries: Array<Record<string, any>> }>('/__unit/webhooks/deliveries');
  return res.body.deliveries;
}

describe('chaos', () => {
  it('rate limits exactly the requests the rule names', async () => {
    const h = await harness();
    await addRule(h, {
      id: 'rl',
      scope: 'request',
      fault: 'rate_limit',
      match: { route: 'POST /v2/orders' },
      when: { nth: [2, 4] },
      params: { retryAfterSeconds: 3 },
    });

    const statuses: number[] = [];
    for (let i = 0; i < 5; i++) {
      const res = await h.api.post<{ errors?: Array<{ code: string; category: string }> }>('/v2/orders', orderBody(), { headers: h.auth });
      statuses.push(res.status);
      if (res.status === 429) {
        expect(res.body.errors![0]!.code).toBe('RATE_LIMITED');
        expect(res.body.errors![0]!.category).toBe('RATE_LIMIT_ERROR');
        expect(res.headers['retry-after']).toBe('3');
      }
    }
    expect(statuses).toEqual([200, 429, 200, 429, 200]);

    // Other routes were untouched by the rule.
    expect((await h.api.get(`/v2/orders/${SEED_OPEN_ORDER}`, { headers: h.auth })).status).toBe(200);
    await h.stop();
  });

  it('expires the token mid-flow without changing stored state', async () => {
    const h = await harness();
    await addRule(h, {
      id: 'expire',
      scope: 'request',
      fault: 'token_expiry',
      match: { route: 'GET /v2/orders/:order_id' },
      when: { nth: [2] },
    });

    expect((await h.api.get(`/v2/orders/${SEED_OPEN_ORDER}`, { headers: h.auth })).status).toBe(200);
    const expired = await h.api.get<{ errors: Array<{ code: string }> }>(`/v2/orders/${SEED_OPEN_ORDER}`, { headers: h.auth });
    expect(expired.status).toBe(401);
    expect(expired.body.errors[0]!.code).toBe('ACCESS_TOKEN_EXPIRED');
    // The next call succeeds: the fault was injected, the token was never revoked.
    expect((await h.api.get(`/v2/orders/${SEED_OPEN_ORDER}`, { headers: h.auth })).status).toBe(200);
    await h.stop();
  });

  it('injects 5xx and timeouts on the order push', async () => {
    const h = await harness();
    await addRule(h, { id: 'boom', scope: 'request', fault: 'server_error', match: { route: 'POST /v2/orders' }, when: { nth: [1] } });
    await addRule(h, { id: 'slow', scope: 'request', fault: 'timeout', match: { route: 'POST /v2/orders' }, when: { nth: [2] }, params: { delayMs: 25 } });

    const boom = await h.api.post<{ errors: Array<{ code: string; category: string }> }>('/v2/orders', orderBody(), { headers: h.auth });
    expect(boom.status).toBe(500);
    expect(boom.body.errors[0]!.code).toBe('INTERNAL_SERVER_ERROR');
    expect(boom.body.errors[0]!.category).toBe('API_ERROR');

    const started = Date.now();
    const slow = await h.api.post<{ errors: Array<{ code: string }> }>('/v2/orders', orderBody(), { headers: h.auth });
    expect(slow.status).toBe(504);
    expect(slow.body.errors[0]!.code).toBe('GATEWAY_TIMEOUT');
    expect(Date.now() - started).toBeGreaterThanOrEqual(20);

    // A rejected request creates nothing.
    const search = await h.api.post<{ orders: Array<unknown> }>('/v2/orders/search', {}, { headers: h.auth });
    expect(search.body.orders).toHaveLength(2);
    await h.stop();
  });

  it('triggers faults from magic values in ordinary request fields', async () => {
    const h = await harness();
    const limited = await h.api.post<{ errors: Array<{ code: string }> }>(
      '/v2/orders',
      orderBody({ reference_id: 'chaos:rate_limit' }),
      { headers: h.auth },
    );
    expect(limited.status).toBe(429);
    expect(limited.body.errors[0]!.code).toBe('RATE_LIMITED');

    const slow = await h.api.post<{ errors: Array<{ code: string }> }>(
      '/v2/orders',
      orderBody({ reference_id: 'chaos:timeout:delayMs=15' }),
      { headers: h.auth },
    );
    expect(slow.status).toBe(504);

    // The magic value affects only the request that carries it.
    expect((await h.api.post('/v2/orders', orderBody(), { headers: h.auth })).status).toBe(200);
    await h.stop();
  });

  it('duplicates a webhook delivery with a stable event id', async () => {
    const h = await harness();
    await subscribe(h, ['order.created']);
    await addRule(h, { id: 'dup', scope: 'webhook', fault: 'webhook.duplicate', match: { eventType: 'order.created' }, when: { nth: [1] }, params: { copies: 1 } });

    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    const log = await deliveries(h);
    expect(log).toHaveLength(2);
    expect(log[0]!.eventId).toBe(log[1]!.eventId);
    expect(log[0]!.bodyHash).toBe(log[1]!.bodyHash);
    expect(log.every((d) => d.status === 'delivered')).toBe(true);
    await h.stop();
  });

  it('delivers events out of order when told to', async () => {
    const h = await harness();
    await subscribe(h, ['order.updated']);
    await addRule(h, { id: 'reorder', scope: 'webhook', fault: 'webhook.out_of_order', match: { eventType: 'order.updated' }, when: { nth: [1] } });

    await h.api.put(
      `/v2/orders/${SEED_OPEN_ORDER}`,
      { idempotency_key: 'ooo-1', order: { version: 1, ticket_name: 'first' } },
      { headers: h.auth },
    );
    await h.api.put(
      `/v2/orders/${SEED_OPEN_ORDER}`,
      { idempotency_key: 'ooo-2', order: { version: 2, ticket_name: 'second' } },
      { headers: h.auth },
    );
    const log = await deliveries(h);

    const held = log.find((d) => d.status === 'skipped');
    expect(held).toBeTruthy();
    const delivered = log.filter((d) => d.status === 'delivered');
    expect(delivered).toHaveLength(2);
    // The version-3 event reaches the subscriber before the version-2 event.
    const versions = delivered.map((d) => d.body.data.object.order_updated.version);
    expect(versions).toEqual([3, 2]);
    await h.stop();
  });

  it('retries when the subscriber acknowledgement is dropped', async () => {
    const h = await harness();
    await subscribe(h, ['order.created']);
    await addRule(h, { id: 'drop', scope: 'webhook', fault: 'webhook.drop_ack', match: { eventType: 'order.created' }, when: { nth: [1] } });

    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    const log = await deliveries(h);
    expect(log.map((d) => d.status)).toEqual(['failed', 'delivered']);
    // The subscriber really answered 200; the acknowledgement was discarded.
    expect(log[0]!.responseStatus).toBe(200);
    expect(log[0]!.error).toContain('chaos');
    expect(log[0]!.eventId).toBe(log[1]!.eventId);
    await h.stop();
  });

  it('is reproducible: the same rule and traffic give the same answers', async () => {
    const run = async (): Promise<number[]> => {
      const h = await harness();
      await addRule(h, { id: 'every-third', scope: 'request', fault: 'rate_limit', match: { route: 'POST /v2/orders' }, when: { every: 3 } });
      const statuses: number[] = [];
      for (let i = 0; i < 7; i++) {
        statuses.push((await h.api.post('/v2/orders', orderBody(), { headers: h.auth })).status);
      }
      await h.stop();
      return statuses;
    };
    const [a, b] = await Promise.all([run(), run()]);
    expect(a).toEqual([200, 200, 429, 200, 200, 429, 200]);
    expect(a).toEqual(b);
  });

  it('can be switched off and back on at runtime', async () => {
    const h = await harness();
    await addRule(h, { id: 'toggle', scope: 'request', fault: 'rate_limit', match: { route: 'POST /v2/orders' }, when: { always: true } });
    expect((await h.api.post('/v2/orders', orderBody(), { headers: h.auth })).status).toBe(429);

    await h.api.post('/__unit/chaos/rules', { enabled: false });
    expect((await h.api.post('/v2/orders', orderBody(), { headers: h.auth })).status).toBe(200);

    await h.api.post('/__unit/chaos/rules', { enabled: true });
    expect((await h.api.post('/v2/orders', orderBody(), { headers: h.auth })).status).toBe(429);

    await h.api.post('/__unit/chaos/reset', {});
    expect((await h.api.post('/v2/orders', orderBody(), { headers: h.auth })).status).toBe(200);
    await h.stop();
  });

  it('records what fired, so a failing run can be explained', async () => {
    const h = await harness();
    await addRule(h, { id: 'audited', scope: 'request', fault: 'rate_limit', match: { route: 'POST /v2/orders' }, when: { nth: [2] } });
    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });

    const status = await h.api.get<{ rules: Array<{ id: string; matches: number; fires: number }>; events: Array<Record<string, unknown>> }>('/__unit/chaos');
    expect(status.body.rules[0]).toMatchObject({ id: 'audited', matches: 2, fires: 1 });
    expect(status.body.events[0]).toMatchObject({ ruleId: 'audited', fault: 'rate_limit', occurrence: 2, subject: 'POST /v2/orders' });
    await h.stop();
  });

  it('refuses webhook faults when webhooks.chaos is disabled', async () => {
    const h = await harness({ profile: 'no-chaos' });
    const res = await h.api.post<{ errors: Array<{ code: string }>; unit_error: Record<string, unknown> }>('/__unit/chaos/rules', {
      id: 'nope',
      scope: 'webhook',
      fault: 'webhook.duplicate',
    });
    expect(res.status).toBe(501);
    expect(res.body.errors[0]!.code).toBe('NOT_IMPLEMENTED');
    expect(res.body.unit_error).toMatchObject({ capability: 'webhooks.chaos', profile: 'no-chaos' });

    // Request-scope faults are unaffected: they are not part of webhooks.chaos.
    expect((await h.api.post('/__unit/chaos/rules', { id: 'ok', scope: 'request', fault: 'rate_limit' })).status).toBe(200);
    await h.stop();
  });
});
