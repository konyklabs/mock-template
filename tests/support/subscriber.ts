import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';

/**
 * A webhook subscriber, the way a consumer's own endpoint behaves: it receives
 * the POST, records the raw bytes (needed to verify the signature) and can be
 * told to fail so the unit's retry behaviour is exercised for real rather than
 * simulated.
 */
export interface ReceivedWebhook {
  headers: Record<string, string>;
  rawBody: Buffer;
  receivedAt: number;
}

export interface SubscriberHandle {
  port: number;
  received: ReceivedWebhook[];
  /** Status to answer with; a function form can fail the first N deliveries. */
  respondWith: number | ((index: number) => number);
  close(): Promise<void>;
}

export async function startSubscriber(): Promise<SubscriberHandle> {
  const received: ReceivedWebhook[] = [];
  const handle: Partial<SubscriberHandle> & { received: ReceivedWebhook[]; respondWith: SubscriberHandle['respondWith'] } = {
    received,
    respondWith: 200,
  };

  const server: Server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      const headers: Record<string, string> = {};
      for (const [k, v] of Object.entries(req.headers)) {
        if (v !== undefined) headers[k.toLowerCase()] = Array.isArray(v) ? v.join(', ') : v;
      }
      const index = received.length;
      received.push({ headers, rawBody: Buffer.concat(chunks), receivedAt: Date.now() });
      const status = typeof handle.respondWith === 'function' ? handle.respondWith(index) : handle.respondWith;
      res.writeHead(status, { 'content-type': 'text/plain' });
      res.end(status >= 200 && status < 300 ? 'ok' : 'nope');
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '0.0.0.0', () => resolve()));
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;

  return Object.assign(handle, {
    port,
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  }) as SubscriberHandle;
}
