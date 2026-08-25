import { readFile } from 'node:fs/promises';
import { isAbsolute, join, dirname } from 'node:path';
import type { ResolvedConfig, RetryPolicy, SubscriberConfig } from '../kernel/types.js';
import { applyCapabilityDelta } from '../capability/registry.js';
import { SQUARE_RETRY_SCHEDULE_MS } from '../webhooks/dispatcher.js';

/**
 * Profiles.
 *
 * "Consumer subsets are config, never code": a profile is a JSON document that
 * names the capabilities to enable, the seed scenario, the chaos rules and the
 * subscribers. Environment variables layer on top so one image serves every
 * subset without a rebuild — which is what makes a unit composable into an
 * environment.
 */
export interface ProfileDocument {
  name?: string;
  summary?: string;
  capabilities?: string[];
  seed?: string;
  vendor?: Record<string, unknown>;
  webhooks?: {
    retry?: Partial<RetryPolicy>;
    subscribers?: SubscriberConfig[];
    disableDelivery?: boolean;
  };
  chaos?: { seed?: number; rules?: ResolvedConfig['chaos']['rules'] };
  clock?: { mode?: 'real' | 'virtual'; start?: string };
}

export const DEFAULT_RETRY: RetryPolicy = {
  scheduleMs: SQUARE_RETRY_SCHEDULE_MS,
  // 1/6000 turns the documented "first retry after 1 minute" into 10ms, so a
  // test observes the real schedule shape without waiting a real minute.
  timeScale: 1 / 6000,
  timeoutMs: 10_000,
};

export interface LoadProfileOptions {
  /** Directory holding `<name>.json` profiles. */
  profileDir: string;
  /** Directory that relative `seed` paths resolve against. */
  baseDir?: string;
  name?: string;
  env?: NodeJS.ProcessEnv;
  /** Applied under the profile document, above the built-in defaults. */
  defaults?: Partial<ProfileDocument>;
}

export interface LoadedProfile {
  config: ResolvedConfig;
  seed: unknown;
  document: ProfileDocument;
  sourcePath: string;
}

export async function loadProfile(opts: LoadProfileOptions): Promise<LoadedProfile> {
  const env = opts.env ?? process.env;
  const name = opts.name ?? env.UNIT_PROFILE ?? 'full';
  const sourcePath = isAbsolute(name) || name.endsWith('.json') ? name : join(opts.profileDir, `${name}.json`);
  const doc = JSON.parse(await readFile(sourcePath, 'utf8')) as ProfileDocument;
  const merged: ProfileDocument = {
    ...opts.defaults,
    ...doc,
    vendor: { ...(opts.defaults?.vendor ?? {}), ...(doc.vendor ?? {}) },
    webhooks: { ...(opts.defaults?.webhooks ?? {}), ...(doc.webhooks ?? {}) },
    chaos: { ...(opts.defaults?.chaos ?? {}), ...(doc.chaos ?? {}) },
    clock: { ...(opts.defaults?.clock ?? {}), ...(doc.clock ?? {}) },
  };

  const baseDir = opts.baseDir ?? dirname(sourcePath);
  let capabilities = merged.capabilities ?? [];
  if (env.UNIT_CAPABILITIES) capabilities = applyCapabilityDelta(capabilities, env.UNIT_CAPABILITIES);

  const subscribers: SubscriberConfig[] = [...(merged.webhooks?.subscribers ?? [])];
  if (env.UNIT_WEBHOOK_URL) {
    subscribers.push({
      id: 'wbhk_env',
      name: 'UNIT_WEBHOOK_URL',
      notificationUrl: env.UNIT_WEBHOOK_URL,
      eventTypes: (env.UNIT_WEBHOOK_EVENTS ?? '*').split(',').map((s) => s.trim()),
      signatureKey: env.UNIT_WEBHOOK_SIGNATURE_KEY ?? 'unit-signature-key',
      enabled: true,
    });
  }

  const retry: RetryPolicy = {
    ...DEFAULT_RETRY,
    ...(merged.webhooks?.retry ?? {}),
  };
  if (env.UNIT_WEBHOOK_TIME_SCALE) retry.timeScale = Number(env.UNIT_WEBHOOK_TIME_SCALE);
  if (env.UNIT_WEBHOOK_TIMEOUT_MS) retry.timeoutMs = Number(env.UNIT_WEBHOOK_TIMEOUT_MS);

  const seedPath = env.UNIT_SEED ?? merged.seed;
  let seed: unknown;
  if (seedPath) {
    const full = isAbsolute(seedPath) ? seedPath : join(baseDir, seedPath);
    seed = JSON.parse(await readFile(full, 'utf8'));
  }

  const vendorConfig: Record<string, unknown> = { ...(merged.vendor ?? {}) };
  for (const [k, v] of Object.entries(env)) {
    if (!k.startsWith('UNIT_VENDOR_') || v === undefined) continue;
    vendorConfig[snakeToCamel(k.slice('UNIT_VENDOR_'.length))] = v;
  }

  const config: ResolvedConfig = {
    profile: merged.name ?? name,
    capabilities,
    seedPath,
    vendorConfig,
    webhooks: {
      retry,
      subscribers,
      disableDelivery: merged.webhooks?.disableDelivery ?? false,
    },
    chaos: {
      seed: env.UNIT_CHAOS_SEED ? Number(env.UNIT_CHAOS_SEED) : (merged.chaos?.seed ?? 1),
      rules: merged.chaos?.rules ?? [],
    },
    clock: {
      mode: (env.UNIT_CLOCK as 'real' | 'virtual') ?? merged.clock?.mode ?? 'real',
      start: merged.clock?.start,
    },
    transport: {
      kind: (env.UNIT_TRANSPORT as ResolvedConfig['transport']['kind']) ?? 'http',
      port: env.UNIT_PORT ? Number(env.UNIT_PORT) : 8080,
      dir: env.UNIT_TRANSPORT_DIR,
    },
  };

  return { config, seed, document: merged, sourcePath };
}

function snakeToCamel(s: string): string {
  return s.toLowerCase().replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}
