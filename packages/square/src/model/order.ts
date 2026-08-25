import { compact } from '@vendor-unit/core';
import type { Money, OrderEntity, OrderLineItemEntity, TenderEntity } from '../entities.js';

/**
 * Order projections: stored entity -> Square wire JSON.
 *
 * Field names and the read-only money roll-ups follow
 * https://developer.squareup.com/reference/square/objects/Order and the
 * CreateOrder response example on
 * https://developer.squareup.com/reference/square/orders-api/create-order.
 *
 * SHRINK (prototype): taxes, discounts, service charges, fulfillments, returns
 * and refunds are not modelled. The corresponding roll-up fields are emitted as
 * zero money so a consumer deserializing the full Order shape still works, and
 * `net_amounts` is therefore always equal to `total_money`.
 */

export function money(amount: number, currency: string): Money {
  return { amount, currency };
}

export function lineItemTotal(li: OrderLineItemEntity): number {
  const qty = Number.parseFloat(li.quantity);
  if (!Number.isFinite(qty)) return 0;
  return Math.round(li.basePriceMoney.amount * qty);
}

export function orderTotal(order: OrderEntity): number {
  return order.lineItems.reduce((sum, li) => sum + lineItemTotal(li), 0);
}

export function tenderedTotal(order: OrderEntity): number {
  return order.tenders.reduce((sum, t) => sum + t.amountMoney.amount, 0);
}

function projectLineItem(li: OrderLineItemEntity, currency: string): Record<string, unknown> {
  const total = lineItemTotal(li);
  return compact({
    uid: li.uid,
    catalog_object_id: li.catalogObjectId,
    variation_name: li.variationName,
    name: li.name,
    quantity: li.quantity,
    note: li.note,
    base_price_money: li.basePriceMoney,
    variation_total_price_money: money(total, currency),
    gross_sales_money: money(total, currency),
    total_tax_money: money(0, currency),
    total_discount_money: money(0, currency),
    total_money: money(total, currency),
    total_service_charge_money: money(0, currency),
  });
}

function projectTender(t: TenderEntity): Record<string, unknown> {
  return {
    id: t.id,
    location_id: t.locationId,
    transaction_id: t.transactionId,
    created_at: t.createdAt,
    amount_money: t.amountMoney,
    type: t.type,
    payment_id: t.paymentId,
  };
}

export function projectOrder(order: OrderEntity): Record<string, unknown> {
  const currency = order.currency;
  const total = orderTotal(order);
  const zero = money(0, currency);
  const totalMoney = money(total, currency);
  return compact({
    id: order.id,
    location_id: order.locationId,
    reference_id: order.referenceId,
    customer_id: order.customerId,
    ticket_name: order.ticketName,
    source: order.sourceName ? { name: order.sourceName } : undefined,
    line_items: order.lineItems.length > 0 ? order.lineItems.map((li) => projectLineItem(li, currency)) : undefined,
    metadata: order.metadata,
    tenders: order.tenders.length > 0 ? order.tenders.map(projectTender) : undefined,
    created_at: order.createdAt,
    updated_at: order.updatedAt,
    closed_at: order.closedAt,
    state: order.state,
    version: order.version,
    total_money: totalMoney,
    total_tax_money: zero,
    total_discount_money: zero,
    total_tip_money: zero,
    total_service_charge_money: zero,
    net_amounts: {
      total_money: totalMoney,
      tax_money: zero,
      discount_money: zero,
      tip_money: zero,
      service_charge_money: zero,
    },
    net_amount_due_money: money(Math.max(0, total - tenderedTotal(order)), currency),
  });
}

/** OrderEntry, returned by SearchOrders when `return_entries` is true. */
export function projectOrderEntry(order: OrderEntity): Record<string, unknown> {
  return { order_id: order.id, version: order.version, location_id: order.locationId };
}
