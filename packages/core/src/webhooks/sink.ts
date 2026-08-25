/**
 * Outbound transport for webhook deliveries.
 *
 * The dispatcher never calls `fetch` itself. Real vendors push over HTTP, but
 * others drop a file on an SFTP share or enqueue a message, and a test wants
 * neither — so delivery is an interface with three implementations here.
 */
export interface SinkRequest {
  url: string;
  headers: Record<string, string>;
  body: Uint8Array;
  timeoutMs: number;
}

export interface SinkResult {
  status: number;
  bodySnippet?: string;
  error?: string;
  /** True when nothing came back in time — the vendor's `http_timeout` case. */
  timedOut?: boolean;
}

export interface DeliverySink {
  readonly kind: string;
  send(req: SinkRequest): Promise<SinkResult>;
}

export class HttpSink implements DeliverySink {
  readonly kind = 'http';

  async send(req: SinkRequest): Promise<SinkResult> {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), req.timeoutMs);
    try {
      const res = await fetch(req.url, {
        method: 'POST',
        headers: req.headers,
        body: req.body,
        signal: ac.signal,
      });
      const text = await res.text().catch(() => '');
      return { status: res.status, bodySnippet: text.slice(0, 200) };
    } catch (err) {
      const timedOut = ac.signal.aborted;
      return { status: 0, error: err instanceof Error ? err.message : String(err), timedOut };
    } finally {
      clearTimeout(timer);
    }
  }
}

/** Captures deliveries in memory. Used by the conformance suite and unit tests. */
export class MemorySink implements DeliverySink {
  readonly kind = 'memory';
  readonly received: SinkRequest[] = [];
  /** Status to return; a function form lets a test fail the first N attempts. */
  respondWith: number | ((req: SinkRequest, callIndex: number) => number) = 200;

  async send(req: SinkRequest): Promise<SinkResult> {
    const index = this.received.length;
    this.received.push({ ...req, body: req.body.slice() });
    const status = typeof this.respondWith === 'function' ? this.respondWith(req, index) : this.respondWith;
    if (status === 0) return { status: 0, error: 'simulated transport failure', timedOut: true };
    return { status };
  }
}

/** Writes each delivery as a JSON document. Proof that the sink is not HTTP-bound. */
export class FileSink implements DeliverySink {
  readonly kind = 'file';
  private seq = 0;

  constructor(private readonly dir: string) {}

  async send(req: SinkRequest): Promise<SinkResult> {
    const { mkdir, writeFile } = await import('node:fs/promises');
    const { join } = await import('node:path');
    await mkdir(this.dir, { recursive: true });
    const name = `delivery-${String(++this.seq).padStart(5, '0')}.json`;
    await writeFile(
      join(this.dir, name),
      JSON.stringify({ url: req.url, headers: req.headers, body: Buffer.from(req.body).toString('utf8') }, null, 2),
      'utf8',
    );
    return { status: 200 };
  }
}
