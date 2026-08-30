/**
 * What a restaurant-ordering integration does against Square, over HTTP,
 * from TypeScript. Same requests as the pytest example; a second language and
 * a second HMAC implementation (node:crypto) checking the same bytes.
 */

import { createHmac, timingSafeEqual } from "node:crypto";
import { beforeAll, describe, expect, inject, test } from "vitest";
import { api, deliveries, header, square } from "./helpers";

let base: ReturnType<typeof api>;
let asSeed: ReturnType<typeof api>;

beforeAll(() => {
  const url = inject("vendorfake").square;
  base = api(url);
  asSeed = api(url, { authorization: `Bearer ${square.accessToken}` });
});

/** base64(HMAC-SHA256(key, notificationUrl + rawBody)) -- Square's documented scheme. */
function squareSignature(signatureKey: string, notificationUrl: string, rawBody: Buffer): string {
  return createHmac("sha256", signatureKey).update(Buffer.concat([Buffer.from(notificationUrl), rawBody])).digest("base64");
}

describe("square", () => {
  test("the unit is healthy and says which vendor it is", async () => {
    const health = await base.get<{ status: string; vendor: string; framework_answered: number }>("/__unit/health");
    expect(health.status).toBe(200);
    expect(health.body.vendor).toBe("square");
    expect(health.body.framework_answered).toBe(0);
  });

  test("oauth exchange -> create order -> pay -> COMPLETED", async () => {
    const authorize = await base.get("/oauth2/authorize", {
      query: { client_id: square.applicationId, scope: "ORDERS_READ ORDERS_WRITE PAYMENTS_WRITE", state: "csrf" },
    });
    expect(authorize.status).toBe(302);
    const code = new URL(authorize.headers.get("location")!).searchParams.get("code");
    expect(code).toMatch(/^sq0cgb-/);

    const token = await base.post<{ access_token: string; merchant_id: string; token_type: string }>("/oauth2/token", {
      client_id: square.applicationId,
      client_secret: square.applicationSecret,
      grant_type: "authorization_code",
      code,
    });
    expect(token.status, token.text).toBe(200);
    expect(token.body.merchant_id).toBe(square.merchantId);
    const asMerchant = api(inject("vendorfake").square, { authorization: `Bearer ${token.body.access_token}` });

    const created = await asMerchant.post<{ order: { id: string; state: string; version: number; total_money: unknown } }>(
      "/v2/orders",
      {
        idempotency_key: "ts-ticket-1",
        order: { location_id: square.locationId, line_items: [{ catalog_object_id: square.teaMugVariationId, quantity: "2" }] },
      },
    );
    expect(created.status, created.text).toBe(200);
    const order = created.body.order;
    expect(order.state).toBe("OPEN");
    expect(order.total_money).toEqual({ amount: 300, currency: "USD" });

    const paid = await asMerchant.post<{ payment: { id: string; status: string } }>("/v2/payments", {
      idempotency_key: "ts-ticket-1-pay",
      source_id: "EXTERNAL",
      amount_money: { amount: 300, currency: "USD" },
      external_details: { type: "OTHER", source: "Counter" },
      order_id: order.id,
    });
    expect(paid.status, paid.text).toBe(200);
    expect(paid.body.payment.status).toBe("COMPLETED");

    const fetched = await asMerchant.get<{ order: { state: string; version: number; tenders: Array<{ payment_id: string }> } }>(
      `/v2/orders/${order.id}`,
    );
    expect(fetched.body.order.state).toBe("COMPLETED");
    expect(fetched.body.order.version).toBe(order.version + 1);
    expect(fetched.body.order.tenders[0].payment_id).toBe(paid.body.payment.id);
  });

  test("an order.created webhook reaches the receiver and its signature verifies", async () => {
    const registered = await asSeed.post<{ subscription: { id: string; signature_key: string } }>("/v2/webhooks/subscriptions", {
      idempotency_key: "ts-sub-1",
      subscription: { name: "orders", event_types: ["order.created"], notification_url: inject("receiverUrl") },
    });
    expect(registered.status, registered.text).toBe(200);
    const signatureKey = registered.body.subscription.signature_key;

    const before = deliveries(inject("receiverLog")).length;
    const created = await asSeed.post<{ order: { id: string } }>("/v2/orders", {
      idempotency_key: "ts-ticket-2",
      order: { location_id: square.locationId, line_items: [{ catalog_object_id: square.teaMugVariationId, quantity: "1" }] },
    });
    expect(created.status, created.text).toBe(200);
    expect((await asSeed.post("/__unit/webhooks/drain", {})).status).toBe(200);

    const arrived = deliveries(inject("receiverLog")).slice(before);
    const delivery = arrived.find((d) => JSON.parse(d.body.toString()).type === "order.created");
    expect(delivery, "no order.created delivery arrived").toBeDefined();

    const expected = Buffer.from(squareSignature(signatureKey, inject("receiverUrl"), delivery!.body));
    const actual = Buffer.from(header(delivery!, "x-square-hmacsha256-signature") ?? "");
    expect(actual.length).toBe(expected.length);
    expect(timingSafeEqual(actual, expected)).toBe(true);
    expect(header(delivery!, "square-environment")).toBe("Sandbox");

    const event = JSON.parse(delivery!.body.toString());
    expect(event.merchant_id).toBe(square.merchantId);
    expect(event.data.object.order_created.order_id).toBe(created.body.order.id);
  });

  test("a rate limit is deterministic and the retry succeeds", async () => {
    const armed = await base.post("/__unit/chaos/rules", {
      id: "ts-429",
      scope: "request",
      fault: "rate_limit",
      match: { route: "GET /v2/locations" },
      when: { nth: [1] },
      params: { retry_after_seconds: 0 },
    });
    expect(armed.status, armed.text).toBe(200);
    const first = await asSeed.get<{ errors: Array<{ code: string }> }>("/v2/locations");
    expect(first.status).toBe(429);
    expect(first.headers.get("retry-after")).toBe("0");
    expect(first.body.errors[0].code).toBe("RATE_LIMITED");
    expect((await asSeed.get("/v2/locations")).status).toBe(200);
  });
});
