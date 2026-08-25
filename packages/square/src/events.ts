import type { EventMapper, JournalEntry, MappedEvent, UnitContext } from '@vendor-unit/core';
import { COL, type OrderEntity } from './entities.js';

/**
 * Journal entry -> Square webhook event.
 *
 * Note what is NOT here: no route handler calls "emit". Events are derived from
 * committed state mutations, so an event cannot exist for a create that was
 * rejected, and a mutation cannot silently skip its event.
 *
 * Envelope: `{merchant_id, type, event_id, created_at, data{type, id, object}}`
 * https://developer.squareup.com/docs/webhooks/build-with-webhooks
 *
 * Order events carry a SUMMARY, not the order — `data.object` holds one key
 * named after `data.type`:
 *   order.created -> object.order_created {created_at, location_id, order_id, state, version}
 *   order.updated -> object.order_updated {created_at, location_id, order_id, state, updated_at, version}
 * https://developer.squareup.com/reference/square/webhooks/order.created
 * https://developer.squareup.com/reference/square/webhooks/order.updated
 */
export const SQUARE_ORDER_EVENT_TYPES = ['order.created', 'order.updated'] as const;

export class SquareEventMapper implements EventMapper {
  map(entry: JournalEntry, ctx: UnitContext): MappedEvent[] {
    if (entry.collection !== COL.orders) return [];
    const order = ctx.store.collection<OrderEntity>(COL.orders).get(entry.id);
    if (!order) return [];

    if (entry.op === 'insert') {
      return [
        buildOrderEvent(order, 'order.created', 'order_created', {
          created_at: order.createdAt,
          location_id: order.locationId,
          order_id: order.id,
          state: order.state,
          version: order.version,
        }),
      ];
    }
    if (entry.op === 'update') {
      return [
        buildOrderEvent(order, 'order.updated', 'order_updated', {
          created_at: order.createdAt,
          location_id: order.locationId,
          order_id: order.id,
          state: order.state,
          updated_at: order.updatedAt,
          version: order.version,
        }),
      ];
    }
    return [];
  }
}

function buildOrderEvent(order: OrderEntity, type: string, dataType: string, object: Record<string, unknown>): MappedEvent {
  return {
    type,
    entityId: order.id,
    build: ({ eventId, createdAt }) => ({
      merchant_id: order.merchantId,
      type,
      event_id: eventId,
      created_at: createdAt,
      data: { type: dataType, id: order.id, object: { [dataType]: object } },
    }),
  };
}
