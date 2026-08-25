import { createHmac } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { SEED_OPEN_ORDER, harness, orderBody, subscribe, type Harness } from './helpers.js';

/**
 * Slice element 3: webhook emission with Square's documented signature scheme
 * and at-least-once delivery.
 *
 * The signature is verified here with an independent implementation of the
 * documented algorithm, not by calling the unit's own signer — otherwise the
 * test would only prove the signer agrees with itself.
 *   base64(HMAC-SHA256(signature_key, notification_url + raw_body))
 *   https://developer.squareup.com/docs/webhooks/step3validate
 *   https://github.com/square/square-python-sdk/blob/master/src/square/utils/webhooks_helper.py
 */
function verifySquareSignature(signatureKey: string, notificationUrl: string, rawBody: Uint8Array, header: string): boolean {
  const payload = Buffer.concat([Buffer.from(notificationUrl, 'utf8'), Buffer.from(rawBody)]);
  return createHmac('sha256', signatureKey).update(payload).digest('base64') === header;
}

async function deliveries(h: Harness): Promise<Array<Record<string, any>>> {
  await h.api.post('/__unit/webhooks/drain', {});
  const res = await h.api.get<{ deliveries: Array<Record<string, any>> }>('/__unit/webhooks/deliveries');
  return res.body.deliveries;
}

describe('webhooks', () => {
  it('emits a signed order.created with the documented envelope', async () => {
    const h = await harness();
    const sub = await subscribe(h, ['order.created', 'order.updated']);
    const created = await h.api.post<{ order: { id: string } }>('/v2/orders', orderBody(), { headers: h.auth });

    const sent = h.sink.received;
    await h.api.post('/__unit/webhooks/drain', {});
    expect(sent).toHaveLength(1);
    const delivery = sent[0]!;
    expect(delivery.url).toBe(sub.url);

    const signature = delivery.headers['x-square-hmacsha256-signature'];
    expect(signature).toBeTruthy();
    expect(verifySquareSignature(sub.signatureKey, sub.url, delivery.body, signature!)).toBe(true);
    // A different key or URL must not verify.
    expect(verifySquareSignature('wrong-key', sub.url, delivery.body, signature!)).toBe(false);
    expect(verifySquareSignature(sub.signatureKey, 'https://elsewhere.test/hooks', delivery.body, signature!)).toBe(false);
    expect(delivery.headers['square-environment']).toBe('Sandbox');

    const body = JSON.parse(Buffer.from(delivery.body).toString('utf8'));
    expect(body.type).toBe('order.created');
    expect(body.merchant_id).toBe('MLQW2MYBY81PZ');
    expect(body.event_id).toMatch(/^[0-9a-f]{8}-/);
    expect(body.data.type).toBe('order_created');
    expect(body.data.id).toBe(created.body.order.id);
    expect(body.data.object.order_created).toEqual({
      created_at: expect.any(String),
      location_id: '18YC4JDH91E1H',
      order_id: created.body.order.id,
      state: 'OPEN',
      version: 1,
    });
    await h.stop();
  });

  it('emits order.updated carrying the new version', async () => {
    const h = await harness();
    await subscribe(h, ['order.*']);
    await h.api.put(
      `/v2/orders/${SEED_OPEN_ORDER}`,
      { idempotency_key: 'wh-upd', order: { version: 1, ticket_name: 'Window' } },
      { headers: h.auth },
    );
    await h.api.post('/__unit/webhooks/drain', {});

    const body = JSON.parse(Buffer.from(h.sink.received[0]!.body).toString('utf8'));
    expect(body.type).toBe('order.updated');
    expect(body.data.type).toBe('order_updated');
    expect(body.data.object.order_updated.version).toBe(2);
    expect(body.data.object.order_updated.state).toBe('OPEN');
    expect(body.data.object.order_updated.updated_at).toBeTruthy();
    await h.stop();
  });

  it('does not emit events for the seed scenario', async () => {
    const h = await harness();
    // Subscribe first, then reset state: re-seeding must stay silent.
    await subscribe(h, ['*']);
    await h.api.post('/__unit/state/reset', {});
    await h.api.post('/__unit/webhooks/drain', {});
    expect(h.sink.received).toHaveLength(0);
    await h.stop();
  });

  it('only delivers to subscriptions that asked for the event type', async () => {
    const h = await harness();
    await subscribe(h, ['payment.created'], 'https://payments.test/hooks');
    await subscribe(h, ['order.created'], 'https://orders.test/hooks');
    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    await h.api.post('/__unit/webhooks/drain', {});

    expect(h.sink.received.map((r) => r.url)).toEqual(['https://orders.test/hooks']);
    await h.stop();
  });

  it('retries a failing subscriber on the documented backoff shape', async () => {
    const h = await harness();
    await subscribe(h, ['order.created']);
    // Fail the first two attempts, then accept.
    h.sink.respondWith = (_req, index) => (index < 2 ? 500 : 200);

    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    const log = await deliveries(h);

    expect(log.map((d) => d.status)).toEqual(['failed', 'failed', 'delivered']);
    expect(log.map((d) => d.retryNumber)).toEqual([0, 1, 2]);
    // Square's schedule is 1 minute then 2 minutes; the profile scales it by
    // 0.000167 so a test can observe the shape (60000*0.000167 = 10ms).
    expect(log[0]!.nextAttemptInMs).toBe(10);
    expect(log[1]!.nextAttemptInMs).toBe(20);
    // Same event id on every attempt: that is the consumer's dedup handle.
    expect(new Set(log.map((d) => d.eventId)).size).toBe(1);
    expect(log[1]!.headers['square-retry-number']).toBe('1');
    expect(log[1]!.headers['square-retry-reason']).toBe('http_error');
    await h.stop();
  });

  it('gives up after the full retry schedule', async () => {
    const h = await harness();
    await subscribe(h, ['order.created']);
    h.sink.respondWith = 500;
    // Collapse the schedule so the test does not spend 24 scaled hours.
    await h.api.post('/__unit/webhooks/retry-policy', { timeScale: 0.0000001 });

    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    const log = await deliveries(h);

    // 1 initial attempt + 11 documented retries.
    expect(log).toHaveLength(12);
    expect(log.at(-1)!.status).toBe('exhausted');
    expect(log.filter((d) => d.status === 'failed')).toHaveLength(11);
    await h.stop();
  });

  it('reports a timed-out subscriber as http_timeout', async () => {
    const h = await harness();
    await subscribe(h, ['order.created']);
    h.sink.respondWith = (_req, index) => (index === 0 ? 0 : 200);

    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    const log = await deliveries(h);
    expect(log[0]!.status).toBe('failed');
    expect(log[1]!.headers['square-retry-reason']).toBe('http_timeout');
    expect(log[1]!.status).toBe('delivered');
    await h.stop();
  });

  it('registers a subscriber through Square\'s own API and delivers to it', async () => {
    const h = await harness();
    const created = await h.api.post<{ subscription: Record<string, any> }>(
      '/v2/webhooks/subscriptions',
      {
        idempotency_key: 'sub-1',
        subscription: {
          name: 'Example Webhook Subscription',
          event_types: ['order.created'],
          notification_url: 'https://api-created.test/hooks',
        },
      },
      { headers: h.auth },
    );
    expect(created.status).toBe(200);
    const subscription = created.body.subscription;
    expect(subscription.id).toMatch(/^wbhk_[0-9a-f]{32}$/);
    expect(subscription.enabled).toBe(true);
    expect(subscription.signature_key).toBeTruthy();
    expect(subscription.api_version).toBe('2026-08-19');

    await h.api.post('/v2/orders', orderBody(), { headers: h.auth });
    await h.api.post('/__unit/webhooks/drain', {});

    const delivery = h.sink.received[0]!;
    expect(delivery.url).toBe('https://api-created.test/hooks');
    expect(
      verifySquareSignature(subscription.signature_key, subscription.notification_url, delivery.body, delivery.headers['x-square-hmacsha256-signature']!),
    ).toBe(true);

    const listed = await h.api.get<{ subscriptions: Array<{ id: string }> }>('/v2/webhooks/subscriptions', { headers: h.auth });
    expect(listed.body.subscriptions.map((s) => s.id)).toContain(subscription.id);

    const tested = await h.api.post<{ subscription_test_result: { status_code: number } }>(
      `/v2/webhooks/subscriptions/${subscription.id}/test`,
      { event_type: 'order.created' },
      { headers: h.auth },
    );
    expect(tested.body.subscription_test_result.status_code).toBe(200);

    await h.api.del(`/v2/webhooks/subscriptions/${subscription.id}`, { headers: h.auth });
    const gone = await h.api.get(`/v2/webhooks/subscriptions/${subscription.id}`, { headers: h.auth });
    expect(gone.status).toBe(404);
    await h.stop();
  });
});
