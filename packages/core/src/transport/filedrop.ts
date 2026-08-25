import { randomUUID } from 'node:crypto';
import { mkdir, readdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { Unit } from '../kernel/unit.js';
import type { UnitRequest } from '../kernel/types.js';

/**
 * File-drop binding.
 *
 * Plenty of real integrations are not HTTP servers: a partner drops a batch
 * file on a share and collects a response file. This binding exists so the
 * claim that the kernel is transport-neutral is demonstrated rather than
 * asserted — it feeds the same `Unit.handle` from JSON documents on disk.
 *
 * Layout: `<dir>/in/<name>.request.json` is consumed and
 * `<dir>/out/<name>.response.json` is written.
 *
 * Request document: `{ "method": "...", "path": "...", "query": {}, "headers": {}, "body": <json> }`
 */
export interface FileDropDocument {
  method: string;
  path: string;
  query?: Record<string, string>;
  headers?: Record<string, string>;
  body?: unknown;
  rawBody?: string;
}

export interface FileDropHandle {
  readonly inDir: string;
  readonly outDir: string;
  /** Process every request document currently waiting; returns how many. */
  poll(): Promise<number>;
  /** Poll on an interval until `stop()`. */
  start(intervalMs?: number): void;
  stop(): Promise<void>;
}

export async function serveFileDrop(unit: Unit, dir: string): Promise<FileDropHandle> {
  const inDir = join(dir, 'in');
  const outDir = join(dir, 'out');
  const doneDir = join(dir, 'processed');
  await mkdir(inDir, { recursive: true });
  await mkdir(outDir, { recursive: true });
  await mkdir(doneDir, { recursive: true });

  let timer: ReturnType<typeof setInterval> | undefined;

  const poll = async (): Promise<number> => {
    const files = (await readdir(inDir)).filter((f) => f.endsWith('.request.json')).sort();
    let handled = 0;
    for (const file of files) {
      const full = join(inDir, file);
      const doc = JSON.parse(await readFile(full, 'utf8')) as FileDropDocument;
      const headers: Record<string, string> = {};
      for (const [k, v] of Object.entries(doc.headers ?? {})) headers[k.toLowerCase()] = v;
      const rawBody =
        doc.rawBody !== undefined
          ? new TextEncoder().encode(doc.rawBody)
          : doc.body !== undefined
            ? new TextEncoder().encode(JSON.stringify(doc.body))
            : new Uint8Array(0);
      if (doc.body !== undefined && !headers['content-type']) headers['content-type'] = 'application/json';

      const req: UnitRequest = {
        id: randomUUID(),
        method: doc.method.toUpperCase(),
        path: doc.path,
        query: doc.query ?? {},
        headers,
        rawBody,
        transport: 'filedrop',
        receivedAt: new Date().toISOString(),
      };
      const res = await unit.handle(req);
      const base = file.replace(/\.request\.json$/, '');
      const text = new TextDecoder().decode(res.body);
      let parsed: unknown;
      try {
        parsed = text ? JSON.parse(text) : null;
      } catch {
        parsed = text;
      }
      await writeFile(
        join(outDir, `${base}.response.json`),
        `${JSON.stringify({ status: res.status, headers: res.headers, body: parsed }, null, 2)}\n`,
        'utf8',
      );
      await rename(full, join(doneDir, file));
      handled++;
    }
    return handled;
  };

  return {
    inDir,
    outDir,
    poll,
    start(intervalMs = 200) {
      timer = setInterval(() => void poll(), intervalMs);
      if (typeof timer === 'object' && timer && 'unref' in timer) (timer as { unref(): void }).unref();
    },
    async stop() {
      if (timer) clearInterval(timer);
    },
  };
}
