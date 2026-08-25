import { createHash } from 'node:crypto';
import { UnitError, json, redirect, text, type Route, type UnitContext } from '@vendor-unit/core';
import { COL, type AuthorizationCodeEntity, type MerchantEntity, type TokenEntity } from '../entities.js';
import { optionalString, readBody, requireString, type SquareDeps } from './common.js';

/**
 * OAuth surface.
 *
 * Authorize:   GET  /oauth2/authorize  https://developer.squareup.com/reference/square/oauth-api/authorize
 * ObtainToken: POST /oauth2/token      https://developer.squareup.com/reference/square/oauth-api/obtain-token
 * RevokeToken: POST /oauth2/revoke     https://developer.squareup.com/reference/square/oauth-api/revoke-token
 * TokenStatus: POST /oauth2/token/status
 *              https://developer.squareup.com/reference/square/o-auth-api/retrieve-token-status
 *
 * Documented behaviour reproduced here:
 *  - authorization codes expire after 5 minutes and are single use, and the
 *    redirect carries `code`, `response_type=code` and `state`
 *    (https://developer.squareup.com/docs/oauth-api/receive-and-manage-tokens);
 *  - a denial redirects with `error=access_denied&error_description=user_denied`
 *    (same page);
 *  - `short_lived: true` expires the access token in 24 hours, otherwise 30 days
 *    (https://developer.squareup.com/reference/square/oauth-api/obtain-token);
 *  - code-flow refresh returns the SAME refresh token, PKCE refresh returns a
 *    new single-use one that expires after 90 days
 *    (https://developer.squareup.com/docs/oauth-api/overview);
 *  - revoke takes `Authorization: Client {APPLICATION_SECRET}`, returns
 *    `{"success": true}`, and revokes every token for the merchant unless
 *    `revoke_only_access_token` is set
 *    (https://developer.squareup.com/reference/square/oauth-api/revoke-token).
 *
 * JUDGMENT — Square publishes NO error table, status codes or example error
 * bodies for /oauth2/token or /oauth2/revoke. The failures below use the
 * standard v2 error envelope (which the ObtainToken response schema does list
 * an `errors` array for) with AUTHENTICATION_ERROR/UNAUTHORIZED for credential
 * failures and INVALID_REQUEST_ERROR for malformed requests. Treat statuses on
 * these two endpoints as this mock's convention, not as Square fidelity.
 *
 * JUDGMENT — the real authorize page is an interactive consent screen. A mock
 * has nobody to click it, so approval is automatic; `unit_prompt=deny` produces
 * the documented denial redirect and `unit_prompt=html` renders a two-link
 * consent page for a human driving the flow in a browser. `unit_prompt` is the
 * only non-Square parameter in the vendor surface.
 */
export function oauthRoutes(deps: SquareDeps): Route[] {
  const { config, ids } = deps;

  return [
    {
      method: 'GET',
      path: '/oauth2/authorize',
      capability: 'oauth',
      operationId: 'Authorize',
      summary: 'Authorization page. Redirects back with an authorization code.',
      handler: ({ ctx, query }) => {
        const clientId = query('client_id');
        if (!clientId) throw new UnitError('missing_field', { detail: 'client_id is required.', field: 'client_id' });
        if (clientId !== config.applicationId) {
          throw new UnitError('invalid_value', {
            detail: `Unknown client_id '${clientId}'. This unit is configured for application '${config.applicationId}'.`,
            field: 'client_id',
          });
        }
        const redirectUri = query('redirect_uri') ?? config.redirectUri;
        if (!redirectUri) {
          throw new UnitError('missing_field', {
            detail: 'redirect_uri was not supplied and the unit has no configured redirect URL.',
            field: 'redirect_uri',
          });
        }
        const state = query('state');
        const prompt = query('unit_prompt');

        if (prompt === 'deny') {
          const url = new URL(redirectUri);
          url.searchParams.set('error', 'access_denied');
          url.searchParams.set('error_description', 'user_denied');
          if (state) url.searchParams.set('state', state);
          return redirect(url.toString());
        }

        const scopes = (query('scope') ?? config.defaultScopes.join(' ')).split(/[\s+]+/).filter(Boolean);
        const merchant = firstMerchant(ctx);

        if (prompt === 'html') {
          const approve = new URL(`http://unit.local/oauth2/authorize`);
          for (const [k, v] of Object.entries({ client_id: clientId, redirect_uri: redirectUri, scope: scopes.join(' '), ...(state ? { state } : {}) })) {
            approve.searchParams.set(k, v);
          }
          const denyUrl = new URL(approve);
          denyUrl.searchParams.set('unit_prompt', 'deny');
          return text(
            `<!doctype html><meta charset="utf-8"><title>Authorize ${merchant.businessName}</title>` +
              `<h1>${merchant.businessName}</h1><p>Grant these permissions?</p><ul>${scopes.map((s) => `<li>${s}</li>`).join('')}</ul>` +
              `<p><a href="${approve.pathname}${approve.search}">Allow</a> &middot; <a href="${denyUrl.pathname}${denyUrl.search}">Deny</a></p>`,
            200,
            { 'content-type': 'text/html; charset=utf-8' },
          );
        }

        const code = ids.authorizationCode();
        ctx.store.collection<AuthorizationCodeEntity>(COL.codes).insert(
          {
            id: code,
            clientId,
            merchantId: merchant.id,
            scopes,
            redirectUri,
            codeChallenge: query('code_challenge'),
            expiresAt: ctx.clock.isoSeconds(config.authorizationCodeTtlMs),
          },
          { operationId: 'Authorize' },
        );

        const url = new URL(redirectUri);
        url.searchParams.set('code', code);
        url.searchParams.set('response_type', 'code');
        if (state) url.searchParams.set('state', state);
        return redirect(url.toString());
      },
    },

    {
      method: 'POST',
      path: '/oauth2/token',
      capability: 'oauth',
      operationId: 'ObtainToken',
      summary: 'Exchange an authorization code, or refresh an access token.',
      handler: (args) => {
        const body = readBody(args);
        const clientId = requireString(body, 'client_id');
        const grantType = requireString(body, 'grant_type');
        if (clientId !== config.applicationId) {
          throw new UnitError('unauthorized', { detail: 'The `client_id` does not match this application.', field: 'client_id' });
        }

        if (grantType === 'authorization_code') {
          return json(exchangeCode(args.ctx, deps, body));
        }
        if (grantType === 'refresh_token') {
          return json(refreshToken(args.ctx, deps, body));
        }
        throw new UnitError('invalid_value', {
          detail: `grant_type '${grantType}' is not supported. Supported: authorization_code, refresh_token.`,
          field: 'grant_type',
          info: { supported: ['authorization_code', 'refresh_token'] },
        });
      },
    },

    {
      method: 'POST',
      path: '/oauth2/revoke',
      capability: 'oauth',
      auth: 'client-secret',
      operationId: 'RevokeToken',
      summary: 'Revoke an access token, or every token held for a merchant.',
      handler: (args) => {
        const body = readBody(args);
        const clientId = requireString(body, 'client_id');
        const accessToken = optionalString(body, 'access_token');
        const merchantId = optionalString(body, 'merchant_id');
        if (!accessToken && !merchantId) {
          throw new UnitError('missing_field', { detail: 'Provide either access_token or merchant_id.', field: 'access_token' });
        }
        if (accessToken && merchantId) {
          throw new UnitError('invalid_value', { detail: 'Do not provide access_token together with merchant_id.', field: 'merchant_id' });
        }
        const onlyThisToken = body.revoke_only_access_token === true;
        const tokens = args.ctx.store.collection<TokenEntity>(COL.tokens);

        const target = accessToken ? tokens.find((t) => t.accessToken === accessToken) : undefined;
        if (accessToken && !target) {
          throw new UnitError('unauthorized', { detail: 'The provided access token was not issued by this application.' });
        }
        const merchant = merchantId ?? target!.merchantId;
        const victims = onlyThisToken && target ? [target] : tokens.filter((t) => t.merchantId === merchant && t.clientId === clientId);
        const at = args.ctx.clock.isoMs();
        for (const v of victims) {
          if (v.revokedAt) continue;
          tokens.update(v.id, { meta: { operationId: 'RevokeToken' } }, (d) => {
            d.revokedAt = at;
          });
        }
        return json({ success: true });
      },
    },

    {
      method: 'POST',
      path: '/oauth2/token/status',
      capability: 'oauth',
      auth: 'bearer',
      operationId: 'RetrieveTokenStatus',
      summary: 'Scopes and expiry for the bearer token presented.',
      handler: ({ ctx, auth }) => {
        const token = ctx.store.collection<TokenEntity>(COL.tokens).require(auth!.tokenId!);
        return json({
          scopes: token.scopes,
          expires_at: token.expiresAt,
          client_id: token.clientId,
          merchant_id: token.merchantId,
        });
      },
    },
  ];
}

function firstMerchant(ctx: UnitContext): MerchantEntity {
  const merchants = ctx.store.collection<MerchantEntity>(COL.merchants).all();
  const merchant = merchants[0];
  if (!merchant) {
    throw new UnitError('internal', { detail: 'The seed scenario contains no merchant; OAuth cannot mint a token.' });
  }
  return merchant;
}

function exchangeCode(ctx: UnitContext, deps: SquareDeps, body: Record<string, unknown>): Record<string, unknown> {
  const { config, ids } = deps;
  const codeValue = requireString(body, 'code');
  const codes = ctx.store.collection<AuthorizationCodeEntity>(COL.codes);
  const record = codes.get(codeValue);
  if (!record) {
    throw new UnitError('unauthorized', { detail: 'The authorization code is invalid.', field: 'code' });
  }
  if (record.usedAt) {
    throw new UnitError('unauthorized', { detail: 'The authorization code has already been used. Codes are single use.', field: 'code' });
  }
  if (Date.parse(record.expiresAt) <= ctx.clock.now()) {
    throw new UnitError('unauthorized', { detail: 'The authorization code expired. Codes expire 5 minutes after they are issued.', field: 'code' });
  }

  const flow: 'code' | 'pkce' = record.codeChallenge ? 'pkce' : 'code';
  if (flow === 'pkce') {
    const verifier = requireString(body, 'code_verifier');
    const challenge = createHash('sha256').update(verifier, 'utf8').digest('base64url');
    if (challenge !== record.codeChallenge) {
      throw new UnitError('unauthorized', { detail: 'code_verifier does not match the code_challenge from the authorization request.', field: 'code_verifier' });
    }
  } else {
    const secret = requireString(body, 'client_secret');
    if (secret !== config.applicationSecret) {
      throw new UnitError('unauthorized', { detail: 'The client_secret is incorrect.', field: 'client_secret' });
    }
  }

  codes.update(record.id, { meta: { operationId: 'ObtainToken' } }, (d) => {
    d.usedAt = ctx.clock.isoMs();
  });

  const shortLived = body.short_lived === true;
  const requestedScopes = Array.isArray(body.scopes) ? (body.scopes as string[]) : record.scopes;
  return mintToken(ctx, deps, {
    clientId: record.clientId,
    merchantId: record.merchantId,
    scopes: requestedScopes,
    shortLived,
    flow,
    refreshToken: ids.refreshToken(),
  });
}

function refreshToken(ctx: UnitContext, deps: SquareDeps, body: Record<string, unknown>): Record<string, unknown> {
  const { config, ids } = deps;
  const presented = requireString(body, 'refresh_token');
  const tokens = ctx.store.collection<TokenEntity>(COL.tokens);
  const existing = tokens.find((t) => t.refreshToken === presented);
  if (!existing) {
    throw new UnitError('unauthorized', { detail: 'The refresh token is invalid.', field: 'refresh_token' });
  }
  if (existing.revokedAt) {
    throw new UnitError('unauthorized', { detail: 'The refresh token was revoked.', field: 'refresh_token' });
  }
  if (existing.flow === 'pkce') {
    if (existing.refreshTokenExpiresAt && Date.parse(existing.refreshTokenExpiresAt) <= ctx.clock.now()) {
      throw new UnitError('unauthorized', { detail: 'The refresh token expired. PKCE refresh tokens expire after 90 days.', field: 'refresh_token' });
    }
  } else {
    const secret = requireString(body, 'client_secret');
    if (secret !== config.applicationSecret) {
      throw new UnitError('unauthorized', { detail: 'The client_secret is incorrect.', field: 'client_secret' });
    }
  }

  // A refreshed code-flow token supersedes the old one; PKCE refresh tokens are
  // single use, so the old record is retired either way.
  tokens.update(existing.id, { meta: { operationId: 'ObtainToken', grant: 'refresh_token' } }, (d) => {
    d.revokedAt = ctx.clock.isoMs();
  });

  return mintToken(ctx, deps, {
    clientId: existing.clientId,
    merchantId: existing.merchantId,
    scopes: Array.isArray(body.scopes) ? (body.scopes as string[]) : existing.scopes,
    shortLived: body.short_lived === true ? true : existing.shortLived,
    flow: existing.flow,
    // Code flow keeps the same refresh token; PKCE issues a fresh one.
    refreshToken: existing.flow === 'code' ? existing.refreshToken : ids.refreshToken(),
  });
}

function mintToken(
  ctx: UnitContext,
  deps: SquareDeps,
  spec: { clientId: string; merchantId: string; scopes: string[]; shortLived: boolean; flow: 'code' | 'pkce'; refreshToken: string },
): Record<string, unknown> {
  const { config, ids } = deps;
  const ttl = spec.shortLived ? config.shortLivedTtlMs : config.accessTokenTtlMs;
  const accessToken = ids.accessToken();
  const expiresAt = ctx.clock.isoSeconds(ttl);
  const refreshExpiresAt = spec.flow === 'pkce' ? ctx.clock.isoSeconds(config.pkceRefreshTtlMs) : undefined;

  ctx.store.collection<TokenEntity>(COL.tokens).insert(
    {
      id: ids.internal('tok'),
      accessToken,
      refreshToken: spec.refreshToken,
      clientId: spec.clientId,
      merchantId: spec.merchantId,
      scopes: spec.scopes,
      expiresAt,
      refreshTokenExpiresAt: refreshExpiresAt,
      shortLived: spec.shortLived,
      flow: spec.flow,
    },
    { operationId: 'ObtainToken' },
  );

  return {
    access_token: accessToken,
    token_type: 'bearer',
    expires_at: expiresAt,
    merchant_id: spec.merchantId,
    refresh_token: spec.refreshToken,
    short_lived: spec.shortLived,
    ...(refreshExpiresAt ? { refresh_token_expires_at: refreshExpiresAt } : {}),
  };
}
