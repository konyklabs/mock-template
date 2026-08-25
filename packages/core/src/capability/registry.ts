import { UnitError } from '../kernel/types.js';
import type { CapabilityDecl, Route } from '../kernel/types.js';

/**
 * Capability registry.
 *
 * A unit's surface is partitioned into named capabilities; a profile enables a
 * subset. A route whose capability is off is NOT hidden — hiding it would make
 * "disabled" indistinguishable from "this vendor has no such endpoint", which
 * is exactly the ambiguity that wastes a consumer's afternoon. It answers with
 * an explicit capability_disabled error naming the capability and the profile.
 */
export const CONTROL_CAPABILITY = '__control';

export interface CapabilityView {
  name: string;
  summary: string;
  enabled: boolean;
  kind: 'surface' | 'behavior';
  requires: string[];
  routes: string[];
  /** Present when the capability is off because a prerequisite is off. */
  blockedBy?: string;
}

export class CapabilityRegistry {
  private declared = new Map<string, CapabilityDecl>();
  private enabled = new Set<string>();
  private routesByCapability = new Map<string, string[]>();
  private profileName = 'default';

  constructor(decls: CapabilityDecl[], routes: Route[], enabled: string[], profileName: string) {
    this.profileName = profileName;
    for (const d of decls) this.declared.set(d.name, d);
    this.declared.set(CONTROL_CAPABILITY, { name: CONTROL_CAPABILITY, summary: 'Unit control plane (always on).' });
    for (const r of routes) {
      const key = `${r.method} ${r.path}`;
      const list = this.routesByCapability.get(r.capability) ?? [];
      list.push(key);
      this.routesByCapability.set(r.capability, list);
    }
    this.setEnabled(enabled);
  }

  get profile(): string {
    return this.profileName;
  }

  setProfileName(name: string): void {
    this.profileName = name;
  }

  names(): string[] {
    return [...this.declared.keys()].filter((n) => n !== CONTROL_CAPABILITY);
  }

  isDeclared(name: string): boolean {
    return this.declared.has(name);
  }

  /** Replace the enabled set. Unknown names are rejected loudly, not ignored. */
  setEnabled(names: string[]): void {
    for (const n of names) {
      if (!this.declared.has(n)) {
        throw new UnitError('invalid_value', {
          detail: `Unknown capability '${n}'. Declared: ${this.names().join(', ')}.`,
          field: 'capabilities',
          info: { declared: this.names() },
        });
      }
    }
    this.enabled = new Set([...names, CONTROL_CAPABILITY]);
  }

  enable(name: string): void {
    this.setEnabled([...this.enabled, name].filter((n) => n !== CONTROL_CAPABILITY));
  }

  disable(name: string): void {
    // Disabling a parent implicitly disables its dotted children.
    const next = [...this.enabled].filter(
      (n) => n !== CONTROL_CAPABILITY && n !== name && !n.startsWith(`${name}.`) && !(this.declared.get(n)?.requires ?? []).includes(name),
    );
    this.setEnabled(next);
  }

  /** Why a capability is unusable, or null when it is usable. */
  blockedBy(name: string): string | null {
    if (!this.enabled.has(name)) return name;
    const parent = name.includes('.') ? name.slice(0, name.lastIndexOf('.')) : null;
    if (parent && this.declared.has(parent) && !this.enabled.has(parent)) return parent;
    for (const req of this.declared.get(name)?.requires ?? []) {
      if (!this.enabled.has(req)) return req;
    }
    return null;
  }

  isEnabled(name: string): boolean {
    return this.blockedBy(name) === null;
  }

  assertEnabled(name: string, routeKey?: string): void {
    const blocker = this.blockedBy(name);
    if (blocker === null) return;
    const because =
      blocker === name
        ? `Capability '${name}' is disabled in profile '${this.profileName}'.`
        : `Capability '${name}' is unavailable because its prerequisite '${blocker}' is disabled in profile '${this.profileName}'.`;
    throw new UnitError('capability_disabled', {
      detail: `${because} Enable it in the profile, in UNIT_CAPABILITIES, or with POST /__unit/capabilities.`,
      info: {
        kind: 'capability_disabled',
        capability: name,
        blockedBy: blocker,
        profile: this.profileName,
        route: routeKey,
        enabled: this.enabledNames(),
      },
    });
  }

  enabledNames(): string[] {
    return [...this.enabled].filter((n) => n !== CONTROL_CAPABILITY).sort();
  }

  view(): CapabilityView[] {
    return this.names().map((name) => {
      const decl = this.declared.get(name)!;
      const blocker = this.blockedBy(name);
      const v: CapabilityView = {
        name,
        summary: decl.summary,
        enabled: blocker === null,
        kind: decl.kind ?? 'surface',
        requires: decl.requires ?? [],
        routes: this.routesByCapability.get(name) ?? [],
      };
      if (blocker !== null && blocker !== name) v.blockedBy = blocker;
      return v;
    });
  }
}

/**
 * Parse a delta expression such as `+webhooks,-webhooks.chaos` or an absolute
 * list `oauth,order-lifecycle`. Consumer subsets are configuration; this is the
 * one-line form of it.
 */
export function applyCapabilityDelta(base: string[], expr: string): string[] {
  const parts = expr
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const hasDelta = parts.some((p) => p.startsWith('+') || p.startsWith('-'));
  if (!hasDelta) return parts;
  const set = new Set(base);
  for (const p of parts) {
    if (p.startsWith('-')) set.delete(p.slice(1));
    else set.add(p.replace(/^\+/, ''));
  }
  return [...set];
}
