import { MemorySink, formatReport, runConformance } from '@vendor-unit/core';
import { createSquareUnit } from '@vendor-unit/square';
import { describe, expect, it } from 'vitest';

/**
 * The template's own conformance suite, run against this fork.
 *
 * This is the check that a core upgrade did not break the contract between the
 * template and the fork. It runs in the fork's ordinary suite so a stale fork
 * fails on the developer's machine, not in someone else's CI.
 */
describe('template conformance', () => {
  for (const profile of ['full', 'oauth-only', 'orders-only']) {
    it(`passes every template contract in the ${profile} profile`, async () => {
      const report = await runConformance({
        makeUnit: () =>
          createSquareUnit({ profile, sink: new MemorySink(), logger: { debug() {}, info() {}, warn() {}, error() {} } }),
      });
      if (!report.ok) throw new Error(`conformance failed:\n${formatReport(report)}`);
      expect(report.failed).toBe(0);
      expect(report.passed).toBeGreaterThanOrEqual(9);
    });
  }
});
