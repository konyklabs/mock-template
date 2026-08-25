import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createUnit, loadProfile, type DeliverySink, type Logger, type Unit } from '@vendor-unit/core';
import { createSquareVendor } from './vendor.js';

export { createSquareVendor, resolveSquareConfig, SQUARE_CAPABILITIES } from './vendor.js';
export { SquareErrorShaper, SQUARE_ERROR_TABLE } from './errors.js';
export { SquareWebhookSigner, squareSignature } from './signer.js';
export { SquareEventMapper, SQUARE_ORDER_EVENT_TYPES } from './events.js';
export { SquareAuth, SQUARE_SCOPES } from './auth.js';
export { orderMachine, ORDER_MACHINE } from './machine.js';
export { SquareIds } from './ids.js';
export { COL } from './entities.js';
export type { SeedDocument } from './hydrate.js';
export type { SquareVendorConfig } from './surface/common.js';

const here = dirname(fileURLToPath(import.meta.url));
/** `packages/square` at runtime, whether running from src or dist. */
export const SQUARE_PACKAGE_ROOT = join(here, '..');
export const SQUARE_PROFILE_DIR = join(SQUARE_PACKAGE_ROOT, 'profiles');

export interface CreateSquareUnitOptions {
  /** Profile name (a file in profiles/) or an absolute path to a JSON profile. */
  profile?: string;
  env?: NodeJS.ProcessEnv;
  sink?: DeliverySink;
  logger?: Logger;
  /** Merged over the profile's `vendor` block. */
  vendorConfig?: Record<string, unknown>;
}

/**
 * Build and start a Square unit from a profile. This is the whole entry point:
 * everything else in this package is the vendor surface, and everything the
 * unit actually does with it comes from @vendor-unit/core.
 */
export async function createSquareUnit(opts: CreateSquareUnitOptions = {}): Promise<Unit> {
  const loaded = await loadProfile({
    profileDir: SQUARE_PROFILE_DIR,
    baseDir: SQUARE_PACKAGE_ROOT,
    name: opts.profile,
    env: opts.env ?? process.env,
  });
  const vendorConfig = { ...loaded.config.vendorConfig, ...(opts.vendorConfig ?? {}) };
  const vendor = createSquareVendor({ vendorConfig, seed: loaded.config.chaos.seed });
  const unit = createUnit({
    vendor,
    config: { ...loaded.config, vendorConfig },
    seed: loaded.seed,
    sink: opts.sink,
    logger: opts.logger,
  });
  await unit.start();
  return unit;
}
