/**
 * What a restaurant-ordering integration does against Toast, over HTTP, from
 * TypeScript. The vendor a JavaScript client is likeliest to get wrong: money
 * is decimal dollars on the wire, and the restaurant is named by a header, so
 * a bearer on its own is a 400 rather than a 401.
 *
 * Nothing here names a guid. The credentials come from `/__unit/auth` and the
 * ids from the menu and the configuration lists, the way a partner would find
 * them.
 */

import { createHmac, timingSafeEqual } from "node:crypto";
import { beforeAll, describe, expect, inject, test } from "vitest";
import { api, deliveries, header, toast } from "./helpers";

interface PublishedCredential {
  mode: string;
  headers: Record<string, string>;
  scopes: string[];
}

interface LoginReply {
  "@class": string;
  status: string;
  token: { tokenType: string; accessToken: string; expiresIn: number };
}

interface MenuItem {
  guid: string;
  name: string;
  price: number;
}

interface Check {
  guid: string | null;
  amount: number;
  taxAmount: number;
  totalAmount: number;
  paymentStatus: string;
  payments: Array<{ guid: string; type: string; amount: number; tipAmount: number; paymentStatus: string }>;
  selections: Array<{ guid: string | null; price: number; tax: number; appliedTaxes: Array<{ rate: number }> }>;
}

interface Order {
  guid: string | null;
  entityType: string;
  checks: Check[];
}

let base: ReturnType<typeof api>;
/** The seeded token acting for the restaurant: both headers. */
let asSeed: ReturnType<typeof api>;
/** The same token with no restaurant header -- a partner naming no restaurant. */
let asPartner: ReturnType<typeof api>;
/** Discovered in `beforeAll`, all of it, from the unit itself. */
let restaurant: string;
let soup: MenuItem;
let dineInGuid: string;

beforeAll(async () => {
  const url = inject("vendorfake").toast;
  base = api(url);

  // Both ways of holding the seeded token, published side by side: with the
  // restaurant it is acting for, and without.
  const published = await base.get<{ credentials: PublishedCredential[] }>("/__unit/auth");
  expect(published.status, published.text).toBe(200);
  const writes = (c: PublishedCredential) => c.scopes.includes("orders:write");
  const scoped = published.body.credentials.find((c) => c.mode === "restaurant" && writes(c));
  const unscoped = published.body.credentials.find((c) => c.mode === "bearer" && writes(c));
  expect(scoped, "no restaurant-scoped credential can write orders").toBeDefined();
  expect(unscoped, "no bearer-only credential can write orders").toBeDefined();
  restaurant = scoped!.headers[toast.restaurantHeader];
  asSeed = api(url, scoped!.headers);
  asPartner = api(url, unscoped!.headers);

  // The documented pricing example is an 8.99 item, so find the one the menu
  // prices at 8.99 rather than naming its guid.
  const menu = await asSeed.get<{ menus: Array<{ menuGroups: Array<{ menuItems: MenuItem[] }> }> }>("/menus/v3/menus");
  expect(menu.status, menu.text).toBe(200);
  const items = menu.body.menus.flatMap((m) => m.menuGroups.flatMap((g) => g.menuItems));
  const priced = items.find((i) => i.price === 8.99);
  expect(priced, `no item is priced at 8.99: ${items.map((i) => `${i.name} ${i.price}`).join(", ")}`).toBeDefined();
  soup = priced!;

  const options = await asSeed.get<Array<{ guid: string; behavior: string }>>("/config/v2/diningOptions");
  expect(options.status, options.text).toBe(200);
  const dineIn = options.body.find((o) => o.behavior === "DINE_IN");
  expect(dineIn, "no DINE_IN dining option is configured").toBeDefined();
  dineInGuid = dineIn!.guid;
});

/** One soup, dine in -- the body both `/prices` and `/orders` take. */
function order() {
  return {
    entityType: "Order",
    diningOption: { guid: dineInGuid, entityType: "DiningOption" },
    checks: [{ entityType: "Check", selections: [{ item: { guid: soup.guid, entityType: "MenuItem" }, quantity: 1 }] }],
  };
}

/** base64(HMAC-SHA256(secret, rawBody + body.timestamp)) -- Toast's documented scheme. */
function toastSignature(secret: string, rawBody: Buffer): string {
  const timestamp = JSON.parse(rawBody.toString()).timestamp as string;
  return createHmac("sha256", secret).update(Buffer.concat([rawBody, Buffer.from(timestamp)])).digest("base64");
}

describe("toast", () => {
  test("the machine client logs in and the JWT it mints is accepted", async () => {
    const login = await base.post<LoginReply>("/authentication/v1/authentication/login", {
      clientId: toast.clientId,
      clientSecret: toast.clientSecret,
      userAccessType: "TOAST_MACHINE_CLIENT",
    });
    expect(login.status, login.text).toBe(200);
    expect(login.body["@class"]).toBe(".SuccessfulResponse");
    expect(login.body.status).toBe("SUCCESS");
    expect(login.body.token.tokenType).toBe("Bearer");

    const [, payload] = login.body.token.accessToken.split(".");
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
    expect(claims.exp - claims.iat).toBe(login.body.token.expiresIn);
    expect(claims.scope.split(" ")).toContain("orders:write");

    const asMinted = api(inject("vendorfake").toast, {
      authorization: `Bearer ${login.body.token.accessToken}`,
      [toast.restaurantHeader]: restaurant,
    });
    const found = await asMinted.get<{ guid: string }>(`/restaurants/v1/restaurants/${restaurant}`);
    expect(found.status, found.text).toBe(200);
    expect(found.body.guid).toBe(restaurant);
  });

  test("a bearer without the restaurant header is a 400, and no bearer is a 401", async () => {
    // The token is fine; the request never named a restaurant. A client that
    // reads every refusal on this route as an expired token re-logs in for
    // ever and never sends the header it is actually missing.
    const noHeader = await asPartner.get<{ status: number; message: string }>("/menus/v3/menus");
    expect(noHeader.status, noHeader.text).toBe(400);
    expect(noHeader.body.status).toBe(400);
    expect(noHeader.body.message).toContain("Toast-Restaurant-External-ID");

    const named = api(inject("vendorfake").toast, { [toast.restaurantHeader]: restaurant });
    const noToken = await named.get<{ status: number }>("/menus/v3/menus");
    expect(noToken.status, noToken.text).toBe(401);
    expect(noToken.body.status).toBe(401);

    expect((await asSeed.get("/menus/v3/menus")).status).toBe(200);
  });

  test("prices -> order -> payment: 8.99 becomes 9.55, in dollars on the wire", async () => {
    const quoted = await asSeed.post<Order>("/orders/v2/prices", order());
    expect(quoted.status, quoted.text).toBe(200);
    // A quote persists nothing, so every guid on it is null.
    expect(quoted.body.guid).toBeNull();
    const quote = quoted.body.checks[0];
    expect([quote.amount, quote.taxAmount, quote.totalAmount]).toEqual([8.99, 0.56, 9.55]);
    expect(quote.selections[0].appliedTaxes[0].rate).toBe(0.0625);
    // The bytes, not the parse: a JSON number of dollars. Integer cents (955)
    // or a string ("9.55") would both survive the assertions above.
    expect(quoted.text).toContain('"totalAmount":9.55');
    expect(Number.isInteger(quote.totalAmount)).toBe(false);
    expect(quote.amount + quote.taxAmount).toBeCloseTo(quote.totalAmount, 10);

    const created = await asSeed.post<Order>("/orders/v2/orders", { ...order(), externalId: "ts-toast-ticket-1" });
    expect(created.status, created.text).toBe(200);
    const check = created.body.checks[0];
    expect(created.body.guid).not.toBeNull();
    expect(check.totalAmount).toBe(quote.totalAmount);
    expect(check.paymentStatus).toBe("OPEN");
    expect(check.payments).toEqual([]);

    const types = await asSeed.get<Array<{ guid: string; name: string }>>("/config/v2/alternatePaymentTypes");
    expect(types.status, types.text).toBe(200);
    // Payments are a list even when there is one of them.
    const paid = await asSeed.post<Order>(
      `/orders/v2/orders/${created.body.guid}/checks/${check.guid}/payments`,
      [{ type: "OTHER", amount: check.totalAmount, tipAmount: 0, otherPayment: { guid: types.body[0].guid } }],
    );
    expect(paid.status, paid.text).toBe(200);
    const [payment] = paid.body.checks[0].payments;
    expect(payment.amount).toBe(9.55);
    expect(payment.paymentStatus).toBe("CAPTURED");

    const fetched = await asSeed.get<Order>(`/orders/v2/orders/${created.body.guid}`);
    expect(fetched.body.checks[0].paymentStatus).toBe("PAID");
    expect(fetched.body.checks[0].payments[0].guid).toBe(payment.guid);
  });

  test("an order_updated delivery arrives and its Toast-Signature verifies", async () => {
    // Toast's own registration route refuses a callback that is not HTTPS, and
    // the receiver here is plain http on loopback, so the subscriber is
    // registered through the core control plane -- same subscription list.
    const secret = "ts-toast-webhook-secret";
    const subscribed = await base.post("/__unit/webhooks/subscriptions", {
      notification_url: inject("receiverUrl"),
      event_types: ["order_updated"],
      signature_key: secret,
    });
    expect([200, 201]).toContain(subscribed.status);

    const before = deliveries(inject("receiverLog")).length;
    const created = await asSeed.post<Order>("/orders/v2/orders", { ...order(), externalId: "ts-toast-ticket-2" });
    expect(created.status, created.text).toBe(200);
    expect((await base.post("/__unit/webhooks/drain", {})).status).toBe(200);

    const arrived = deliveries(inject("receiverLog")).slice(before);
    const delivery = arrived.find((d) => JSON.parse(d.body.toString()).details?.order?.guid === created.body.guid);
    expect(delivery, "no order_updated delivery arrived").toBeDefined();

    const expected = Buffer.from(toastSignature(secret, delivery!.body));
    const actual = Buffer.from(header(delivery!, "toast-signature") ?? "");
    expect(actual.length).toBe(expected.length);
    expect(timingSafeEqual(actual, expected)).toBe(true);
    expect(header(delivery!, "toast-event-type")).toBe("order_updated");
    expect(header(delivery!, "toast-restaurant-external-id")).toBe(restaurant);
    expect(header(delivery!, "toast-attempt-number")).toBe("1");

    const envelope = JSON.parse(delivery!.body.toString());
    expect(envelope.details.restaurantGuid).toBe(restaurant);
    // The order as GET answers it, dollars included.
    expect(envelope.details.order.checks[0].totalAmount).toBe(9.55);
  });

  test("a rate limit is deterministic and the retry succeeds", async () => {
    const armed = await base.post("/__unit/chaos/rules", {
      id: "ts-toast-429",
      scope: "request",
      fault: "rate_limit",
      match: { route: "GET /menus/v3/metadata" },
      when: { nth: [1] },
      params: { retry_after_seconds: 0 },
    });
    expect(armed.status, armed.text).toBe(200);
    const first = await asSeed.get<{ status: number; message: string }>("/menus/v3/metadata");
    expect(first.status).toBe(429);
    expect(first.headers.get("retry-after")).toBe("0");
    expect(first.body.status).toBe(429);
    expect((await asSeed.get("/menus/v3/metadata")).status).toBe(200);
  });
});
