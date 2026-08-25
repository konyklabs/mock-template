import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { randomUUID } from 'node:crypto';
import type { Unit } from '../kernel/unit.js';
import type { UnitRequest } from '../kernel/types.js';

/**
 * HTTP binding.
 *
 * One of three bindings over the same `Unit.handle`. Nothing in the kernel,
 * the state engine, the chaos engine or the dispatcher imports this file —
 * which is the mechanical form of "the core does not assume HTTP".
 */
export interface HttpServerHandle {
  readonly port: number;
  readonly url: string;
  close(): Promise<void>;
}

export async function serveHttp(unit: Unit, opts: { port?: number; host?: string } = {}): Promise<HttpServerHandle> {
  const server: Server = createServer((req, res) => {
    void handleNodeRequest(unit, req, res);
  });
  const host = opts.host ?? '0.0.0.0';
  const port = opts.port ?? 0;
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => resolve());
  });
  const address = server.address();
  const boundPort = typeof address === 'object' && address ? address.port : port;
  return {
    port: boundPort,
    url: `http://127.0.0.1:${boundPort}`,
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}

async function handleNodeRequest(unit: Unit, req: IncomingMessage, res: ServerResponse): Promise<void> {
  try {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    const rawBody = new Uint8Array(Buffer.concat(chunks));
    const url = new URL(req.url ?? '/', 'http://unit.local');
    const headers: Record<string, string> = {};
    for (const [k, v] of Object.entries(req.headers)) {
      if (v === undefined) continue;
      headers[k.toLowerCase()] = Array.isArray(v) ? v.join(', ') : v;
    }
    const query: Record<string, string> = {};
    for (const [k, v] of url.searchParams) query[k] = v;

    const unitReq: UnitRequest = {
      id: headers['x-unit-request-id'] ?? randomUUID(),
      method: (req.method ?? 'GET').toUpperCase(),
      path: url.pathname,
      query,
      headers,
      rawBody,
      transport: 'http',
      receivedAt: new Date().toISOString(),
    };

    const out = await unit.handle(unitReq);
    res.writeHead(out.status, out.headers);
    res.end(Buffer.from(out.body));
  } catch (err) {
    res.writeHead(500, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ errors: [{ category: 'API_ERROR', code: 'INTERNAL_SERVER_ERROR', detail: String(err) }] }));
  }
}
