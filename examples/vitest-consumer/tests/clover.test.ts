/**
 * What a restaurant-ordering integration does against Clover, over HTTP,
 * from TypeScript. Both Clover hosts -- OAuth and /v3 -- are the same base
 * URL here; every /v3 path is scoped to the seeded merchant.
 */

import { timingSafeEqual } from "node:crypto";
import { beforeAll, describe, expect, inject, test } from "vitest";
import { api, clover, deliveries, header } from "./helpers";

let base: ReturnType<typeof api>;
let asSeed: ReturnType<typeof api>;

beforeAll(() => {
  const url = inject("vendorfake").clover;
  base = api(url);
  asSeed = api(url, { authorization: `Bearer ${clover.accessToken}` });
});

describe("clover", () => {
  test("token exchange -> atomic order -> payment -> locked / PAID", async () => {
    const authorize = await base.get("/oauth/v2/authorize", { query: { client_id: clover.clientId } });
    expect(authorize.status).toBe(302);
    const redirect = new URL(authorize.headers.get("location")!);
    expect(redirect.searchParams.get("merchant_id")).toBe(clover.merchantId);

    const token = await base.post<{ access_token: string; refresh_token: string }>("/oauth/v2/token", {
      client_id: clover.clientId,
      client_secret: clover.clientSecret,
      code: redirect.searchParams.get("code"),
    });
    expect(token.status, token.text).toBe(200);
    const asMerchant = api(inject("vendorfake").clover, { authorization: `Bearer ${token.body.access_token}` });

    const created = await asMerchant.post<{ id: string; total: number; state: string; paymentState: string }>(
      clover.path("/atomic_order/orders"),
      {
        orderCart: {
          orderType: { id: clover.orderTypeDineInId },
          lineItems: [
            { item: { id: clover.itemEspressoId }, modifications: [{ modifier: { id: clover.modifierOatId } }] },
            { item: { id: clover.itemCroissantId } },
          ],
          serviceCharge: { id: clover.serviceChargeId },
        },
      },
    );
    expect(created.status, created.text).toBe(200);
    expect(created.body.total).toBe(1002);
    expect([created.body.state, created.body.paymentState]).toEqual(["open", "OPEN"]);

    const paid = await asMerchant.post<{ result: string }>(clover.path(`/orders/${created.body.id}/payments`), {
      tender: { id: clover.tenderExternalId },
      employee: { id: clover.employeeBaristaId },
      amount: created.body.total,
      offline: false,
    });
    expect(paid.status, paid.text).toBe(200);
    expect(paid.body.result).toBe("SUCCESS");

    const fetched = await asMerchant.get<{ state: string; paymentState: string }>(clover.path(`/orders/${created.body.id}`));
    expect([fetched.body.state, fetched.body.paymentState]).toEqual(["locked", "PAID"]);
  });

  test("an O:CREATE webhook arrives with the X-Clover-Auth code you configured", async () => {
    const authCode = "ts-auth-code-from-the-dashboard";
    const subscribed = await base.post("/__unit/webhooks/subscriptions", {
      notification_url: inject("receiverUrl"),
      event_types: ["O:*"],
      signature_key: authCode,
    });
    expect([200, 201]).toContain(subscribed.status);

    const before = deliveries(inject("receiverLog")).length;
    const created = await asSeed.post<{ id: string }>(clover.path("/orders"), {
      currency: "USD",
      total: 1500,
      state: "open",
      title: "Table 4",
    });
    expect(created.status, created.text).toBe(200);
    expect((await base.post("/__unit/webhooks/drain", {})).status).toBe(200);

    const arrived = deliveries(inject("receiverLog")).slice(before);
    const delivery = arrived.find((d) => JSON.parse(d.body.toString()).appId === clover.clientId);
    expect(delivery, "no Clover delivery arrived").toBeDefined();

    // Clover's whole scheme: the auth code, verbatim, in one header.
    const actual = Buffer.from(header(delivery!, "x-clover-auth") ?? "");
    expect(actual.length).toBe(authCode.length);
    expect(timingSafeEqual(actual, Buffer.from(authCode))).toBe(true);

    const payload = JSON.parse(delivery!.body.toString());
    const [event] = payload.merchants[clover.merchantId];
    expect(event.objectId).toBe(`O:${created.body.id}`);
    expect(event.type).toBe("CREATE");
  });

  test("a transient 401 is followed by a 200", async () => {
    const armed = await base.post("/__unit/chaos/rules", {
      id: "ts-401",
      scope: "request",
      fault: "token_expiry",
      match: { route: "GET /v3/merchants/{mId}/items" },
      when: { nth: [1] },
    });
    expect(armed.status, armed.text).toBe(200);
    const first = await asSeed.get<{ message: string }>(clover.path("/items"));
    expect(first.status).toBe(401);
    expect(first.body.message).toBe("401 Unauthorized");
    expect((await asSeed.get(clover.path("/items"))).status).toBe(200);
  });
});
