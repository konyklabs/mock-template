import type { Unit, RequestInit } from '../kernel/unit.js';
import { makeRequest } from '../kernel/unit.js';
import { parseBody, decodeBody } from '../kernel/reply.js';
import type { UnitResponse } from '../kernel/types.js';

/**
 * In-process binding: call the unit directly, with no socket.
 *
 * Used by the conformance suite (it must run against a unit that was never
 * given a port) and by fork unit tests, which get a few hundred assertions per
 * second instead of a few dozen.
 */
export interface InProcessResponse<T = unknown> {
  status: number;
  headers: Record<string, string>;
  body: T;
  text: string;
  raw: UnitResponse;
}

export interface InProcessClient {
  call<T = unknown>(init: RequestInit): Promise<InProcessResponse<T>>;
  get<T = unknown>(path: string, init?: Partial<RequestInit>): Promise<InProcessResponse<T>>;
  post<T = unknown>(path: string, body?: unknown, init?: Partial<RequestInit>): Promise<InProcessResponse<T>>;
  put<T = unknown>(path: string, body?: unknown, init?: Partial<RequestInit>): Promise<InProcessResponse<T>>;
  del<T = unknown>(path: string, init?: Partial<RequestInit>): Promise<InProcessResponse<T>>;
}

export function inProcess(unit: Unit): InProcessClient {
  const call = async <T>(init: RequestInit): Promise<InProcessResponse<T>> => {
    const raw = await unit.handle(makeRequest({ ...init, transport: init.transport ?? 'inprocess' }));
    const text = decodeBody(raw);
    let body: T;
    try {
      body = (text ? parseBody<T>(raw) : (undefined as T)) as T;
    } catch {
      body = text as unknown as T;
    }
    return { status: raw.status, headers: { ...raw.headers }, body, text, raw };
  };
  return {
    call,
    get: (path, init) => call({ method: 'GET', path, ...init }),
    post: (path, body, init) => call({ method: 'POST', path, body, ...init }),
    put: (path, body, init) => call({ method: 'PUT', path, body, ...init }),
    del: (path, init) => call({ method: 'DELETE', path, ...init }),
  };
}
