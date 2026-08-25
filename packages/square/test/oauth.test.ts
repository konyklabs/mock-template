import { describe, expect, it } from 'vitest';
import { APPLICATION_ID, APPLICATION_SECRET, harness } from './helpers.js';

/**
 * OAuth slice element 1: authorize -> code -> token, refresh, revocation, and
 * the obtain-token error modes. Assertions are against the shapes documented at
 * developer.squareup.com/reference/square/oauth-api/*.
 */
describe('oauth', () => {
  const authorizeQuery = (extra: Record<string, string> = {}) => ({
    client_id: APPLICATION_ID,
    scope: 'ORDERS_READ ORDERS_WRITE MERCHANT_PROFILE_READ',
    state: 'unit-test-state',
    redirect_uri: 'https://example.test/oauth/callback',
    ...extra,
  });

  it('redirects back with code, response_type and state', async () => {
    const h = await harness();
    const res = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    expect(res.status).toBe(302);
    const location = new URL(res.headers['location']!);
    expect(location.origin + location.pathname).toBe('https://example.test/oauth/callback');
    expect(location.searchParams.get('response_type')).toBe('code');
    expect(location.searchParams.get('state')).toBe('unit-test-state');
    expect(location.searchParams.get('code')).toMatch(/^sq0cgb-/);
    await h.stop();
  });

  it('redirects with access_denied when the merchant declines', async () => {
    const h = await harness();
    const res = await h.api.get('/oauth2/authorize', { query: authorizeQuery({ unit_prompt: 'deny' }) });
    const location = new URL(res.headers['location']!);
    expect(location.searchParams.get('error')).toBe('access_denied');
    expect(location.searchParams.get('error_description')).toBe('user_denied');
    expect(location.searchParams.get('state')).toBe('unit-test-state');
    await h.stop();
  });

  it('exchanges a code for a bearer token that works on the v2 surface', async () => {
    const h = await harness();
    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;

    const token = await h.api.post<Record<string, unknown>>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: APPLICATION_SECRET,
      grant_type: 'authorization_code',
      code,
    });
    expect(token.status).toBe(200);
    expect(token.body.token_type).toBe('bearer');
    expect(token.body.short_lived).toBe(false);
    expect(token.body.merchant_id).toBe('MLQW2MYBY81PZ');
    expect(String(token.body.access_token)).toMatch(/^EAAA/);
    expect(String(token.body.refresh_token)).toMatch(/^EQAA/);
    // "The timestamp of when the access_token expires, in ISO 8601 format."
    expect(String(token.body.expires_at)).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
    // 30 days for a non-short-lived token.
    const ttlDays = (Date.parse(String(token.body.expires_at)) - Date.now()) / 86_400_000;
    expect(ttlDays).toBeGreaterThan(29.9);
    expect(ttlDays).toBeLessThan(30.1);

    const orders = await h.api.post('/v2/orders/search', {}, { headers: { authorization: `Bearer ${token.body.access_token}` } });
    expect(orders.status).toBe(200);
    await h.stop();
  });

  it('honours short_lived with a 24 hour expiry', async () => {
    const h = await harness();
    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;
    const token = await h.api.post<Record<string, unknown>>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: APPLICATION_SECRET,
      grant_type: 'authorization_code',
      code,
      short_lived: true,
    });
    expect(token.body.short_lived).toBe(true);
    const ttlHours = (Date.parse(String(token.body.expires_at)) - Date.now()) / 3_600_000;
    expect(ttlHours).toBeGreaterThan(23.9);
    expect(ttlHours).toBeLessThan(24.1);
    await h.stop();
  });

  it('rejects a reused authorization code', async () => {
    const h = await harness();
    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;
    const body = { client_id: APPLICATION_ID, client_secret: APPLICATION_SECRET, grant_type: 'authorization_code', code };

    expect((await h.api.post('/oauth2/token', body)).status).toBe(200);
    const second = await h.api.post<{ errors: Array<{ code: string; category: string; detail: string }> }>('/oauth2/token', body);
    expect(second.status).toBe(401);
    expect(second.body.errors[0]!.category).toBe('AUTHENTICATION_ERROR');
    expect(second.body.errors[0]!.detail).toContain('single use');
    await h.stop();
  });

  it('rejects an expired authorization code after five minutes', async () => {
    const h = await harness({ env: { UNIT_CLOCK: 'virtual' } });
    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;
    await h.api.post('/__unit/clock/advance', { ms: 5 * 60 * 1000 + 1000 });
    const res = await h.api.post<{ errors: Array<{ code: string }> }>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: APPLICATION_SECRET,
      grant_type: 'authorization_code',
      code,
    });
    expect(res.status).toBe(401);
    expect(res.body.errors[0]!.code).toBe('UNAUTHORIZED');
    await h.stop();
  });

  it('rejects a wrong client secret and an unsupported grant type', async () => {
    const h = await harness();
    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;

    const badSecret = await h.api.post<{ errors: Array<{ code: string }> }>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: 'wrong',
      grant_type: 'authorization_code',
      code,
    });
    expect(badSecret.status).toBe(401);
    expect(badSecret.body.errors[0]!.code).toBe('UNAUTHORIZED');

    const badGrant = await h.api.post<{ errors: Array<{ code: string; field: string }> }>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: APPLICATION_SECRET,
      grant_type: 'client_credentials',
    });
    expect(badGrant.status).toBe(400);
    expect(badGrant.body.errors[0]!.code).toBe('INVALID_VALUE');
    expect(badGrant.body.errors[0]!.field).toBe('grant_type');

    const missing = await h.api.post<{ errors: Array<{ code: string; field: string }> }>('/oauth2/token', { client_id: APPLICATION_ID });
    expect(missing.status).toBe(400);
    expect(missing.body.errors[0]!.field).toBe('grant_type');
    await h.stop();
  });

  it('refreshes a code-flow token and keeps the same refresh token', async () => {
    const h = await harness();
    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;
    const first = await h.api.post<Record<string, string>>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: APPLICATION_SECRET,
      grant_type: 'authorization_code',
      code,
    });

    const refreshed = await h.api.post<Record<string, string>>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: APPLICATION_SECRET,
      grant_type: 'refresh_token',
      refresh_token: first.body.refresh_token!,
    });
    expect(refreshed.status).toBe(200);
    expect(refreshed.body.refresh_token).toBe(first.body.refresh_token);
    expect(refreshed.body.access_token).not.toBe(first.body.access_token);

    // The superseded access token no longer authenticates.
    const stale = await h.api.post<{ errors: Array<{ code: string }> }>(
      '/v2/orders/search',
      {},
      { headers: { authorization: `Bearer ${first.body.access_token}` } },
    );
    expect(stale.status).toBe(401);
    expect(stale.body.errors[0]!.code).toBe('ACCESS_TOKEN_REVOKED');
    await h.stop();
  });

  it('completes the PKCE flow and issues a new refresh token on refresh', async () => {
    const { createHash, randomBytes } = await import('node:crypto');
    const h = await harness();
    const verifier = randomBytes(32).toString('base64url');
    const challenge = createHash('sha256').update(verifier, 'utf8').digest('base64url');

    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery({ code_challenge: challenge }) });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;

    const wrongVerifier = await h.api.post('/oauth2/token', {
      client_id: APPLICATION_ID,
      grant_type: 'authorization_code',
      code,
      code_verifier: 'not-the-verifier',
    });
    expect(wrongVerifier.status).toBe(401);

    const token = await h.api.post<Record<string, string>>('/oauth2/token', {
      client_id: APPLICATION_ID,
      grant_type: 'authorization_code',
      code,
      code_verifier: verifier,
    });
    expect(token.status).toBe(200);
    // PKCE tokens carry a refresh-token expiry; code-flow tokens do not.
    expect(token.body.refresh_token_expires_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    const refreshed = await h.api.post<Record<string, string>>('/oauth2/token', {
      client_id: APPLICATION_ID,
      grant_type: 'refresh_token',
      refresh_token: token.body.refresh_token!,
    });
    expect(refreshed.body.refresh_token).not.toBe(token.body.refresh_token);
    await h.stop();
  });

  it('revokes tokens with the Client application-secret scheme', async () => {
    const h = await harness();
    const authorize = await h.api.get('/oauth2/authorize', { query: authorizeQuery() });
    const code = new URL(authorize.headers['location']!).searchParams.get('code')!;
    const token = await h.api.post<Record<string, string>>('/oauth2/token', {
      client_id: APPLICATION_ID,
      client_secret: APPLICATION_SECRET,
      grant_type: 'authorization_code',
      code,
    });

    const unauthorized = await h.api.post('/oauth2/revoke', { client_id: APPLICATION_ID, access_token: token.body.access_token });
    expect(unauthorized.status).toBe(401);

    const revoked = await h.api.post<{ success: boolean }>(
      '/oauth2/revoke',
      { client_id: APPLICATION_ID, access_token: token.body.access_token, revoke_only_access_token: true },
      { headers: { authorization: `Client ${APPLICATION_SECRET}` } },
    );
    expect(revoked.status).toBe(200);
    expect(revoked.body).toEqual({ success: true });

    const after = await h.api.post<{ errors: Array<{ code: string }> }>(
      '/v2/orders/search',
      {},
      { headers: { authorization: `Bearer ${token.body.access_token}` } },
    );
    expect(after.status).toBe(401);
    expect(after.body.errors[0]!.code).toBe('ACCESS_TOKEN_REVOKED');
    await h.stop();
  });

  it('reports token status and expires the token on the clock', async () => {
    const h = await harness({ env: { UNIT_CLOCK: 'virtual' } });
    const status = await h.api.post<{ scopes: string[]; merchant_id: string }>(
      '/oauth2/token/status',
      {},
      { headers: h.auth },
    );
    expect(status.status).toBe(200);
    expect(status.body.scopes).toContain('ORDERS_WRITE');

    await h.api.post('/__unit/clock/advance', { ms: 31 * 24 * 60 * 60 * 1000 });
    const expired = await h.api.post<{ errors: Array<{ code: string }> }>('/v2/orders/search', {}, { headers: h.auth });
    expect(expired.status).toBe(401);
    expect(expired.body.errors[0]!.code).toBe('ACCESS_TOKEN_EXPIRED');
    await h.stop();
  });
});
