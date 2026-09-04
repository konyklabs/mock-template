/**
 * What a restaurant-ordering integration does against Toast, over HTTP, from
 * TypeScript. The vendor a JavaScript client is likeliest to get wrong: money
 * is decimal dollars on the wire, and the restaurant is named by a header, so
 * a bearer on its own is refused for a reason that is not authentication.
 *
 * Nothing here names a guid. The credentials come from `/__unit/auth` and the
 * ids from the menu and the configuration lists, the way a partner would find
 * them.
 *
 * Every assertion below is on something Toast publishes. Where this unit had
 * to choose -- the status for a missing header, the status an OTHER payment
 * lands in, which timestamp the signature covers -- the assertion is weakened
 * to the part a consumer could rely on against the real API, and the comment
 * says why. Do not strengthen one of those back: `src/vendorfake/toast/`
 * labels each of them JUDGMENT at its source.
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

/**
 * base64(HMAC-SHA256(secret, rawBody + body.timestamp)).
 *
 * Toast documents the algorithm and that it covers "the body and timestamp of
 * the webhook message", but not *which* timestamp string: there is no
 * timestamp header on a delivery, so the envelope's own `timestamp` field is
 * the only candidate, and appending it is a reading rather than a quotation
 * (`toast/signer.py` labels it the loudest judgment in that package). A
 * handler that disagrees with the real Toast should try the raw body alone
 * before assuming its HMAC is wrong.
 */
function toastSignature(secret: string, rawBody: Buffer): string {
  // Mirrors `toast/signer.py` for every body the fake emits (konyklabs/roadmap#49):
  // the timestamp is appended only when the body is a JSON object carrying one
  // as a string; any other body -- no timestamp, a numeric one, an array, not
  // JSON at all -- is signed alone. The two still differ where JSON.parse and
  // Python's json.loads do (NaN/Infinity, which the fake never emits) and on
  // invalid UTF-8 (Node substitutes U+FFFD, Python falls back to the raw body).
  // The parity vectors below are the same four `tests/unit/toast/test_signer.py` pins.
  let timestamp = "";
  try {
    const parsed: unknown = JSON.parse(rawBody.toString("utf8"));
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      const candidate = (parsed as { timestamp?: unknown }).timestamp;
      if (typeof candidate === "string") timestamp = candidate;
    }
  } catch {
    // not JSON: sign the raw body alone
  }
  return createHmac("sha256", secret).update(Buffer.concat([rawBody, Buffer.from(timestamp, "utf8")])).digest("base64");
}

describe("toast signature helper parity", () => {
  // The same four vectors `tests/unit/toast/test_signer.py` pins against
  // `vendorfake.toast.signer.toast_signature`, secret "unit-toast-secret".
  test.each([
    ['{"a":1}', "rTBExUaDzRnVkfhaD8Pz7qIYnG9jsWmuOSCbWXqiphQ="],
    ['{"timestamp":"2026-01-01T00:00:00.000+0000","a":1}', "Tsbjo/JYVqjaFU5+djDD34GtYFSkfRqNUe+sjrl+U6k="],
    ['{"timestamp":1700000000,"a":1}', "Y0z3ljb+/jq9KdNW7wOd/ecsnIdaWZpesqMaZEFZ4zU="],
    ["[1,2]", "4OGec3FSLo4jYVHtIb2SAZ4Mwdc7XMo0SaREpuPd7gQ="],
  ])("signs %s the way signer.py does", (body, expected) => {
    expect(toastSignature("unit-toast-secret", Buffer.from(body, "utf8"))).toBe(expected);
  });
});

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

    // Toast documents that the token is a JWT and that it carries
    // `partner_guid`, and nothing else about its claims. Do not decode one to
    // learn its scopes -- a consumer never holds Toast's signing key and must
    // not verify a Toast token locally (`toast/jwt.py`). The shape is all
    // there is to check.
    expect(login.body.token.accessToken.split(".")).toHaveLength(3);

    const asMinted = api(inject("vendorfake").toast, {
      authorization: `Bearer ${login.body.token.accessToken}`,
      [toast.restaurantHeader]: restaurant,
    });
    const found = await asMinted.get<{ guid: string }>(`/restaurants/v1/restaurants/${restaurant}`);
    expect(found.status, found.text).toBe(200);
    expect(found.body.guid).toBe(restaurant);
  });

  test("a bearer without the restaurant header is refused, but not as bad auth", async () => {
    // The token is fine; the request never named a restaurant. Toast does not
    // document what a MISSING header gets -- this unit answers 400 and labels
    // that choice a judgment (`toast/auth.py`) -- so the assertion here is the
    // part that changes what a client does, and that a consumer can carry to
    // the real API: it is not an authentication failure, so logging in again
    // cannot fix it. Send the header. (The exact status and message are the
    // unit's, and are pinned in the unit's own tests, not in an example.)
    const noHeader = await asPartner.get<{ unit_error?: { reason: string } }>("/menus/v3/menus");
    expect(noHeader.status, noHeader.text).not.toBe(401);
    expect(noHeader.status).toBeGreaterThanOrEqual(400);
    expect(noHeader.status).toBeLessThan(500);
    // FAKE-ONLY. Which refusal it is: Toast's envelope does not say, and this
    // unit's wording is not Toast's, so the distinction is read off the fake's
    // `unit_error` sidecar -- a deliberate, namespaced deviation from the wire
    // format, off under `{"error_sidecar": false}` and absent against the real
    // API. Guarded on its presence so this file, copied, still passes there;
    // it just proves less.
    if (noHeader.body.unit_error !== undefined) {
      expect(noHeader.body.unit_error.reason).toBe("restaurant_header_missing");
    }

    // A missing bearer, though, is the documented 401.
    const named = api(inject("vendorfake").toast, { [toast.restaurantHeader]: restaurant });
    const noToken = await named.get<{ status: number }>("/menus/v3/menus");
    expect(noToken.status, noToken.text).toBe(401);
    expect(noToken.body.status).toBe(401); // the error envelope repeats it, documented verbatim

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
    // A JSON number of dollars, held two ways. `typeof` is the one that does
    // not care how the server spaces its JSON; the raw-bytes check is the
    // proof that 9.55 crossed the wire as a number rather than as "9.55", and
    // it only holds while the serializer stays compact. (`Number.isInteger`
    // alone would not do it: it is false for a string too, so it catches
    // integer cents and nothing else.)
    expect(typeof quote.totalAmount).toBe("number");
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
    const external = types.body.find((t) => t.name === "External");
    expect(external, "no External alternate payment type is configured").toBeDefined();
    // Payments are a list even when there is one of them.
    const paid = await asSeed.post<Order>(
      `/orders/v2/orders/${created.body.guid}/checks/${check.guid}/payments`,
      [{ type: "OTHER", amount: check.totalAmount, tipAmount: 0, otherPayment: { guid: external!.guid } }],
    );
    expect(paid.status, paid.text).toBe(200);
    const [payment] = paid.body.checks[0].payments;
    expect(payment.amount).toBe(9.55);
    // Not the payment's own status: what an OTHER payment lands in is
    // undocumented (this unit says CAPTURED). What settles the check is.

    const fetched = await asSeed.get<Order>(`/orders/v2/orders/${created.body.guid}`);
    // DOCUMENTED: an OTHER payment covering the total closes the check -- the
    // payment walkthrough's own result answers CLOSED
    // (doc.toasttab.com/doc/devguide/apiCreatingAnOrderWithPaymentInformation.html);
    // PAID is a card charge whose tip is still unadjusted. The unit answered
    // PAID here before the fidelity corpus caught it (konyklabs/roadmap#56).
    expect(fetched.body.checks[0].paymentStatus).toBe("CLOSED");
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
    const first = await asSeed.get<{ status: number }>("/menus/v3/metadata");
    expect(first.status).toBe(429); // documented, with Retry-After (apiRateLimiting.html)
    expect(first.headers.get("retry-after")).toBe("0"); // the delay armed above; a real 429's is Toast's
    expect(first.body.status).toBe(429);
    expect((await asSeed.get("/menus/v3/metadata")).status).toBe(200);
  });
});
