/**
 * Seeded RNG (mulberry32).
 *
 * The chaos engine's *triggering* is counter-based and never consults this —
 * see chaos/engine.ts. The RNG exists only for rules that explicitly ask for a
 * probability, and even then the seed is in the profile and is reported by
 * `/__unit/info`, so a run is replayable from its report alone.
 */
export class Rng {
  private state: number;
  readonly seed: number;
  private draws = 0;

  constructor(seed: number) {
    this.seed = seed >>> 0;
    this.state = this.seed;
  }

  next(): number {
    this.draws++;
    this.state = (this.state + 0x6d2b79f5) >>> 0;
    let t = this.state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  int(maxExclusive: number): number {
    return Math.floor(this.next() * maxExclusive);
  }

  /** Deterministic hex string of `bytes` bytes. */
  hex(bytes: number): string {
    let out = '';
    for (let i = 0; i < bytes; i++) out += this.int(256).toString(16).padStart(2, '0');
    return out;
  }

  reset(): void {
    this.state = this.seed;
    this.draws = 0;
  }

  get drawCount(): number {
    return this.draws;
  }
}
