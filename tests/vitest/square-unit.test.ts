import { createHmac } from 'node:crypto';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { call, launchUnit, type UnitHandle } from '../support/launch.js';
import { startSubscriber, type SubscriberHandle } from '../support/subscriber.js';

/**
 * Consumer-side integration test (TypeScript / Vitest).
 *
 * This is what a team integrating with Square would write: it knows the base
 * URL and nothing else about the unit's internals. It runs against the
 * container image when a container runtime is present, and against the same
 * built server as a plain process otherwise — the assertions do not change.
 */
const APPLICATION_ID = 'sandbox-sq0idb-unit-square-application';
const APPLICATION_SECRET = 'sandbox-sq0csb-unit-square-secret';
const SEED_LOCATION = '18YC4JDH91E1H';
const TEA_MUG = '2TZFAOHWGG7PAK2QEXWYPZSP';

let unit: UnitHandle;
let subscriber: SubscriberHandle;

beforeAll(async () => {
  // The subscriber must exist before the unit starts: a container needs its
  // port published to the Testcontainers host alias at start time.
  subscriber = await startSubscriber();
  unit = await launchUnit({ profile: 'full', exposeHostPorts: [subscriber.port] });
  // Printed so a green run always says what it actually exercised.
  console.log(`\n[integration] backend=${unit.backend} (${unit.describe}) baseUrl=${unit.baseUrl} subscriber=${unit.hostUrl(subscriber.port)}\n`);
}, 180_000);

afterAll(async () => {
  await unit?.stop();
  await subscriber?.close();
});

describe('square unit, over the wire', () => {
  it('reports healthy and describes itself', async () => {
    const health = await call<{ status: string; vendor: string; profile: string }>(unit.baseUrl, 'GET', '/__unit/health');
    expect(health.status).toBe(200);
    expect(health.body).toMatchObject({ status: 'ok', vendor: 'square', profile: 'full' });

    const info = await call<{ vendor: { apiVersion: string }; capabilities: Array<{ name: string; enabled: boolean }> }>(
      unit.baseUrl,
      'GET',
      '/__unit/info',
    );
    expect(info.body.vendor.apiVersion).toBe('2026-08-19');
    expect(info.body.capabilities.map((c) => c.name)).toEqual([
      'oauth',
      'order-lifecycle',
      'merchant-directory',
      'webhooks',
      'webhooks.chaos',
    ]);
  });

  it('completes the OAuth flow and uses the token to drive an order to COMPLETED', async () => {
    const authorize = await call(
      unit.baseUrl,
      'GET',
      `/oauth2/authorize?client_id=${APPLICATION_ID}&scope=${encodeURIComponent('ORDERS_READ ORDERS_WRITE PAYMENTS_WRITE MERCHANT_PROFILE_READ ITEMS_READ')}&state=vitest&redirect_uri=${encodeURIComponent('https://example.test/oauth/callback')}`,
    );
    expect(authorize.status).toBe(302);
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;
    expect(code).toMatch(/^sq0cgb-/);

    const token = await call<{ access_token: string; token_type: string; merchant_id: string }>(unit.baseUrl, 'POST', '/oauth2/token', {
      body: { client_id: APPLICATION_ID, client_secret: APPLICATION_SECRET, grant_type: 'authorization_code', code },
    });
    expect(token.status).toBe(200);
    expect(token.body.token_type).toBe('bearer');
    const auth = { authorization: `Bearer ${token.body.access_token}` };

    const locations = await call<{ locations: Array<{ id: string }> }>(unit.baseUrl, 'GET', '/v2/locations', { headers: auth });
    expect(locations.body.locations.map((l) => l.id)).toContain(SEED_LOCATION);

    const created = await call<{ order: { id: string; state: string; version: number; total_money: { amount: number } } }>(
      unit.baseUrl,
      'POST',
      '/v2/orders',
      {
        headers: auth,
        body: {
          idempotency_key: 'vitest-integration-1',
          order: { location_id: SEED_LOCATION, line_items: [{ catalog_object_id: TEA_MUG, quantity: '2' }] },
        },
      },
    );
    expect(created.status).toBe(200);
    expect(created.body.order.state).toBe('OPEN');
    expect(created.body.order.total_money.amount).toBe(300);
    const orderId = created.body.order.id;

    const paid = await call<{ order: { state: string; version: number } }>(unit.baseUrl, 'POST', `/v2/orders/${orderId}/pay`, {
      headers: auth,
      body: { idempotency_key: 'vitest-integration-pay', order_version: 1, payment_ids: ['PAY_INTEGRATION'] },
    });
    expect(paid.body.order.state).toBe('COMPLETED');

    // State survives the call boundary: a fresh request sees the mutation.
    const fetched = await call<{ order: { state: string; version: number } }>(unit.baseUrl, 'GET', `/v2/orders/${orderId}`, { headers: auth });
    expect(fetched.body.order.state).toBe('COMPLETED');
    expect(fetched.body.order.version).toBe(2);
  });

  it('delivers a signed order.created to a real subscriber, and retries a failure', async () => {
    const notificationUrl = unit.hostUrl(subscriber.port);
    const signatureKey = 'integration-signature-key';
    const auth = { authorization: 'Bearer EAAAl-unit-seeded-access-token-full-scopes' };

    await call(unit.baseUrl, 'POST', '/__unit/webhooks/subscriptions', {
      body: { id: 'wbhk_integration', notificationUrl, eventTypes: ['order.created'], signatureKey },
    });

    // Reject the first delivery so the retry actually crosses the network.
    subscriber.respondWith = (index) => (index === 0 ? 500 : 200);

    await call(unit.baseUrl, 'POST', '/v2/orders', {
      headers: auth,
      body: {
        idempotency_key: 'vitest-webhook-1',
        order: { location_id: SEED_LOCATION, line_items: [{ catalog_object_id: TEA_MUG, quantity: '1' }] },
      },
    });
    await call(unit.baseUrl, 'POST', '/__unit/webhooks/drain', { body: {} });

    expect(subscriber.received).toHaveLength(2);
    const [failed, retried] = subscriber.received;

    // Verify the signature exactly as Square documents it, over the RAW bytes
    // the subscriber received: base64(HMAC-SHA256(key, notification_url + body)).
    for (const delivery of [failed!, retried!]) {
      const expected = createHmac('sha256', signatureKey)
        .update(Buffer.concat([Buffer.from(notificationUrl, 'utf8'), delivery.rawBody]))
        .digest('base64');
      expect(delivery.headers['x-square-hmacsha256-signature']).toBe(expected);
      expect(delivery.headers['square-environment']).toBe('Sandbox');
    }

    const event = JSON.parse(failed!.rawBody.toString('utf8'));
    expect(event.type).toBe('order.created');
    expect(event.data.type).toBe('order_created');
    expect(event.data.object.order_created.state).toBe('OPEN');

    // At-least-once: the retry is the same event, so the consumer dedupes on event_id.
    expect(JSON.parse(retried!.rawBody.toString('utf8')).event_id).toBe(event.event_id);
    expect(retried!.headers['square-retry-number']).toBe('1');
    expect(retried!.headers['square-retry-reason']).toBe('http_error');
  });

  it('answers a disabled capability explicitly and injects a deterministic 429', async () => {
    await call(unit.baseUrl, 'POST', '/__unit/capabilities', { body: { disable: ['merchant-directory'] } });
    const disabled = await call<{ errors: Array<{ code: string }> }>(unit.baseUrl, 'GET', '/v2/locations', {
      headers: { authorization: 'Bearer EAAAl-unit-seeded-access-token-full-scopes' },
    });
    expect(disabled.status).toBe(501);
    expect(disabled.headers['x-unit-error']).toBe('capability_disabled');
    expect(disabled.body.errors[0]!.code).toBe('NOT_IMPLEMENTED');
    await call(unit.baseUrl, 'POST', '/__unit/capabilities', { body: { enable: ['merchant-directory'] } });

    await call(unit.baseUrl, 'POST', '/__unit/chaos/rules', {
      body: { id: 'integration-429', scope: 'request', fault: 'rate_limit', match: { route: 'GET /v2/locations' }, when: { nth: [2] } },
    });
    const auth = { authorization: 'Bearer EAAAl-unit-seeded-access-token-full-scopes' };
    const first = await call(unit.baseUrl, 'GET', '/v2/locations', { headers: auth });
    const second = await call<{ errors: Array<{ code: string }> }>(unit.baseUrl, 'GET', '/v2/locations', { headers: auth });
    const third = await call(unit.baseUrl, 'GET', '/v2/locations', { headers: auth });

    expect([first.status, second.status, third.status]).toEqual([200, 429, 200]);
    expect(second.body.errors[0]!.code).toBe('RATE_LIMITED');
    expect(second.headers['retry-after']).toBe('1');
    await call(unit.baseUrl, 'POST', '/__unit/chaos/reset', { body: {} });
  });
});
