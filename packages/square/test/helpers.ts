import { MemorySink, inProcess, type InProcessClient, type Unit } from '@vendor-unit/core';
import { createSquareUnit } from '@vendor-unit/square';

/**
 * Test harness for the fork's own suite.
 *
 * Everything here talks to the unit in process (no socket, no container), which
 * is what makes a fork's behavioural suite fast enough to run on every save.
 * The container-backed consumer tests live in tests/vitest and tests/pytest.
 */
export const SEEDED_TOKEN = 'EAAAl-unit-seeded-access-token-full-scopes';
export const SEEDED_READ_TOKEN = 'EAAAl-unit-seeded-access-token-read-only';
export const SEED_OPEN_ORDER = 'CAISENgvlJ6jLWAzERDzjyHVybY';
export const SEED_LOCATION = '18YC4JDH91E1H';
export const TEA_MUG_VARIATION = '2TZFAOHWGG7PAK2QEXWYPZSP';
export const APPLICATION_ID = 'sandbox-sq0idb-unit-square-application';
export const APPLICATION_SECRET = 'sandbox-sq0csb-unit-square-secret';

const silent = { debug() {}, info() {}, warn() {}, error() {} };

export interface Harness {
  unit: Unit;
  api: InProcessClient;
  sink: MemorySink;
  auth: Record<string, string>;
  readAuth: Record<string, string>;
  stop(): Promise<void>;
}

export async function harness(opts: { profile?: string; env?: Record<string, string> } = {}): Promise<Harness> {
  const sink = new MemorySink();
  const unit = await createSquareUnit({
    profile: opts.profile ?? 'full',
    sink,
    logger: silent,
    env: { ...process.env, ...(opts.env ?? {}) },
  });
  return {
    unit,
    api: inProcess(unit),
    sink,
    auth: { authorization: `Bearer ${SEEDED_TOKEN}` },
    readAuth: { authorization: `Bearer ${SEEDED_READ_TOKEN}` },
    stop: () => unit.stop(),
  };
}

/** Register a subscriber through the control plane and return its signature key. */
export async function subscribe(h: Harness, eventTypes: string[] = ['*'], url = 'https://subscriber.test/hooks'): Promise<{ id: string; signatureKey: string; url: string }> {
  const res = await h.api.post<{ subscription: { id: string; signatureKey: string } }>('/__unit/webhooks/subscriptions', {
    notificationUrl: url,
    eventTypes,
    signatureKey: 'test-signature-key',
  });
  return { id: res.body.subscription.id, signatureKey: 'test-signature-key', url };
}

export function orderBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    idempotency_key: `test-${Math.random().toString(36).slice(2)}`,
    order: {
      location_id: SEED_LOCATION,
      line_items: [{ catalog_object_id: TEA_MUG_VARIATION, quantity: '2' }],
      ...overrides,
    },
  };
}
