import { UnitError, type HandlerArgs } from '@vendor-unit/core';
import type { SquareIds } from '../ids.js';

export interface SquareVendorConfig {
  applicationId: string;
  applicationSecret: string;
  redirectUri: string;
  environment: 'Production' | 'Sandbox';
  apiVersion: string;
  errorSidecar: boolean;
  accessTokenTtlMs: number;
  shortLivedTtlMs: number;
  pkceRefreshTtlMs: number;
  authorizationCodeTtlMs: number;
  defaultScopes: string[];
}

export interface SquareDeps {
  ids: SquareIds;
  config: SquareVendorConfig;
}

/**
 * Square's REST API takes JSON, but the OAuth endpoints are the ones consumers
 * most often reach with a form-encoded client out of habit. Accepting both and
 * normalizing is a JUDGMENT call in the mock's favour: it fails on the thing
 * under test rather than on a content-type mismatch.
 */
export function readBody(args: HandlerArgs): Record<string, unknown> {
  const ct = args.header('content-type') ?? '';
  if (ct.includes('application/x-www-form-urlencoded')) return args.form();
  return args.json<Record<string, unknown>>();
}

export function requireString(body: Record<string, unknown>, field: string): string {
  const v = body[field];
  if (typeof v !== 'string' || v.length === 0) {
    throw new UnitError('missing_field', { detail: `${field} is required.`, field });
  }
  return v;
}

export function optionalString(body: Record<string, unknown>, field: string): string | undefined {
  const v = body[field];
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}

export function asRecord(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new UnitError('invalid_value', { detail: `${field} must be an object.`, field });
  }
  return value as Record<string, unknown>;
}

export function asArray(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new UnitError('invalid_value', { detail: `${field} must be an array.`, field });
  }
  return value;
}
