import { describe, expect, it } from 'vitest';
import { SEED_OPEN_ORDER, harness, orderBody } from './helpers.js';

/**
 * Slice element 5: capability toggles, and the composition claim that a
 * consumer subset is configuration rather than code. Nothing in this file
 * changes a line of the unit; every subset comes from a profile, an environment
 * variable, or a control-plane call.
 */
describe('capabilities', () => {
  it('answers a disabled capability explicitly, never with a 404', async () => {
    const h = await harness({ profile: 'oauth-only' });
    const res = await h.api.post<{ errors: Array<{ code: string; category: string; detail: string }>; unit_error: Record<string, unknown> }>(
      '/v2/orders',
      orderBody(),
      { headers: h.auth },
    );
    expect(res.status).toBe(501);
    expect(res.headers['x-unit-error']).toBe('capability_disabled');
    expect(res.headers['x-unit-capability']).toBe('order-lifecycle');
    expect(res.body.errors[0]!.code).toBe('NOT_IMPLEMENTED');
    expect(res.body.errors[0]!.category).toBe('API_ERROR');
    expect(res.body.errors[0]!.detail).toContain("Capability 'order-lifecycle' is disabled in profile 'oauth-only'");
    expect(res.body.unit_error).toMatchObject({
      kind: 'capability_disabled',
      capability: 'order-lifecycle',
      profile: 'oauth-only',
      route: 'POST /v2/orders',
    });

    // A path this vendor genuinely does not serve still answers 404, so the two
    // cases stay distinguishable.
    const missing = await h.api.get('/v2/subscriptions', { headers: h.auth });
    expect(missing.status).toBe(404);
    expect(missing.headers['x-unit-error']).toBe('not_found');
    await h.stop();
  });

  it('serves the enabled capability in a narrow profile', async () => {
    const h = await harness({ profile: 'oauth-only' });
    const res = await h.api.get('/oauth2/authorize', {
      query: { client_id: 'sandbox-sq0idb-unit-square-application', redirect_uri: 'https://example.test/oauth/callback' },
    });
    expect(res.status).toBe(302);
    await h.stop();
  });

  it('keeps seeded tokens usable when the OAuth capability is off', async () => {
    const h = await harness({ profile: 'orders-only' });
    const oauth = await h.api.post('/oauth2/token', { client_id: 'x', grant_type: 'refresh_token' });
    expect(oauth.status).toBe(501);
    expect(oauth.headers['x-unit-capability']).toBe('oauth');

    // Authentication is not part of the oauth capability: a consumer that does
    // not test the OAuth dance is not forced to run it.
    const order = await h.api.get(`/v2/orders/${SEED_OPEN_ORDER}`, { headers: h.auth });
    expect(order.status).toBe(200);

    const unauthenticated = await h.api.get(`/v2/orders/${SEED_OPEN_ORDER}`);
    expect(unauthenticated.status).toBe(401);
    await h.stop();
  });

  it('takes a capability subset from the environment', async () => {
    const h = await harness({ profile: 'full', env: { UNIT_CAPABILITIES: '-webhooks,-webhooks.chaos' } });
    const caps = await h.api.get<{ capabilities: Array<{ name: string; enabled: boolean }> }>('/__unit/capabilities');
    const byName = Object.fromEntries(caps.body.capabilities.map((c) => [c.name, c.enabled]));
    expect(byName).toMatchObject({ oauth: true, 'order-lifecycle': true, webhooks: false, 'webhooks.chaos': false });

    const res = await h.api.get('/v2/webhooks/subscriptions', { headers: h.auth });
    expect(res.status).toBe(501);
    await h.stop();
  });

  it('toggles capabilities at runtime without a restart', async () => {
    const h = await harness({ profile: 'full' });
    expect((await h.api.get('/v2/locations', { headers: h.auth })).status).toBe(200);

    await h.api.post('/__unit/capabilities', { disable: ['merchant-directory'] });
    expect((await h.api.get('/v2/locations', { headers: h.auth })).status).toBe(501);

    await h.api.post('/__unit/capabilities', { enable: ['merchant-directory'] });
    expect((await h.api.get('/v2/locations', { headers: h.auth })).status).toBe(200);
    await h.stop();
  });

  it('disables a child capability when its parent goes away', async () => {
    const h = await harness({ profile: 'full' });
    await h.api.post('/__unit/capabilities', { disable: ['webhooks'] });
    const caps = await h.api.get<{ capabilities: Array<{ name: string; enabled: boolean; blockedBy?: string }> }>('/__unit/capabilities');
    const chaos = caps.body.capabilities.find((c) => c.name === 'webhooks.chaos')!;
    expect(chaos.enabled).toBe(false);
    await h.stop();
  });

  it('rejects an unknown capability name loudly', async () => {
    const h = await harness();
    const res = await h.api.post<{ errors: Array<{ code: string; detail: string }> }>('/__unit/capabilities', { set: ['not-a-capability'] });
    expect(res.status).toBe(400);
    expect(res.body.errors[0]!.detail).toContain("Unknown capability 'not-a-capability'");
    await h.stop();
  });

  it('reports which routes each capability owns', async () => {
    const h = await harness();
    const caps = await h.api.get<{ capabilities: Array<{ name: string; kind: string; routes: string[] }> }>('/__unit/capabilities');
    const byName = Object.fromEntries(caps.body.capabilities.map((c) => [c.name, c]));
    expect(byName['order-lifecycle']!.routes).toContain('POST /v2/orders');
    expect(byName['order-lifecycle']!.routes).toContain('POST /v2/orders/:order_id/pay');
    expect(byName['webhooks.chaos']!.kind).toBe('behavior');
    expect(byName['webhooks.chaos']!.routes).toEqual([]);
    await h.stop();
  });
});
