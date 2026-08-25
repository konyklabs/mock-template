import type { Route } from './types.js';

export interface RouteMatch {
  route: Route;
  params: Record<string, string>;
}

export type MatchOutcome =
  | { kind: 'match'; match: RouteMatch }
  | { kind: 'method-not-allowed'; allowed: string[] }
  | { kind: 'no-route' };

interface Compiled {
  route: Route;
  segments: string[];
  paramNames: string[];
}

/**
 * Segment router. Deliberately tiny: a vendor surface is a fixed, hand-written
 * list of paths, and every dependency added here is a dependency every fork
 * inherits forever.
 */
export class Router {
  private compiled: Compiled[] = [];

  constructor(routes: Route[] = []) {
    for (const r of routes) this.add(r);
  }

  add(route: Route): void {
    const segments = splitPath(route.path);
    const paramNames = segments.filter((s) => s.startsWith(':')).map((s) => s.slice(1));
    this.compiled.push({ route, segments, paramNames });
  }

  routes(): Route[] {
    return this.compiled.map((c) => c.route);
  }

  match(method: string, path: string): MatchOutcome {
    const wanted = splitPath(path);
    const pathMatches: Compiled[] = [];
    for (const c of this.compiled) {
      if (c.segments.length !== wanted.length) continue;
      let ok = true;
      const params: Record<string, string> = {};
      for (let i = 0; i < c.segments.length; i++) {
        const seg = c.segments[i]!;
        const got = wanted[i]!;
        if (seg.startsWith(':')) {
          params[seg.slice(1)] = decodeURIComponent(got);
        } else if (seg !== got) {
          ok = false;
          break;
        }
      }
      if (!ok) continue;
      pathMatches.push(c);
      if (c.route.method.toUpperCase() === method.toUpperCase()) {
        return { kind: 'match', match: { route: c.route, params } };
      }
    }
    if (pathMatches.length > 0) {
      return { kind: 'method-not-allowed', allowed: [...new Set(pathMatches.map((c) => c.route.method.toUpperCase()))].sort() };
    }
    return { kind: 'no-route' };
  }
}

function splitPath(p: string): string[] {
  return p.split('/').filter((s) => s.length > 0);
}
