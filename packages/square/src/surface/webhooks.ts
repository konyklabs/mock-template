import { SUBSCRIPTION_COLLECTION, UnitError, json, type Route, type SubscriptionEntity } from '@vendor-unit/core';
import { SQUARE_ORDER_EVENT_TYPES } from '../events.js';
import { asArray, asRecord, optionalString, type SquareDeps } from './common.js';

/**
 * Webhook Subscriptions surface.
 *
 * https://developer.squareup.com/reference/square/webhook-subscriptions-api
 *   POST   /v2/webhooks/subscriptions
 *   GET    /v2/webhooks/subscriptions
 *   GET    /v2/webhooks/subscriptions/{subscription_id}
 *   DELETE /v2/webhooks/subscriptions/{subscription_id}
 *   POST   /v2/webhooks/subscriptions/{subscription_id}/test
 *   GET    /v2/webhooks/event-types
 *
 * The subscription record lives in the core-owned `subscriptions` collection,
 * so registering a subscriber through Square's own API and registering one
 * through the control plane converge on the same dispatcher state. That is why
 * these handlers are pure shape translation.
 *
 * SHRINK (prototype): UpdateWebhookSubscription (PUT), the enabled/disabled
 * toggle endpoint and signature-key rotation are not implemented; they add no
 * delivery behaviour.
 */
export function webhookRoutes(deps: SquareDeps): Route[] {
  return [
    {
      method: 'GET',
      path: '/v2/webhooks/event-types',
      capability: 'webhooks',
      auth: 'bearer',
      operationId: 'ListWebhookEventTypes',
      summary: 'Event types this unit can emit.',
      handler: ({ ctx }) =>
        json({
          event_types: [...SQUARE_ORDER_EVENT_TYPES],
          metadata: SQUARE_ORDER_EVENT_TYPES.map((t) => ({
            event_type: t,
            api_version_introduced: ctx.vendor.apiVersion,
            release_status: 'PUBLIC',
          })),
        }),
    },

    {
      method: 'POST',
      path: '/v2/webhooks/subscriptions',
      capability: 'webhooks',
      auth: 'bearer',
      operationId: 'CreateWebhookSubscription',
      summary: 'Register a subscriber and receive its signature key.',
      idempotency: { keyPath: 'idempotency_key', scope: 'webhooks.create' },
      handler: ({ ctx, json: readJson }) => {
        const body = readJson<Record<string, unknown>>();
        const spec = asRecord(body.subscription, 'subscription');
        const notificationUrl = optionalString(spec, 'notification_url');
        if (!notificationUrl) {
          throw new UnitError('missing_field', { detail: 'notification_url is required.', field: 'subscription.notification_url' });
        }
        const eventTypes = spec.event_types ? (asArray(spec.event_types, 'subscription.event_types') as string[]) : [];
        if (eventTypes.length === 0) {
          throw new UnitError('missing_field', { detail: 'event_types is required.', field: 'subscription.event_types' });
        }
        const entity = ctx.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION).insert(
          {
            id: deps.ids.subscription(),
            name: optionalString(spec, 'name') ?? 'Subscription',
            notificationUrl,
            eventTypes,
            signatureKey: deps.ids.signatureKey(),
            enabled: spec.enabled === undefined ? true : spec.enabled === true,
            apiVersion: optionalString(spec, 'api_version') ?? ctx.vendor.apiVersion,
          } as SubscriptionEntity,
          { operationId: 'CreateWebhookSubscription' },
        );
        return json({ subscription: project(entity) });
      },
    },

    {
      method: 'GET',
      path: '/v2/webhooks/subscriptions',
      capability: 'webhooks',
      auth: 'bearer',
      operationId: 'ListWebhookSubscriptions',
      summary: 'List subscribers.',
      handler: ({ ctx }) => json({ subscriptions: ctx.webhooks.subscriptions().map(project) }),
    },

    {
      method: 'GET',
      path: '/v2/webhooks/subscriptions/:subscription_id',
      capability: 'webhooks',
      auth: 'bearer',
      operationId: 'RetrieveWebhookSubscription',
      summary: 'Retrieve one subscriber.',
      handler: ({ ctx, params }) => json({ subscription: project(requireSubscription(ctx, params.subscription_id!)) }),
    },

    {
      method: 'DELETE',
      path: '/v2/webhooks/subscriptions/:subscription_id',
      capability: 'webhooks',
      auth: 'bearer',
      operationId: 'DeleteWebhookSubscription',
      summary: 'Remove a subscriber.',
      handler: ({ ctx, params }) => {
        requireSubscription(ctx, params.subscription_id!);
        ctx.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION).delete(params.subscription_id!, { operationId: 'DeleteWebhookSubscription' });
        return json({});
      },
    },

    {
      method: 'POST',
      path: '/v2/webhooks/subscriptions/:subscription_id/test',
      capability: 'webhooks',
      auth: 'bearer',
      operationId: 'TestWebhookSubscription',
      summary: 'Send a signed test event and report the subscriber status code.',
      handler: async ({ ctx, params, json: readJson }) => {
        const subscription = requireSubscription(ctx, params.subscription_id!);
        const body = readJson<Record<string, unknown>>();
        const eventType = optionalString(body, 'event_type') ?? subscription.eventTypes[0] ?? 'order.created';
        const before = ctx.webhooks.deliveries().length;
        const eventId = `evt_test_${before + 1}`;
        ctx.webhooks.enqueue(
          {
            type: eventType,
            eventId,
            entityId: subscription.id,
            createdAt: ctx.clock.isoMs(),
            body: {
              merchant_id: 'TEST_MERCHANT',
              type: eventType,
              event_id: eventId,
              created_at: ctx.clock.isoMs(),
              data: { type: 'test', id: subscription.id, object: { test: true } },
            },
          },
          ctx,
        );
        await ctx.webhooks.drain();
        const attempt = ctx.webhooks.deliveries().find((d) => d.eventId === eventId);
        return json({
          subscription_test_result: {
            id: eventId,
            status_code: attempt?.responseStatus ?? 0,
            payload: attempt?.bodyPreview ?? '',
            created_at: ctx.clock.isoMs(),
            updated_at: ctx.clock.isoMs(),
          },
        });
      },
    },
  ];
}

function requireSubscription(ctx: Parameters<NonNullable<Route['handler']>>[0]['ctx'], id: string): SubscriptionEntity {
  const found = ctx.store.collection<SubscriptionEntity>(SUBSCRIPTION_COLLECTION).get(id);
  if (!found) throw new UnitError('not_found', { detail: `Webhook subscription ${id} was not found.`, field: 'subscription_id' });
  return found;
}

function project(s: SubscriptionEntity): Record<string, unknown> {
  return {
    id: s.id,
    name: s.name,
    enabled: s.enabled,
    event_types: s.eventTypes,
    notification_url: s.notificationUrl,
    api_version: s.apiVersion,
    signature_key: s.signatureKey,
    created_at: s.createdAt,
    updated_at: s.updatedAt,
  };
}
