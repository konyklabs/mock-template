import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { serveFileDrop, serveHttp } from '@vendor-unit/core';
import { describe, expect, it } from 'vitest';
import { SEEDED_TOKEN, SEED_OPEN_ORDER, harness } from './helpers.js';

/**
 * The transport claim, exercised rather than asserted.
 *
 * The same unit answers the same logical request over three bindings. If the
 * kernel had grown an HTTP assumption, the file-drop case would be the one that
 * broke — which is exactly why it is here.
 */
describe('transport', () => {
  it('serves the same order over HTTP and in process', async () => {
    const h = await harness();
    const server = await serveHttp(h.unit, { port: 0, host: '127.0.0.1' });
    try {
      const viaHttp = await fetch(`${server.url}/v2/orders/${SEED_OPEN_ORDER}`, {
        headers: { authorization: `Bearer ${SEEDED_TOKEN}` },
      });
      const httpText = await viaHttp.text();
      const inProcessRes = await h.api.get(`/v2/orders/${SEED_OPEN_ORDER}`, { headers: h.auth });

      expect(viaHttp.status).toBe(200);
      expect(httpText).toBe(inProcessRes.text);
      expect(viaHttp.headers.get('square-version')).toBe('2026-08-19');
      expect(viaHttp.headers.get('x-unit-vendor')).toBe('square');
    } finally {
      await server.close();
      await h.stop();
    }
  });

  it('serves a request that arrives as a file, not a socket', async () => {
    const h = await harness();
    const dir = await mkdtemp(join(tmpdir(), 'unit-filedrop-'));
    const drop = await serveFileDrop(h.unit, dir);

    await writeFile(
      join(drop.inDir, 'create.request.json'),
      JSON.stringify({
        method: 'POST',
        path: '/v2/orders',
        headers: { authorization: `Bearer ${SEEDED_TOKEN}` },
        body: {
          idempotency_key: 'filedrop-1',
          order: { location_id: '18YC4JDH91E1H', line_items: [{ catalog_object_id: '2TZFAOHWGG7PAK2QEXWYPZSP', quantity: '3' }] },
        },
      }),
      'utf8',
    );

    expect(await drop.poll()).toBe(1);
    const response = JSON.parse(await readFile(join(drop.outDir, 'create.response.json'), 'utf8'));
    expect(response.status).toBe(200);
    expect(response.body.order.total_money).toEqual({ amount: 450, currency: 'USD' });

    // The state that request created is visible over the other bindings.
    const overHttp = await h.api.get<{ order: { id: string } }>(`/v2/orders/${response.body.order.id}`, { headers: h.auth });
    expect(overHttp.status).toBe(200);

    await drop.stop();
    await h.stop();
  });
});
