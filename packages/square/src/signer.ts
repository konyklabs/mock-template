import { createHmac } from 'node:crypto';
import type { PreparedEvent, WebhookSigner } from '@vendor-unit/core';

/**
 * Square webhook signature.
 *
 * `x-square-hmacsha256-signature` = base64( HMAC-SHA256( signature_key,
 * notification_url + raw_body ) ) — notification URL first, raw body second, no
 * separator, UTF-8.
 *
 * The header name, the algorithm and the three inputs are documented at
 * https://developer.squareup.com/docs/webhooks/step3validate. The docs do not
 * state the concatenation ORDER; Square's own SDKs do, and they are the
 * authority a consumer's verification code will actually be using:
 *   payload = notification_url + request_body
 *   https://github.com/square/square-python-sdk/blob/master/src/square/utils/webhooks_helper.py
 *   https://github.com/square/square-nodejs-sdk/blob/master/src/wrapper/WebhooksHelper.ts
 *
 * `square-environment` is one of the documented delivery headers
 * (https://developer.squareup.com/docs/webhooks/build-with-webhooks); the retry
 * headers are added by the core dispatcher because retrying is its job.
 */
export class SquareWebhookSigner implements WebhookSigner {
  /** HMAC over notification URL + body, keyed by the subscription secret. */
  readonly properties = { urlBound: true, bodyBound: true, secretBound: true };

  constructor(private readonly environment: 'Production' | 'Sandbox' = 'Sandbox') {}

  describe(): Record<string, string> {
    return {
      header: 'x-square-hmacsha256-signature',
      algorithm: 'HMAC-SHA256, base64',
      payload: 'notification_url + raw_body (no separator, UTF-8)',
      reference: 'https://developer.squareup.com/docs/webhooks/step3validate',
    };
  }

  sign(input: { notificationUrl: string; rawBody: Uint8Array; secret: string; attempt: number; event: PreparedEvent }): Record<string, string> {
    return {
      'x-square-hmacsha256-signature': squareSignature(input.secret, input.notificationUrl, input.rawBody),
      'square-environment': this.environment,
    };
  }
}

/** Exported so tests and the README example verify against the same function. */
export function squareSignature(signatureKey: string, notificationUrl: string, rawBody: Uint8Array | string): string {
  const bodyBytes = typeof rawBody === 'string' ? Buffer.from(rawBody, 'utf8') : Buffer.from(rawBody);
  const payload = Buffer.concat([Buffer.from(notificationUrl, 'utf8'), bodyBytes]);
  return createHmac('sha256', signatureKey).update(payload).digest('base64');
}
