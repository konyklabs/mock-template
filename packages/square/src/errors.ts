import { UnitError } from '@vendor-unit/core';
import type { ErrorShaper, ShapedError, UnitContext, UnitErrorKind, UnitRequest } from '@vendor-unit/core';

/**
 * Square error shaping — the entire fork-side error story, in one table.
 *
 * The core raises twenty vendor-neutral error kinds; this maps each to a Square
 * `{category, code}` pair and an HTTP status. Envelope shape and the four Error
 * fields (`category`, `code`, `detail`, `field`) are documented:
 *   https://developer.squareup.com/docs/build-basics/handling-errors
 *   https://developer.squareup.com/reference/square/objects/Error
 *
 * Statuses marked JUDGMENT below are ones Square does not publish. Square
 * documents statuses only for the authentication codes and 429
 * (https://developer.squareup.com/docs/build-basics/handling-errors) plus a
 * verbatim 400 example for IDEMPOTENCY_KEY_REUSED
 * (https://developer.squareup.com/docs/build-basics/general-considerations/using-rest-api);
 * everything else here is the conventional REST reading of the code name, and
 * is labelled as such rather than presented as fidelity.
 */
export type SquareCategory =
  | 'API_ERROR'
  | 'AUTHENTICATION_ERROR'
  | 'INVALID_REQUEST_ERROR'
  | 'RATE_LIMIT_ERROR';

interface Mapping {
  status: number;
  category: SquareCategory;
  code: string;
  /** Where the status comes from; surfaced in README and /__unit/info. */
  provenance: 'documented' | 'judgment';
  detail: string;
}

export const SQUARE_ERROR_TABLE: Record<UnitErrorKind, Mapping> = {
  bad_request: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'BAD_REQUEST', provenance: 'judgment', detail: 'A general error occurred with the request.' },
  invalid_json: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'EXPECTED_JSON_BODY', provenance: 'judgment', detail: 'The request body is not valid JSON.' },
  missing_field: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'MISSING_REQUIRED_PARAMETER', provenance: 'judgment', detail: 'A required parameter is missing.' },
  invalid_value: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'INVALID_VALUE', provenance: 'judgment', detail: 'The provided value is invalid.' },
  not_found: { status: 404, category: 'INVALID_REQUEST_ERROR', code: 'NOT_FOUND', provenance: 'judgment', detail: 'Not Found - a general error occurred.' },
  method_not_allowed: { status: 405, category: 'INVALID_REQUEST_ERROR', code: 'METHOD_NOT_ALLOWED', provenance: 'judgment', detail: 'The HTTP method is not allowed on this resource.' },
  unauthorized: { status: 401, category: 'AUTHENTICATION_ERROR', code: 'UNAUTHORIZED', provenance: 'documented', detail: 'This request could not be authorized.' },
  token_expired: { status: 401, category: 'AUTHENTICATION_ERROR', code: 'ACCESS_TOKEN_EXPIRED', provenance: 'documented', detail: 'The provided access token has expired.' },
  token_revoked: { status: 401, category: 'AUTHENTICATION_ERROR', code: 'ACCESS_TOKEN_REVOKED', provenance: 'documented', detail: 'The provided access token has been revoked.' },
  forbidden_scope: { status: 403, category: 'AUTHENTICATION_ERROR', code: 'INSUFFICIENT_SCOPES', provenance: 'documented', detail: 'The provided access token does not have permission to execute the requested action.' },
  // NOT_IMPLEMENTED is a real Square generic error code (api.json
  // info["x-square-generic-error-codes"]); using it keeps a disabled capability
  // inside the vendor's own vocabulary instead of inventing one. The 501 status
  // and the `unit_error` sidecar are this template's addition — see below.
  capability_disabled: { status: 501, category: 'API_ERROR', code: 'NOT_IMPLEMENTED', provenance: 'judgment', detail: 'This capability is not enabled on this unit.' },
  version_conflict: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'VERSION_MISMATCH', provenance: 'judgment', detail: 'The supplied version does not match the current version.' },
  idempotency_conflict: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'IDEMPOTENCY_KEY_REUSED', provenance: 'documented', detail: 'The idempotency key can only be retried with the same request data.' },
  invalid_cursor: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'INVALID_CURSOR', provenance: 'judgment', detail: 'The provided cursor is not valid.' },
  invalid_transition: { status: 400, category: 'INVALID_REQUEST_ERROR', code: 'BAD_REQUEST', provenance: 'judgment', detail: 'The order cannot be updated in its current state.' },
  conflict: { status: 409, category: 'INVALID_REQUEST_ERROR', code: 'CONFLICT', provenance: 'judgment', detail: 'Conflict - a general error occurred.' },
  rate_limited: { status: 429, category: 'RATE_LIMIT_ERROR', code: 'RATE_LIMITED', provenance: 'documented', detail: 'Rate Limited - a general error occurred.' },
  timeout: { status: 504, category: 'API_ERROR', code: 'GATEWAY_TIMEOUT', provenance: 'judgment', detail: 'Gateway Timeout - a general error occurred.' },
  unavailable: { status: 503, category: 'API_ERROR', code: 'SERVICE_UNAVAILABLE', provenance: 'judgment', detail: 'Service Unavailable - a general error occurred.' },
  internal: { status: 500, category: 'API_ERROR', code: 'INTERNAL_SERVER_ERROR', provenance: 'judgment', detail: 'A general server error occurred.' },
};

export interface SquareErrorBody {
  errors: Array<{ category: string; code: string; detail?: string; field?: string }>;
  /**
   * Deliberate, namespaced deviation from Square's wire format. A consumer that
   * only reads `errors` never sees it; a consumer debugging a mock gets the
   * machine-readable reason without parsing prose. Suppress with
   * `"errorSidecar": false` in a profile's `vendor` block.
   */
  unit_error?: Record<string, unknown>;
}

export class SquareErrorShaper implements ErrorShaper {
  constructor(private readonly opts: { sidecar: boolean } = { sidecar: true }) {}

  shape(err: UnitError, _ctx: UnitContext): ShapedError {
    const m = SQUARE_ERROR_TABLE[err.kind] ?? SQUARE_ERROR_TABLE.internal;
    const body: SquareErrorBody = {
      errors: [
        {
          category: m.category,
          code: m.code,
          ...(err.detail || m.detail ? { detail: err.detail ?? m.detail } : {}),
          ...(err.field ? { field: err.field } : {}),
        },
      ],
    };
    if (this.opts.sidecar) {
      body.unit_error = { kind: err.kind, statusProvenance: m.provenance, ...(err.info ?? {}) };
    }
    const headers: Record<string, string> = {};
    if (err.kind === 'rate_limited') {
      const retryAfter = Number((err.info ?? {}).retryAfterSeconds ?? 1);
      // Square does not document a Retry-After header; sending one is a
      // convenience for consumers testing their own backoff. JUDGMENT.
      headers['retry-after'] = String(retryAfter);
    }
    if (err.kind === 'capability_disabled') {
      headers['x-unit-capability'] = String((err.info ?? {}).capability ?? '');
    }
    return { status: m.status, body, headers };
  }

  notFound(req: UnitRequest, ctx: UnitContext): ShapedError {
    return this.shape(
      new UnitError('not_found', {
        detail: `${req.method} ${req.path} is not a route on this Square unit. GET /__unit/routes lists the surface this profile serves.`,
        info: { path: req.path, method: req.method, profile: ctx.config.profile },
      }),
      ctx,
    );
  }
}
