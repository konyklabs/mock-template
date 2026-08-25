import type { UnitContext } from './types.js';

export interface ControlBinding {
  /** Wipe state and re-apply the seed document. */
  hydrate(): void;
  listRoutes(): Array<{ method: string; path: string; capability: string; auth?: string; operationId?: string; summary?: string; internal?: boolean }>;
}

/**
 * Lets control-plane handlers reach unit internals that are deliberately absent
 * from `UnitContext` (a route handler has no business re-seeding the store).
 */
export const controlBindings = new WeakMap<UnitContext, ControlBinding>();
