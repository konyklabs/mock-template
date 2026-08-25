/**
 * Clock + scheduler.
 *
 * `virtual` mode exists so that behaviour measured in vendor-scale time (a
 * 30-day token life, a 24-hour webhook retry schedule) is testable in a
 * millisecond without sleeping: the control plane advances the clock and every
 * timer that became due fires synchronously.
 */
export type ClockMode = 'real' | 'virtual';

export interface Timer {
  id: number;
  dueAt: number;
  fn: () => void | Promise<void>;
  label: string;
}

export class Clock {
  readonly mode: ClockMode;
  private virtualNow: number;
  private nextId = 1;
  private timers = new Map<number, Timer>();
  private realTimers = new Map<number, ReturnType<typeof setTimeout>>();

  constructor(mode: ClockMode = 'real', start?: string) {
    this.mode = mode;
    this.virtualNow = start ? Date.parse(start) : Date.now();
  }

  now(): number {
    return this.mode === 'virtual' ? this.virtualNow : Date.now();
  }

  /** RFC 3339 with milliseconds, the format Square uses for `created_at`. */
  isoMs(offsetMs = 0): string {
    return new Date(this.now() + offsetMs).toISOString();
  }

  /** RFC 3339 truncated to seconds, the format Square uses for `expires_at`. */
  isoSeconds(offsetMs = 0): string {
    return new Date(this.now() + offsetMs).toISOString().replace(/\.\d{3}Z$/, 'Z');
  }

  after(delayMs: number, label: string, fn: () => void | Promise<void>): number {
    const id = this.nextId++;
    const dueAt = this.now() + Math.max(0, delayMs);
    this.timers.set(id, { id, dueAt, fn, label });
    if (this.mode === 'real') {
      const handle = setTimeout(() => {
        this.realTimers.delete(id);
        const t = this.timers.get(id);
        if (!t) return;
        this.timers.delete(id);
        void t.fn();
      }, Math.max(0, delayMs));
      // Do not hold the process open for a pending webhook retry.
      if (typeof handle === 'object' && handle && 'unref' in handle) (handle as { unref(): void }).unref();
      this.realTimers.set(id, handle);
    }
    return id;
  }

  cancel(id: number): void {
    this.timers.delete(id);
    const h = this.realTimers.get(id);
    if (h) {
      clearTimeout(h);
      this.realTimers.delete(id);
    }
  }

  /** Virtual mode only: move time forward and fire everything that came due. */
  async advance(ms: number): Promise<number> {
    if (this.mode !== 'virtual') {
      throw new Error('clock.advance requires clock.mode="virtual"');
    }
    this.virtualNow += ms;
    let fired = 0;
    // Re-scan after each firing: a timer may schedule another due timer.
    for (;;) {
      const due = [...this.timers.values()].filter((t) => t.dueAt <= this.virtualNow).sort((a, b) => a.dueAt - b.dueAt);
      if (due.length === 0) break;
      const t = due[0]!;
      this.timers.delete(t.id);
      await t.fn();
      fired++;
    }
    return fired;
  }

  pending(): Array<{ id: number; label: string; dueInMs: number }> {
    const now = this.now();
    return [...this.timers.values()].map((t) => ({ id: t.id, label: t.label, dueInMs: t.dueAt - now }));
  }

  clearAll(): void {
    for (const id of [...this.timers.keys()]) this.cancel(id);
  }
}

export const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
