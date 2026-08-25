import { UnitError, type AuthAdapter, type AuthResult, type HandlerArgs } from '@vendor-unit/core';
import { COL, type TokenEntity } from './entities.js';

/**
 * Square authentication.
 *
 * Two modes, both documented:
 *   `bearer`        `Authorization: Bearer {ACCESS_TOKEN}` on every v2 call.
 *                   https://developer.squareup.com/docs/build-basics/access-tokens
 *   `client-secret` `Authorization: Client {APPLICATION_SECRET}` on
 *                   POST /oauth2/revoke.
 *                   https://developer.squareup.com/docs/oauth-api/cookbook/revoke-oauth-tokens
 *
 * Token *validity* is intentionally not part of the `oauth` capability: a
 * profile with OAuth switched off still authenticates the seeded token, so a
 * consumer that does not test the OAuth dance is not forced to run it.
 */
export const SQUARE_SCOPES = [
  'MERCHANT_PROFILE_READ',
  'ORDERS_READ',
  'ORDERS_WRITE',
  'ITEMS_READ',
  'PAYMENTS_WRITE',
] as const;

export class SquareAuth implements AuthAdapter {
  constructor(private readonly getApplicationSecret: () => string) {}

  describe(): Record<string, string> {
    return {
      bearer: 'Authorization: Bearer {ACCESS_TOKEN} (developer.squareup.com/docs/build-basics/access-tokens)',
      'client-secret': 'Authorization: Client {APPLICATION_SECRET} (developer.squareup.com/docs/oauth-api/cookbook/revoke-oauth-tokens)',
      scopes: SQUARE_SCOPES.join(' '),
    };
  }

  resolve(args: Omit<HandlerArgs, 'auth'>, mode: string): AuthResult {
    const header = args.req.headers['authorization'];
    if (!header) {
      throw new UnitError('unauthorized', {
        detail: 'The `Authorization` http header of your request was incorrect or expired.',
        info: { expected: mode },
      });
    }

    if (mode === 'client-secret') {
      const [scheme, ...rest] = header.split(' ');
      const secret = rest.join(' ');
      if (scheme !== 'Client' || secret !== this.getApplicationSecret()) {
        throw new UnitError('unauthorized', {
          detail: 'The `Authorization` http header of your request was incorrect or expired.',
          info: { expected: 'Authorization: Client {APPLICATION_SECRET}' },
        });
      }
      return { principalId: 'application', scopes: [...SQUARE_SCOPES], meta: { mode } };
    }

    const [scheme, ...rest] = header.split(' ');
    if (scheme !== 'Bearer' || rest.length === 0) {
      throw new UnitError('unauthorized', {
        detail: 'The `Authorization` http header of your request was incorrect or expired.',
        info: { expected: 'Authorization: Bearer {ACCESS_TOKEN}' },
      });
    }
    const value = rest.join(' ');
    const token = args.ctx.store.collection<TokenEntity>(COL.tokens).find((t) => t.accessToken === value);
    if (!token) {
      throw new UnitError('unauthorized', { detail: 'This request could not be authorized.' });
    }
    if (token.revokedAt) {
      throw new UnitError('token_revoked', { detail: 'The provided access token has been revoked.' });
    }
    if (Date.parse(token.expiresAt) <= args.ctx.clock.now()) {
      throw new UnitError('token_expired', { detail: 'The provided access token has expired.' });
    }
    return {
      principalId: token.merchantId,
      scopes: token.scopes,
      tokenId: token.id,
      meta: { clientId: token.clientId, shortLived: token.shortLived, flow: token.flow },
    };
  }
}
