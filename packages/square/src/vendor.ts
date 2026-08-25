import type { CapabilityDecl, MutableResponse, UnitContext, UnitRequest, VendorDefinition } from '@vendor-unit/core';
import { SquareAuth } from './auth.js';
import { SquareErrorShaper } from './errors.js';
import { SquareEventMapper } from './events.js';
import { hydrateSquare } from './hydrate.js';
import { SquareIds } from './ids.js';
import { SquareWebhookSigner } from './signer.js';
import { oauthRoutes } from './surface/oauth.js';
import { orderRoutes } from './surface/orders.js';
import { directoryRoutes } from './surface/directory.js';
import { webhookRoutes } from './surface/webhooks.js';
import type { SquareDeps, SquareVendorConfig } from './surface/common.js';

/**
 * The Square vendor definition — everything the template needs to become a
 * Square unit, and nothing else. Compare this file's length with the core it
 * plugs into: that ratio is the authoring-economics claim in README.md.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

export const SQUARE_CAPABILITIES: CapabilityDecl[] = [
  { name: 'oauth', summary: 'Authorization-code flow, token refresh, revocation and token status.' },
  { name: 'order-lifecycle', summary: 'Create, retrieve, update, search and pay orders, with state persisting across calls.' },
  { name: 'merchant-directory', summary: 'Locations and catalog — the reference data orders point at.' },
  { name: 'webhooks', summary: 'Signed event delivery to subscribers, with the documented retry schedule.' },
  {
    name: 'webhooks.chaos',
    summary: 'Delivery faults: duplication, reordering, dropped acknowledgements, delay.',
    kind: 'behavior',
    requires: ['webhooks'],
  },
];

export function resolveSquareConfig(raw: Record<string, unknown>): SquareVendorConfig {
  const str = (key: string, fallback: string): string => (typeof raw[key] === 'string' ? (raw[key] as string) : fallback);
  const num = (key: string, fallback: number): number => (raw[key] === undefined ? fallback : Number(raw[key]));
  return {
    applicationId: str('applicationId', 'sandbox-sq0idb-unit-square-application'),
    applicationSecret: str('applicationSecret', 'sandbox-sq0csb-unit-square-secret'),
    redirectUri: str('redirectUri', 'https://example.test/oauth/callback'),
    environment: str('environment', 'Sandbox') === 'Production' ? 'Production' : 'Sandbox',
    // The Square-Version this unit claims to implement. The freshness job
    // compares it against the latest published version and reports drift.
    apiVersion: str('apiVersion', '2026-08-19'),
    errorSidecar: raw.errorSidecar !== false,
    // "An OAuth access token expires after 30 days."
    // https://developer.squareup.com/docs/oauth-api/overview
    accessTokenTtlMs: num('accessTokenTtlMs', 30 * DAY_MS),
    // "If true, the access token expires in 24 hours."
    // https://developer.squareup.com/reference/square/oauth-api/obtain-token
    shortLivedTtlMs: num('shortLivedTtlMs', DAY_MS),
    // "Refresh tokens obtained using the PKCE flow ... expire after 90 days."
    pkceRefreshTtlMs: num('pkceRefreshTtlMs', 90 * DAY_MS),
    // "Authorization codes ... expire after 5 minutes."
    authorizationCodeTtlMs: num('authorizationCodeTtlMs', 5 * 60 * 1000),
    // The documented default scope set on GET /oauth2/authorize.
    // https://developer.squareup.com/reference/square/oauth-api/authorize
    defaultScopes: Array.isArray(raw.defaultScopes)
      ? (raw.defaultScopes as string[])
      : ['MERCHANT_PROFILE_READ', 'PAYMENTS_READ', 'SETTLEMENTS_READ', 'BANK_ACCOUNTS_READ'],
  };
}

export interface SquareVendorOptions {
  vendorConfig?: Record<string, unknown>;
  /** Seeds the deterministic id generator; pass the profile's chaos seed. */
  seed?: number;
}

export function createSquareVendor(opts: SquareVendorOptions = {}): VendorDefinition {
  const config = resolveSquareConfig(opts.vendorConfig ?? {});
  const deps: SquareDeps = { ids: new SquareIds(opts.seed ?? 1), config };

  return {
    name: 'square',
    displayName: 'Square (Connect v2)',
    apiVersion: config.apiVersion,
    capabilities: SQUARE_CAPABILITIES,
    routes: [...oauthRoutes(deps), ...orderRoutes(deps), ...directoryRoutes(), ...webhookRoutes(deps)],
    errors: new SquareErrorShaper({ sidecar: config.errorSidecar }),
    auth: new SquareAuth(() => config.applicationSecret),
    signer: new SquareWebhookSigner(config.environment),
    events: new SquareEventMapper(),
    volatileFields: ['expiresAt', 'refreshTokenExpiresAt', 'closedAt', 'usedAt', 'revokedAt'],
    /**
     * In-band fault triggering. Square's own Sandbox uses magic values in
     * ordinary request fields, so the mock uses the fields a consumer can
     * actually set through an SDK: an order's `reference_id`, the OAuth `state`
     * parameter, and the idempotency key.
     * https://developer.squareup.com/docs/devtools/sandbox/testing
     */
    magic: {
      prefix: 'chaos:',
      bodyPaths: ['order.reference_id', 'idempotency_key', 'subscription.name'],
      queryParams: ['state'],
    },
    hydrate: (ctx, seed) =>
      hydrateSquare(ctx, seed, {
        clientId: config.applicationId,
        apiVersion: config.apiVersion,
        accessTokenTtlMs: config.accessTokenTtlMs,
      }),
    decorate: (res: MutableResponse, ctx: UnitContext, req: UnitRequest) => {
      // "The response always returns the Square-Version header indicating the
      // version used." https://developer.squareup.com/docs/build-basics/versioning-overview
      res.headers['square-version'] = req.headers['square-version'] ?? config.apiVersion;
      res.headers['x-unit-vendor'] = ctx.vendor.name;
    },
  };
}
