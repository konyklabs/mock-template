import { UnitError, json, type Route, type UnitContext } from '@vendor-unit/core';
import { COL, type CatalogObjectEntity, type LocationEntity, type Money, type OrderEntity, type OrderLineItemEntity } from '../entities.js';
import { orderMachine } from '../machine.js';
import { orderTotal, projectOrder, projectOrderEntry } from '../model/order.js';
import { asArray, asRecord, optionalString, requireString, type SquareDeps } from './common.js';

/**
 * Orders surface — the stateful core of the slice.
 *
 * CreateOrder   POST /v2/orders                  https://developer.squareup.com/reference/square/orders-api/create-order
 * RetrieveOrder GET  /v2/orders/{order_id}       https://developer.squareup.com/reference/square/orders-api/retrieve-order
 * UpdateOrder   PUT  /v2/orders/{order_id}       https://developer.squareup.com/reference/square/orders-api/update-order
 * SearchOrders  POST /v2/orders/search           https://developer.squareup.com/reference/square/orders-api/search-orders
 * PayOrder      POST /v2/orders/{order_id}/pay   https://developer.squareup.com/reference/square/orders-api/pay-order
 *
 * SHRINK (prototype): BatchRetrieveOrders, CalculateOrder and CloneOrder are
 * not implemented — they add no new state behaviour over the five above. Taxes,
 * discounts, service charges and fulfillments are not modelled; see model/order.ts.
 */
export function orderRoutes(deps: SquareDeps): Route[] {
  return [
    {
      method: 'POST',
      path: '/v2/orders',
      capability: 'order-lifecycle',
      auth: 'bearer',
      scopes: ['ORDERS_WRITE'],
      operationId: 'CreateOrder',
      summary: 'Create an order. Idempotent on idempotency_key.',
      idempotency: { keyPath: 'idempotency_key', scope: 'orders.create' },
      handler: ({ ctx, json: readJson }) => {
        const body = readJson<Record<string, unknown>>();
        const spec = asRecord(body.order, 'order');
        const locationId = requireString(spec, 'location_id');
        const location = requireLocation(ctx, locationId);

        const state = optionalString(spec, 'state') ?? 'OPEN';
        if (state !== 'OPEN' && state !== 'DRAFT') {
          throw new UnitError('invalid_value', {
            detail: `An order cannot be created in state ${state}. CreateOrder accepts OPEN (default) or DRAFT.`,
            field: 'order.state',
            info: { allowed: ['OPEN', 'DRAFT'] },
          });
        }

        const lineItems = buildLineItems(ctx, deps, spec.line_items, location.currency) as OrderLineItemEntity[];
        const source = spec.source ? asRecord(spec.source, 'order.source') : undefined;

        const order = ctx.store.collection<OrderEntity>(COL.orders).insert(
          {
            id: deps.ids.order(),
            locationId,
            merchantId: location.merchantId,
            referenceId: optionalString(spec, 'reference_id'),
            customerId: optionalString(spec, 'customer_id'),
            ticketName: optionalString(spec, 'ticket_name'),
            sourceName: source ? (typeof source.name === 'string' ? source.name : undefined) : undefined,
            state,
            currency: location.currency,
            lineItems,
            tenders: [],
            metadata: (spec.metadata as Record<string, string> | undefined) ?? undefined,
          },
          { operationId: 'CreateOrder' },
        );
        return json({ order: projectOrder(order) });
      },
    },

    {
      method: 'GET',
      path: '/v2/orders/:order_id',
      capability: 'order-lifecycle',
      auth: 'bearer',
      scopes: ['ORDERS_READ'],
      operationId: 'RetrieveOrder',
      summary: 'Retrieve one order, reflecting every committed mutation.',
      handler: ({ ctx, params }) => json({ order: projectOrder(requireOrder(ctx, params.order_id!)) }),
    },

    {
      method: 'PUT',
      path: '/v2/orders/:order_id',
      capability: 'order-lifecycle',
      auth: 'bearer',
      scopes: ['ORDERS_WRITE'],
      operationId: 'UpdateOrder',
      summary: 'Sparse update under optimistic concurrency.',
      // Square documents that reusing an update key returns the stored response
      // and silently drops the new changes, rather than erroring:
      // https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
      idempotency: { keyPath: 'idempotency_key', scope: 'orders.update', onMismatch: 'replay' },
      handler: ({ ctx, params, json: readJson }) => {
        const body = readJson<Record<string, unknown>>();
        const patch = asRecord(body.order, 'order');
        const orderId = params.order_id!;
        const current = requireOrder(ctx, orderId);

        const version = patch.version;
        if (typeof version !== 'number') {
          throw new UnitError('missing_field', {
            detail: 'Your request must include the order.version property set to the current version of the order.',
            field: 'order.version',
          });
        }

        orderMachine.assertMutable(current.state, `Order ${orderId}`);
        const nextState = optionalString(patch, 'state');
        if (nextState) orderMachine.assertTransition(current.state, nextState, `Order ${orderId}`);

        const fieldsToClear = Array.isArray(body.fields_to_clear) ? (body.fields_to_clear as string[]) : [];
        const location = requireLocation(ctx, current.locationId);
        const incoming = patch.line_items !== undefined ? buildLineItems(ctx, deps, patch.line_items, location.currency, true) : undefined;

        const updated = ctx.store.collection<OrderEntity>(COL.orders).update(
          orderId,
          { expectVersion: version, meta: { operationId: 'UpdateOrder' } },
          (draft) => {
            if (incoming) draft.lineItems = mergeLineItems(draft.lineItems, incoming, location.currency);
            if (patch.reference_id !== undefined) draft.referenceId = optionalString(patch, 'reference_id');
            if (patch.customer_id !== undefined) draft.customerId = optionalString(patch, 'customer_id');
            if (patch.ticket_name !== undefined) draft.ticketName = optionalString(patch, 'ticket_name');
            if (patch.metadata !== undefined) draft.metadata = patch.metadata as Record<string, string>;
            applyFieldsToClear(draft, fieldsToClear);
            if (nextState && nextState !== draft.state) {
              draft.state = nextState;
              if (orderMachine.isTerminal(nextState)) draft.closedAt = ctx.clock.isoMs();
            }
          },
        );
        return json({ order: projectOrder(updated) });
      },
    },

    {
      method: 'POST',
      path: '/v2/orders/search',
      capability: 'order-lifecycle',
      auth: 'bearer',
      scopes: ['ORDERS_READ'],
      operationId: 'SearchOrders',
      summary: 'Filtered, sorted, cursor-paginated order search.',
      handler: ({ ctx, json: readJson }) => {
        const body = readJson<Record<string, unknown>>();
        const locationIds = body.location_ids === undefined ? undefined : (asArray(body.location_ids, 'location_ids') as string[]);
        if (locationIds && locationIds.length > 10) {
          throw new UnitError('invalid_value', { detail: 'Max: 10 location IDs.', field: 'location_ids' });
        }
        const query = body.query ? asRecord(body.query, 'query') : {};
        const filter = query.filter ? asRecord(query.filter, 'query.filter') : {};
        const sort = query.sort ? asRecord(query.sort, 'query.sort') : {};
        const sortField = (optionalString(sort, 'sort_field') ?? 'CREATED_AT').toUpperCase();
        const sortOrder = (optionalString(sort, 'sort_order') ?? 'DESC').toUpperCase();
        if (!['CREATED_AT', 'UPDATED_AT', 'CLOSED_AT'].includes(sortField)) {
          throw new UnitError('invalid_value', { detail: `sort_field must be CREATED_AT, UPDATED_AT or CLOSED_AT.`, field: 'query.sort.sort_field' });
        }
        if (!['ASC', 'DESC'].includes(sortOrder)) {
          throw new UnitError('invalid_value', { detail: 'sort_order must be ASC or DESC.', field: 'query.sort.sort_order' });
        }

        const states = filter.state_filter ? (asArray(asRecord(filter.state_filter, 'query.filter.state_filter').states, 'query.filter.state_filter.states') as string[]) : undefined;
        const dateFilter = filter.date_time_filter ? asRecord(filter.date_time_filter, 'query.filter.date_time_filter') : undefined;
        if (dateFilter) {
          const fields = Object.keys(dateFilter);
          const expected = { created_at: 'CREATED_AT', updated_at: 'UPDATED_AT', closed_at: 'CLOSED_AT' } as Record<string, string>;
          for (const f of fields) {
            // "If you use the DateTimeFilter in a SearchOrders query, you must set
            // the sort_field in OrdersSort to the same field you filter for."
            // https://developer.squareup.com/reference/square/objects/SearchOrdersDateTimeFilter
            if (expected[f] && expected[f] !== sortField) {
              throw new UnitError('invalid_value', {
                detail: `A date_time_filter on ${f} requires sort_field ${expected[f]}.`,
                field: 'query.sort.sort_field',
              });
            }
          }
        }

        const collection = ctx.store.collection<OrderEntity>(COL.orders);
        let orders = collection.all();
        if (locationIds) orders = orders.filter((o) => locationIds.includes(o.locationId));
        if (states) orders = orders.filter((o) => states.includes(o.state));
        if (dateFilter) orders = orders.filter((o) => withinRange(o, sortField, dateFilter));

        const key = sortField === 'CREATED_AT' ? 'createdAt' : sortField === 'UPDATED_AT' ? 'updatedAt' : 'closedAt';
        orders.sort((a, b) => {
          const av = String(a[key] ?? '');
          const bv = String(b[key] ?? '');
          const cmp = av === bv ? a.id.localeCompare(b.id) : av.localeCompare(bv);
          return sortOrder === 'ASC' ? cmp : -cmp;
        });

        // The fingerprint is everything except paging, which is how the cursor
        // enforces Square's "you must use the original query" rule.
        const { cursor: _c, limit: _l, ...fingerprint } = body;
        const page = collection.paginate(orders, {
          limit: typeof body.limit === 'number' ? body.limit : undefined,
          cursor: optionalString(body, 'cursor'),
          fingerprint,
          defaultLimit: 500,
          maxLimit: 1000,
        });

        const returnEntries = body.return_entries === true;
        return json({
          ...(returnEntries ? { order_entries: page.items.map(projectOrderEntry) } : { orders: page.items.map(projectOrder) }),
          ...(page.cursor ? { cursor: page.cursor } : {}),
        });
      },
    },

    {
      method: 'POST',
      path: '/v2/orders/:order_id/pay',
      capability: 'order-lifecycle',
      auth: 'bearer',
      scopes: ['ORDERS_WRITE', 'PAYMENTS_WRITE'],
      operationId: 'PayOrder',
      summary: 'Pay an open order and move it to COMPLETED.',
      idempotency: { keyPath: 'idempotency_key', scope: 'orders.pay', required: true },
      handler: ({ ctx, params, json: readJson }) => {
        const body = readJson<Record<string, unknown>>();
        const orderId = params.order_id!;
        const current = requireOrder(ctx, orderId);
        const paymentIds = body.payment_ids === undefined ? [] : (asArray(body.payment_ids, 'payment_ids') as string[]);

        if (current.state === 'DRAFT') {
          throw new UnitError('invalid_transition', {
            detail: `Order ${orderId} is in state DRAFT and cannot be paid. A DRAFT order cannot be paid or fulfilled.`,
            field: 'state',
          });
        }
        orderMachine.assertTransition(current.state, 'COMPLETED', `Order ${orderId}`);

        const expectVersion = typeof body.order_version === 'number' ? body.order_version : undefined;
        const total = orderTotal(current);
        const now = ctx.clock.isoMs();

        const updated = ctx.store.collection<OrderEntity>(COL.orders).update(
          orderId,
          { expectVersion, meta: { operationId: 'PayOrder' } },
          (draft) => {
            // SHRINK: there is no Payments API in this unit, so payment ids are
            // accepted as opaque references and the tender total is taken from
            // the order rather than from stored payments. Square requires the
            // payment sum to equal the order total, which is trivially true here.
            draft.tenders = (paymentIds.length > 0 ? paymentIds : ['unit-payment']).map((paymentId, i) => ({
              id: deps.ids.tender(),
              locationId: draft.locationId,
              transactionId: draft.id,
              createdAt: now,
              amountMoney: { amount: i === 0 ? total : 0, currency: draft.currency },
              type: 'CARD',
              paymentId,
            }));
            draft.state = 'COMPLETED';
            draft.closedAt = now;
          },
        );
        return json({ order: projectOrder(updated) });
      },
    },
  ];
}

function requireOrder(ctx: UnitContext, id: string): OrderEntity {
  const order = ctx.store.collection<OrderEntity>(COL.orders).get(id);
  if (!order) throw new UnitError('not_found', { detail: `Order ${id} was not found.`, field: 'order_id' });
  return order;
}

function requireLocation(ctx: UnitContext, id: string): LocationEntity {
  const location = ctx.store.collection<LocationEntity>(COL.locations).get(id);
  if (!location) {
    throw new UnitError('invalid_value', {
      detail: `Location ${id} does not exist for this merchant.`,
      field: 'order.location_id',
      info: { known: ctx.store.collection<LocationEntity>(COL.locations).all().map((l) => l.id) },
    });
  }
  return location;
}

function withinRange(order: OrderEntity, sortField: string, dateFilter: Record<string, unknown>): boolean {
  const field = sortField === 'CREATED_AT' ? 'created_at' : sortField === 'UPDATED_AT' ? 'updated_at' : 'closed_at';
  const range = dateFilter[field];
  if (!range || typeof range !== 'object') return true;
  const { start_at: startAt, end_at: endAt } = range as { start_at?: string; end_at?: string };
  const value = sortField === 'CREATED_AT' ? order.createdAt : sortField === 'UPDATED_AT' ? order.updatedAt : order.closedAt;
  if (!value) return false;
  const t = Date.parse(value);
  if (startAt && t < Date.parse(startAt)) return false;
  if (endAt && t >= Date.parse(endAt)) return false;
  return true;
}

type LineItemPatch = Partial<OrderLineItemEntity> & { uid: string };

/**
 * Parse incoming line items. In sparse mode (UpdateOrder) an absent field stays
 * absent so the merge preserves what is already stored — synthesizing a default
 * here would silently zero a price the caller never mentioned.
 */
function buildLineItems(ctx: UnitContext, deps: SquareDeps, raw: unknown, currency: string, sparse = false): LineItemPatch[] {
  if (raw === undefined) return [];
  const items = asArray(raw, 'order.line_items');
  return items.map((entry, index) => {
    const li = asRecord(entry, `order.line_items[${index}]`);
    const uid = optionalString(li, 'uid') ?? deps.ids.lineItemUid();
    const quantity = optionalString(li, 'quantity');
    if (!quantity && !sparse) {
      throw new UnitError('missing_field', { detail: 'quantity is required on every line item.', field: `order.line_items[${index}].quantity` });
    }
    const catalogObjectId = optionalString(li, 'catalog_object_id');
    let basePriceMoney = li.base_price_money ? (asRecord(li.base_price_money, `order.line_items[${index}].base_price_money`) as unknown as Money) : undefined;
    let name = optionalString(li, 'name');
    let variationName = optionalString(li, 'variation_name');

    if (catalogObjectId) {
      const variation = ctx.store.collection<CatalogObjectEntity>(COL.catalog).get(catalogObjectId);
      if (!variation || variation.objectType !== 'ITEM_VARIATION') {
        throw new UnitError('invalid_value', {
          detail: `catalog_object_id ${catalogObjectId} is not an ITEM_VARIATION in this catalog.`,
          field: `order.line_items[${index}].catalog_object_id`,
        });
      }
      // Pricing resolves from the catalog when the caller does not override it —
      // the behaviour that makes seeded catalog data worth having.
      basePriceMoney = basePriceMoney ?? variation.priceMoney;
      variationName = variationName ?? variation.variationName;
      if (!name && variation.itemId) {
        name = ctx.store.collection<CatalogObjectEntity>(COL.catalog).get(variation.itemId)?.itemName;
      }
    }

    if (!basePriceMoney && !sparse) {
      throw new UnitError('invalid_value', {
        detail: 'A line item needs either base_price_money or a catalog_object_id with a fixed price.',
        field: `order.line_items[${index}].base_price_money`,
      });
    }

    if (sparse) {
      const patch: LineItemPatch = { uid };
      if (name !== undefined) patch.name = name;
      if (quantity !== undefined) patch.quantity = quantity;
      if (li.note !== undefined) patch.note = optionalString(li, 'note');
      if (catalogObjectId !== undefined) patch.catalogObjectId = catalogObjectId;
      if (variationName !== undefined) patch.variationName = variationName;
      if (basePriceMoney !== undefined) patch.basePriceMoney = basePriceMoney;
      return patch;
    }

    return {
      uid,
      name,
      quantity: quantity ?? '1',
      note: optionalString(li, 'note'),
      catalogObjectId,
      variationName,
      basePriceMoney: basePriceMoney ?? { amount: 0, currency },
    };
  });
}

/** Sparse merge: a patch with a known uid updates it, an unknown uid appends. */
function mergeLineItems(existing: OrderLineItemEntity[], incoming: LineItemPatch[], currency: string): OrderLineItemEntity[] {
  const byUid = new Map(existing.map((li) => [li.uid, li]));
  for (const patch of incoming) {
    const prior = byUid.get(patch.uid);
    if (prior) {
      byUid.set(patch.uid, { ...prior, ...patch });
      continue;
    }
    if (!patch.quantity || !patch.basePriceMoney) {
      throw new UnitError('missing_field', {
        detail: `Line item ${patch.uid} is new, so it needs a quantity and a price.`,
        field: 'order.line_items',
      });
    }
    byUid.set(patch.uid, { ...patch, quantity: patch.quantity, basePriceMoney: patch.basePriceMoney } as OrderLineItemEntity);
  }
  return [...byUid.values()];
}

/**
 * `fields_to_clear` uses Square's dot/bracket notation, e.g. `discounts`,
 * `line_items[uid]`, `line_items[uid].note`.
 * https://developer.squareup.com/reference/square/orders-api/update-order
 */
function applyFieldsToClear(draft: OrderEntity, paths: string[]): void {
  for (const path of paths) {
    const lineItemMatch = /^line_items\[([^\]]+)\](?:\.(.+))?$/.exec(path);
    if (lineItemMatch) {
      const [, uid, sub] = lineItemMatch;
      if (!sub) {
        draft.lineItems = draft.lineItems.filter((li) => li.uid !== uid);
      } else {
        const li = draft.lineItems.find((x) => x.uid === uid);
        if (li && sub === 'note') delete li.note;
      }
      continue;
    }
    switch (path) {
      case 'line_items':
        draft.lineItems = [];
        break;
      case 'reference_id':
        delete draft.referenceId;
        break;
      case 'customer_id':
        delete draft.customerId;
        break;
      case 'ticket_name':
        delete draft.ticketName;
        break;
      case 'metadata':
        delete draft.metadata;
        break;
      default:
        // Square silently ignores clears it cannot apply (read-only or unknown
        // properties), and still increments the version. Same here.
        break;
    }
  }
}
