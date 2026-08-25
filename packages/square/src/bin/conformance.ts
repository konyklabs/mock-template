#!/usr/bin/env node
import { MemorySink, formatReport, runConformance } from '@vendor-unit/core';
import { createSquareUnit } from '../index.js';

/**
 * Run the template conformance suite against this fork.
 *
 * This is what a fork runs after bumping @vendor-unit/core. A green run is the
 * evidence that the fork still satisfies the template's contracts; a red check
 * names what to fix. See README "Updating a fork from the template".
 */
async function main(): Promise<void> {
  const profile = process.argv[2] ?? process.env.UNIT_PROFILE ?? 'full';
  process.stdout.write(`conformance: @vendor-unit/core against the square fork (profile ${profile})\n\n`);

  const report = await runConformance({
    makeUnit: () =>
      createSquareUnit({
        profile,
        // Deliveries go nowhere during conformance: the suite asserts on the
        // signing contract, not on a live subscriber.
        sink: new MemorySink(),
        logger: { debug() {}, info() {}, warn() {}, error() {} },
      }),
  });

  process.stdout.write(`${formatReport(report)}\n`);
  process.exit(report.ok ? 0 : 1);
}

main().catch((err: unknown) => {
  process.stderr.write(`${err instanceof Error ? err.stack : String(err)}\n`);
  process.exit(1);
});
