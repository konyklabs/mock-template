#!/usr/bin/env node
import { serveFileDrop, serveHttp } from '@vendor-unit/core';
import { createSquareUnit } from '../index.js';

/**
 * Container entry point.
 *
 * Configuration is entirely environment: UNIT_PROFILE picks the capability
 * subset, UNIT_CAPABILITIES adjusts it (`+webhooks,-webhooks.chaos`),
 * UNIT_WEBHOOK_URL registers a subscriber, UNIT_TRANSPORT selects the binding.
 * One image, every subset — see README "Profiles and composition".
 */
async function main(): Promise<void> {
  const unit = await createSquareUnit();
  const transport = process.env.UNIT_TRANSPORT ?? 'http';

  if (transport === 'filedrop') {
    const dir = process.env.UNIT_TRANSPORT_DIR ?? '/data/exchange';
    const handle = await serveFileDrop(unit, dir);
    handle.start(Number(process.env.UNIT_POLL_MS ?? 200));
    process.stdout.write(`square unit listening on file drop ${handle.inDir} -> ${handle.outDir}\n`);
    installShutdown(async () => {
      await handle.stop();
      await unit.stop();
    });
    return;
  }

  const port = Number(process.env.UNIT_PORT ?? 8080);
  const server = await serveHttp(unit, { port, host: process.env.UNIT_HOST ?? '0.0.0.0' });
  process.stdout.write(`square unit listening on http://0.0.0.0:${server.port} (profile ${unit.context.config.profile})\n`);
  installShutdown(async () => {
    await server.close();
    await unit.stop();
  });
}

function installShutdown(close: () => Promise<void>): void {
  let closing = false;
  for (const signal of ['SIGTERM', 'SIGINT'] as const) {
    process.on(signal, () => {
      if (closing) return;
      closing = true;
      void close().then(
        () => process.exit(0),
        () => process.exit(1),
      );
    });
  }
}

main().catch((err: unknown) => {
  process.stderr.write(`${err instanceof Error ? err.stack : String(err)}\n`);
  process.exit(1);
});
