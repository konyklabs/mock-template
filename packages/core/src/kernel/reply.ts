import type { ReplyInit, UnitResponse } from './types.js';

const encoder = new TextEncoder();

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): ReplyInit {
  return { status, json: body, headers };
}

export function text(body: string, status = 200, headers: Record<string, string> = {}): ReplyInit {
  return { status, text: body, headers };
}

export function redirect(location: string, status = 302): ReplyInit {
  return { status, headers: { location }, text: '' };
}

export function noContent(): ReplyInit {
  return { status: 204, text: '' };
}

export function normalize(init: ReplyInit | UnitResponse): UnitResponse {
  if ('body' in init && init.body instanceof Uint8Array) return init as UnitResponse;
  const r = init as ReplyInit;
  const headers: Record<string, string> = { ...(r.headers ?? {}) };
  let body: Uint8Array;
  if (r.raw) {
    body = r.raw;
  } else if (r.text !== undefined) {
    body = encoder.encode(r.text);
    if (r.text.length > 0 && !headers['content-type']) headers['content-type'] = 'text/plain; charset=utf-8';
  } else {
    body = encoder.encode(JSON.stringify(r.json ?? {}));
    headers['content-type'] = headers['content-type'] ?? 'application/json';
  }
  return { status: r.status ?? 200, headers, body };
}

export function decodeBody(res: UnitResponse): string {
  return new TextDecoder().decode(res.body);
}

export function parseBody<T = unknown>(res: UnitResponse): T {
  const t = decodeBody(res);
  return (t ? JSON.parse(t) : undefined) as T;
}
