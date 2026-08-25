import { describe, expect, it } from 'vitest';
import { SEED_LOCATION, SEED_OPEN_ORDER, TEA_MUG_VARIATION, harness, orderBody } from './helpers.js';

/**
 * Slice element 2: stateful order flow. Every assertion here is about state
 * SURVIVING a call boundary — the mutation is made by one request and observed
 * by a later one.
 */
describe('orders', () => {
  it('loads the seed scenario and reflects it on retrieve', async () => {
    const h = await harness();
    const res = await h.api.get<{ order: Record<string, any> }>(`/v2/orders/${SEED_OPEN_ORDER}`, { headers: h.auth });
    expect(res.status).toBe(200);
    expect(res.body.order.state).toBe('OPEN');
    expect(res.body.order.version).toBe(1);
    expect(res.body.order.location_id).toBe(SEED_LOCATION);
    // 2 x Tea Mug (150) + 1 x Cold Brew Large (525) = 825.
    expect(res.body.order.total_money).toEqual({ amount: 825, currency: 'USD' });
    expect(res.body.order.net_amounts.total_money).toEqual({ amount: 825, currency: 'USD' });
    await h.stop();
  });

  it('creates an order and prices line items from the catalog', async () => {
    const h = await harness();
    const created = await h.api.post<{ order: Record<string, any> }>('/v2/orders', orderBody(), { headers: h.auth });
    expect(created.status).toBe(200);
    const order = created.body.order;
    expect(order.id).toMatch(/^CAIS/);
    expect(order.state).toBe('OPEN');
    expect(order.version).toBe(1);
    expect(order.line_items[0].name).toBe('Tea');
    expect(order.line_items[0].variation_name).toBe('Mug');
    expect(order.line_items[0].base_price_money).toEqual({ amount: 150, currency: 'USD' });
    expect(order.total_money).toEqual({ amount: 300, currency: 'USD' });

    // The mutation persists: a fresh retrieve sees it.
    const fetched = await h.api.get<{ order: Record<string, any> }>(`/v2/orders/${order.id}`, { headers: h.auth });
    expect(fetched.body.order.total_money).toEqual({ amount: 300, currency: 'USD' });
    await h.stop();
  });

  it('replays an identical idempotent create and rejects a mismatched one', async () => {
    const h = await harness();
    const body = orderBody();
    const first = await h.api.post<{ order: { id: string } }>('/v2/orders', body, { headers: h.auth });
    const replay = await h.api.post<{ order: { id: string } }>('/v2/orders', body, { headers: h.auth });
    expect(replay.status).toBe(200);
    expect(replay.body.order.id).toBe(first.body.order.id);
    expect(replay.headers['x-unit-idempotent-replay']).toBe('true');

    const mismatched = await h.api.post<{ errors: Array<{ code: string; field: string }> }>(
      '/v2/orders',
      { ...body, order: { ...(body.order as object), reference_id: 'different' } },
      { headers: h.auth },
    );
    expect(mismatched.status).toBe(400);
    expect(mismatched.body.errors[0]!.code).toBe('IDEMPOTENCY_KEY_REUSED');

    const search = await h.api.post<{ orders: Array<{ id: string }> }>(
      '/v2/orders/search',
      { location_ids: [SEED_LOCATION] },
      { headers: h.auth },
    );
    // One create, one replay, one rejection: exactly one new order exists.
    expect(search.body.orders.filter((o) => o.id === first.body.order.id)).toHaveLength(1);
    await h.stop();
  });

  it('updates under optimistic concurrency and rejects a stale version', async () => {
    const h = await harness();
    const created = await h.api.post<{ order: { id: string; version: number } }>('/v2/orders', orderBody(), { headers: h.auth });
    const id = created.body.order.id;

    const updated = await h.api.put<{ order: Record<string, any> }>(
      `/v2/orders/${id}`,
      { idempotency_key: 'upd-1', order: { version: 1, ticket_name: 'Table 9' } },
      { headers: h.auth },
    );
    expect(updated.status).toBe(200);
    expect(updated.body.order.version).toBe(2);
    expect(updated.body.order.ticket_name).toBe('Table 9');

    const stale = await h.api.put<{ errors: Array<{ code: string; category: string; detail: string }> }>(
      `/v2/orders/${id}`,
      { idempotency_key: 'upd-2', order: { version: 1, ticket_name: 'Table 10' } },
      { headers: h.auth },
    );
    expect(stale.status).toBe(400);
    expect(stale.body.errors[0]!.code).toBe('VERSION_MISMATCH');
    expect(stale.body.errors[0]!.category).toBe('INVALID_REQUEST_ERROR');
    expect(stale.body.errors[0]!.detail).toContain('does not match the current version 2');

    // The rejected update did not commit.
    const after = await h.api.get<{ order: Record<string, any> }>(`/v2/orders/${id}`, { headers: h.auth });
    expect(after.body.order.ticket_name).toBe('Table 9');
    expect(after.body.order.version).toBe(2);
    await h.stop();
  });

  it('merges sparse line-item updates and honours fields_to_clear', async () => {
    const h = await harness();
    const created = await h.api.post<{ order: Record<string, any> }>(
      '/v2/orders',
      {
        idempotency_key: 'sparse-create',
        order: {
          location_id: SEED_LOCATION,
          reference_id: 'to-be-cleared',
          line_items: [
            { uid: 'li_a', catalog_object_id: TEA_MUG_VARIATION, quantity: '1', note: 'no sugar' },
            { uid: 'li_b', name: 'Manual', quantity: '1', base_price_money: { amount: 900, currency: 'USD' } },
          ],
        },
      },
      { headers: h.auth },
    );
    const id = created.body.order.id;
    expect(created.body.order.total_money.amount).toBe(1050);

    const updated = await h.api.put<{ order: Record<string, any> }>(
      `/v2/orders/${id}`,
      {
        idempotency_key: 'sparse-update',
        order: { version: 1, line_items: [{ uid: 'li_a', quantity: '4' }] },
        fields_to_clear: ['line_items[li_b]', 'reference_id'],
      },
      { headers: h.auth },
    );
    expect(updated.status).toBe(200);
    expect(updated.body.order.reference_id).toBeUndefined();
    expect(updated.body.order.line_items).toHaveLength(1);
    // Quantity replaced, note preserved: a sparse update, not a wholesale replace.
    expect(updated.body.order.line_items[0].quantity).toBe('4');
    expect(updated.body.order.line_items[0].note).toBe('no sugar');
    expect(updated.body.order.total_money.amount).toBe(600);
    await h.stop();
  });

  it('walks the lifecycle to COMPLETED and refuses to update a terminal order', async () => {
    const h = await harness();
    const created = await h.api.post<{ order: { id: string } }>('/v2/orders', orderBody(), { headers: h.auth });
    const id = created.body.order.id;

    const paid = await h.api.post<{ order: Record<string, any> }>(
      `/v2/orders/${id}/pay`,
      { idempotency_key: 'pay-1', order_version: 1, payment_ids: ['PAY_ABC'] },
      { headers: h.auth },
    );
    expect(paid.status).toBe(200);
    expect(paid.body.order.state).toBe('COMPLETED');
    expect(paid.body.order.closed_at).toBeTruthy();
    expect(paid.body.order.tenders[0].amount_money).toEqual({ amount: 300, currency: 'USD' });
    expect(paid.body.order.net_amount_due_money).toEqual({ amount: 0, currency: 'USD' });

    const reopen = await h.api.put<{ errors: Array<{ code: string; detail: string }> }>(
      `/v2/orders/${id}`,
      { idempotency_key: 'reopen', order: { version: 2, state: 'OPEN' } },
      { headers: h.auth },
    );
    expect(reopen.status).toBe(400);
    expect(reopen.body.errors[0]!.detail).toContain('terminal');
    await h.stop();
  });

  it('cancels an open order and blocks payment afterwards', async () => {
    const h = await harness();
    const created = await h.api.post<{ order: { id: string } }>('/v2/orders', orderBody(), { headers: h.auth });
    const id = created.body.order.id;

    const canceled = await h.api.put<{ order: Record<string, any> }>(
      `/v2/orders/${id}`,
      { idempotency_key: 'cancel-1', order: { version: 1, state: 'CANCELED' } },
      { headers: h.auth },
    );
    expect(canceled.body.order.state).toBe('CANCELED');

    const pay = await h.api.post<{ errors: Array<{ detail: string }> }>(
      `/v2/orders/${id}/pay`,
      { idempotency_key: 'pay-canceled', payment_ids: ['PAY_X'] },
      { headers: h.auth },
    );
    expect(pay.status).toBe(400);
    expect(pay.body.errors[0]!.detail).toContain('terminal');
    await h.stop();
  });

  it('moves a DRAFT order to OPEN and refuses to pay it while it is a draft', async () => {
    const h = await harness();
    const created = await h.api.post<{ order: { id: string } }>('/v2/orders', orderBody({ state: 'DRAFT' }), { headers: h.auth });
    const id = created.body.order.id;

    const payDraft = await h.api.post<{ errors: Array<{ detail: string }> }>(
      `/v2/orders/${id}/pay`,
      { idempotency_key: 'pay-draft', payment_ids: ['PAY_X'] },
      { headers: h.auth },
    );
    expect(payDraft.status).toBe(400);
    expect(payDraft.body.errors[0]!.detail).toContain('DRAFT');

    const opened = await h.api.put<{ order: { state: string } }>(
      `/v2/orders/${id}`,
      { idempotency_key: 'open-draft', order: { version: 1, state: 'OPEN' } },
      { headers: h.auth },
    );
    expect(opened.body.order.state).toBe('OPEN');
    await h.stop();
  });

  it('searches by state and location, and paginates with an opaque cursor', async () => {
    const h = await harness();
    for (let i = 0; i < 4; i++) {
      await h.api.post('/v2/orders', orderBody({ reference_id: `page-${i}` }), { headers: h.auth });
    }

    const query = { location_ids: [SEED_LOCATION], query: { filter: { state_filter: { states: ['OPEN'] } } }, limit: 2 };
    const first = await h.api.post<{ orders: Array<{ id: string }>; cursor?: string }>('/v2/orders/search', query, { headers: h.auth });
    expect(first.body.orders).toHaveLength(2);
    expect(first.body.cursor).toBeTruthy();

    const second = await h.api.post<{ orders: Array<{ id: string }>; cursor?: string }>(
      '/v2/orders/search',
      { ...query, cursor: first.body.cursor },
      { headers: h.auth },
    );
    expect(second.body.orders).toHaveLength(2);
    const ids = new Set([...first.body.orders, ...second.body.orders].map((o) => o.id));
    expect(ids.size).toBe(4);

    // "When retrieving additional pages using a cursor, you must use the original query."
    const changedQuery = await h.api.post<{ errors: Array<{ code: string }> }>(
      '/v2/orders/search',
      { ...query, location_ids: ['057P5VYJ4A5X1'], cursor: first.body.cursor },
      { headers: h.auth },
    );
    expect(changedQuery.status).toBe(400);
    expect(changedQuery.body.errors[0]!.code).toBe('INVALID_CURSOR');
    await h.stop();
  });

  it('returns order entries and enforces the date-filter/sort-field pairing', async () => {
    const h = await harness();
    const entries = await h.api.post<{ order_entries: Array<Record<string, unknown>> }>(
      '/v2/orders/search',
      { return_entries: true, query: { filter: { state_filter: { states: ['COMPLETED'] } } } },
      { headers: h.auth },
    );
    expect(entries.body.order_entries[0]).toEqual({
      order_id: 'CAISEM82RcpmcFBM0TfOyiHV3es',
      version: 3,
      location_id: '057P5VYJ4A5X1',
    });

    const mismatched = await h.api.post<{ errors: Array<{ field: string }> }>(
      '/v2/orders/search',
      {
        query: {
          filter: { date_time_filter: { closed_at: { start_at: '2020-01-01T00:00:00Z' } } },
          sort: { sort_field: 'CREATED_AT' },
        },
      },
      { headers: h.auth },
    );
    expect(mismatched.status).toBe(400);
    expect(mismatched.body.errors[0]!.field).toBe('query.sort.sort_field');
    await h.stop();
  });

  it('enforces scopes and rejects unknown locations and orders', async () => {
    const h = await harness();
    const forbidden = await h.api.post<{ errors: Array<{ code: string }> }>('/v2/orders', orderBody(), { headers: h.readAuth });
    expect(forbidden.status).toBe(403);
    expect(forbidden.body.errors[0]!.code).toBe('INSUFFICIENT_SCOPES');

    const missing = await h.api.get<{ errors: Array<{ code: string }> }>('/v2/orders/CAISNOPE', { headers: h.auth });
    expect(missing.status).toBe(404);
    expect(missing.body.errors[0]!.code).toBe('NOT_FOUND');

    const badLocation = await h.api.post<{ errors: Array<{ field: string }> }>(
      '/v2/orders',
      { idempotency_key: 'bad-loc', order: { location_id: 'NOSUCHLOCATION', line_items: [] } },
      { headers: h.auth },
    );
    expect(badLocation.status).toBe(400);
    expect(badLocation.body.errors[0]!.field).toBe('order.location_id');
    await h.stop();
  });

  it('records every committed mutation in the journal', async () => {
    const h = await harness();
    const before = await h.api.get<{ seq: number }>('/__unit/journal');
    const created = await h.api.post<{ order: { id: string } }>('/v2/orders', orderBody(), { headers: h.auth });
    await h.api.put(
      `/v2/orders/${created.body.order.id}`,
      { idempotency_key: 'j-1', order: { version: 1, ticket_name: 'Bar' } },
      { headers: h.auth },
    );
    // A rejected update must leave no trace.
    await h.api.put(
      `/v2/orders/${created.body.order.id}`,
      { idempotency_key: 'j-2', order: { version: 99, ticket_name: 'Ghost' } },
      { headers: h.auth },
    );

    const after = await h.api.get<{ entries: Array<{ op: string; id: string; toVersion: number | null; meta?: Record<string, unknown> }> }>(
      '/__unit/journal',
      { query: { since: String(before.body.seq) } },
    );
    expect(after.body.entries).toHaveLength(2);
    expect(after.body.entries.map((e) => e.op)).toEqual(['insert', 'update']);
    expect(after.body.entries[1]!.toVersion).toBe(2);
    expect(after.body.entries[0]!.meta).toMatchObject({ operationId: 'CreateOrder' });
    await h.stop();
  });
});
