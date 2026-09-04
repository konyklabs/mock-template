/**
 * What a retail integration does against Lightspeed X-Series, over HTTP, from
 * TypeScript. The vendor a JavaScript client is likeliest to get wrong in three
 * separate ways.
 *
 * The token endpoint is **form-encoded** and sits under a different version
 * segment (`/api/1.0/token`) from the resource API (`/api/2026-07/...`), so the
 * `api()` helper -- which sends JSON -- is not what calls it.
 *
 * A refresh **revokes the access token it was issued with**, immediately. A
 * client that keeps the pre-refresh bearer passes against a naive mock and
 * fails in production.
 *
 * `Retry-After` on a 429 is an **RFC 1123 date**, not delta-seconds:
 * `parseInt` on it is `NaN`, and a retry loop that trusts a number sleeps
 * forever or not at all.
 *
 * Nothing here imports vendorfake. The signature is recomputed with
 * `node:crypto` and compared with `timingSafeEqual`, which is what makes this
 * independent verification rather than the fake agreeing with itself. Where
 * this unit had to choose -- the status a revoked token gets, the integer in a
 * payment error, what exactly the signature covers -- the assertion is weakened
 * to the part a consumer could rely on against the real API, and the comment
 * says why.
 */

import { createHmac, timingSafeEqual } from "node:crypto";
import { beforeAll, describe, expect, inject, test } from "vitest";
import { api, deliveries, header, lightspeed } from "./helpers";

const API = "/api/2026-07";
const TOKEN_PATH = "/api/1.0/token";

interface TokenReply {
  access_token: string;
  token_type: string;
  expires: number;
  expires_in: number;
  refresh_token: string;
  domain_prefix: string;
  scope: string;
}

interface Product {
  id: string;
  name: string;
  sku: string;
  price_including_tax: number;
  price_excluding_tax: number;
  version: number;
}

interface Page<T> {
  data: T[];
  version: { max: number | null; min: number | null };
}

interface Sale {
  id: string;
  state: string;
  source: { register_id: string; outlet_id: string };
  totals: { price: number; price_incl_tax: number; tax: number };
  payments: Array<{ id: string; amount: number; type: { config_id: string; name: string } }>;
}

let baseUrl: string;
let base: ReturnType<typeof api>;
/** The seeded full-scope bearer, read off the control plane like a partner would. */
let asSeed: ReturnType<typeof api>;

/** The token endpoint's own encoding. `api()` sends JSON; this one does not. */
async function form(path: string, fields: Record<string, string>) {
  const response = await fetch(new URL(path, baseUrl), {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields).toString(),
  });
  const text = await response.text();
  return { status: response.status, text, body: text ? (JSON.parse(text) as TokenReply) : null };
}

function aSale(registerId: string, quantity = 1) {
  const price = 10.87;
  const tax = 1.63;
  return {
    state: "closed",
    source: { author_id: lightspeed.cashierUserId, register_id: registerId },
    customer_id: lightspeed.customerAdaId,
    line_items: [
      {
        product: { id: lightspeed.productTrailMixId },
        quantity,
        pricing: { price },
        tax: { id: lightspeed.taxId, amount: tax },
      },
    ],
    payments: [
      { type: { config_id: lightspeed.paymentTypeCashId }, amount: Number(((price + tax) * quantity).toFixed(2)) },
    ],
  };
}

/**
 * The documented scheme: HMAC-SHA256 over the webhook request body, hex.
 *
 * JUDGMENT, and the reason this helper exists here rather than being imported:
 * "hashing the webhook request body" is ambiguous over a form-encoded body
 * with JSON inside a field, and the docs page's own sample signature value is
 * neither hex nor base64. This unit signs the RAW BYTES as sent and encodes
 * hex; the other reading (the `payload` field's JSON alone) is
 * `vendorfake.lightspeed.signer.lightspeed_signature_over_payload`, and both
 * are published at `GET /__unit/info`. Sign the raw bytes: decoding the form
 * first changes the answer, which is what the third parity vector below shows.
 */
function lightspeedSignature(secret: string, rawBody: Buffer): string {
  return createHmac("sha256", secret).update(rawBody).digest("hex");
}

describe("lightspeed signature helper parity", () => {
  // The same four vectors `tests/unit/lightspeed/test_signer.py` pins against
  // `vendorfake.lightspeed.signer.lightspeed_signature`, secret
  // "unit-lightspeed-client-secret". Literals on both sides, so the two
  // implementations can actually disagree -- the guard the Toast signer grew
  // after konyklabs/vendorfake#49.
  test.each([
    [
      "payload=%7B%22a%22%3A1%7D&domain_prefix=x&environment=production",
      "ffd4b298008c45ecd13f69c0239aa15fe7612ab480d36fe158c5f9f882c15e02",
    ],
    ["", "aa81478116f860a004add86213a9c206e42009d4a11575f044a9c18f7715f737"],
    // Percent-encoded UTF-8, still signed as the bytes on the wire.
    ["payload=%7B%22n%22%3A%22caf%C3%A9%22%7D", "f76dfad7447259b5e6e70ad7d2241e1c2b6a6dd19588a78e36212bd4cdfde3e1"],
    // The OTHER reading's input: the payload JSON alone.
    ['{"a":1}', "44e095ac177198ba199189fe729f31162cc59f85df33e1e09613980c98a200a6"],
  ])("signs %s the way signer.py does", (body, expected) => {
    expect(lightspeedSignature(lightspeed.clientSecret, Buffer.from(body, "utf8"))).toBe(expected);
  });
});

beforeAll(async () => {
  baseUrl = inject("vendorfake").lightspeed;
  base = api(baseUrl);
  const published = await base.get<{ credentials: Array<{ headers: Record<string, string>; scopes: string[] }> }>(
    "/__unit/auth",
  );
  expect(published.status, published.text).toBe(200);
  const full = published.body.credentials.find((row) => row.scopes.includes("webhooks"));
  expect(full, "no full-scope credential published at /__unit/auth").toBeDefined();
  asSeed = api(baseUrl, full!.headers);
});

describe("lightspeed", () => {
  test("the code exchange answers the seven documented members, form-encoded", async () => {
    const redirected = await base.get("/connect", {
      query: {
        response_type: "code",
        client_id: lightspeed.clientId,
        redirect_uri: lightspeed.redirectUri,
        state: "ts-opaque-state",
        scope: "products:read sales:write",
      },
    });
    expect(redirected.status, redirected.text).toBe(302);
    const location = new URL(redirected.headers.get("location") ?? "");
    // The state comes back untouched: that is the whole of what it is for.
    expect(location.searchParams.get("state")).toBe("ts-opaque-state");
    const code = location.searchParams.get("code") ?? "";
    expect(code.length).toBeGreaterThan(0);

    const granted = await form(TOKEN_PATH, {
      grant_type: "authorization_code",
      code,
      client_id: lightspeed.clientId,
      client_secret: lightspeed.clientSecret,
      redirect_uri: lightspeed.redirectUri,
    });
    expect(granted.status, granted.text).toBe(200);
    const token = granted.body!;
    expect(Object.keys(token).sort()).toEqual(
      ["access_token", "domain_prefix", "expires", "expires_in", "refresh_token", "scope", "token_type"].sort(),
    );
    expect(token.token_type).toBe("Bearer");
    expect(token.domain_prefix).toBe(lightspeed.domainPrefix);
    expect(token.scope.split(" ")).toContain("products:read");
    // `expires` is a Unix timestamp and `expires_in` its seconds. The VALUE of
    // expires_in is not asserted: 86400 is the docs page's own example, not a
    // lifetime the vendor promises.
    expect(Number.isInteger(token.expires)).toBe(true);
    expect(Number.isInteger(token.expires_in)).toBe(true);
  });

  test("refreshing revokes the access token that was returned with the consumed refresh token", async () => {
    const redirected = await base.get("/connect", {
      query: {
        response_type: "code",
        client_id: lightspeed.clientId,
        redirect_uri: lightspeed.redirectUri,
        state: "ts-rotation",
        scope: "products:read",
      },
    });
    const code = new URL(redirected.headers.get("location") ?? "").searchParams.get("code") ?? "";
    const first = (
      await form(TOKEN_PATH, {
        grant_type: "authorization_code",
        code,
        client_id: lightspeed.clientId,
        client_secret: lightspeed.clientSecret,
        redirect_uri: lightspeed.redirectUri,
      })
    ).body!;

    const asFirst = api(baseUrl, { authorization: `Bearer ${first.access_token}` });
    expect((await asFirst.get(`${API}/products`)).status).toBe(200);

    const rotated = await form(TOKEN_PATH, {
      grant_type: "refresh_token",
      refresh_token: first.refresh_token,
      client_id: lightspeed.clientId,
      client_secret: lightspeed.clientSecret,
    });
    expect(rotated.status, rotated.text).toBe(200);
    const second = rotated.body!;
    expect(second.access_token).not.toBe(first.access_token);
    expect(second.refresh_token).not.toBe(first.refresh_token);

    // The line worth copying. The STATUS is this unit's choice -- no page says
    // what a revoked token gets -- so what is relied on is that it is refused
    // and that re-authenticating fixes it.
    const stale = await asFirst.get(`${API}/products`);
    expect(stale.status, stale.text).toBe(401);
    expect((await api(baseUrl, { authorization: `Bearer ${second.access_token}` }).get(`${API}/products`)).status).toBe(
      200,
    );
  });

  test("a forward sync walks version.max and stops on an empty page", async () => {
    const seen: Product[] = [];
    const versions: number[] = [];
    let after: number | null = null;
    for (let page = 0; page < 10; page += 1) {
      const query: Record<string, string> = { page_size: "2" };
      if (after !== null) query.after = String(after);
      const answered = await asSeed.get<Page<Product>>(`${API}/products`, { query });
      expect(answered.status, answered.text).toBe(200);
      if (answered.body.data.length === 0) {
        // The documented terminator, and the documented null pair with it.
        expect(answered.body.version).toEqual({ max: null, min: null });
        break;
      }
      seen.push(...answered.body.data);
      versions.push(...answered.body.data.map((row) => row.version));
      expect(answered.body.version.max).toBe(answered.body.data[answered.body.data.length - 1].version);
      after = answered.body.version.max;
    }
    expect(new Set(seen.map((row) => row.id)).size).toBe(seen.length);
    expect(versions).toEqual([...versions].sort((a, b) => a - b));
    expect(seen[0].id).toBe(lightspeed.productTrailMixId);
    // Money on the catalogue is a JSON number; on the register surface the same
    // API sends decimal strings. A client with one money parser meets both.
    expect(seen[0].price_including_tax).toBe(12.5);

    const fresh = await asSeed.get(`${API}/products`);
    // Documented as present on EVERY response: 300 x registers + 50, and the
    // shipped scenario has two registers.
    expect(fresh.headers.get("x-ratelimit-limit")).toBe("650");
    expect(Number(fresh.headers.get("x-ratelimit-remaining"))).toBeLessThan(650);
  });

  test("a sale carries its payments inline and a closed register refuses one", async () => {
    const rungUp = await asSeed.post<{ data: Sale }>(`${API}/sales`, aSale(lightspeed.registerMainId, 2));
    expect(rungUp.status, rungUp.text).toBe(200);
    const sale = rungUp.body.data;
    expect(sale.state).toBe("closed");
    // The outlet is derived from the register: a sale names the till, not the shop.
    expect(sale.source.outlet_id).toBe(lightspeed.outletMainId);
    // Totals are computed from the line items; the request schema has no place
    // to declare one.
    expect(sale.totals.price).toBe(21.74);
    expect(sale.totals.tax).toBe(3.26);
    expect(sale.totals.price_incl_tax).toBe(25.0);
    expect(sale.payments[0].type.config_id).toBe(lightspeed.paymentTypeCashId);

    const refused = await asSeed.post<{ error: { code: number; message: string }; message?: string }>(
      `${API}/sales`,
      aSale(lightspeed.registerSecondId),
    );
    expect(refused.status).toBeGreaterThanOrEqual(400);
    expect(refused.status).toBeLessThan(500);
    // The SHAPE is documented (`PaymentErrorResponse`). The code's VALUE is
    // not: `code` is "type: integer" with no enum, no example and no range, and
    // there is no error-codes page, so nothing here branches on it.
    expect(typeof refused.body.error).toBe("object");
    expect(Number.isInteger(refused.body.error.code)).toBe(true);
    expect(typeof refused.body.error.message).toBe("string");
    // A payment error is the nested shape, not the generalised {error, message}.
    expect(refused.body.message).toBeUndefined();
  });

  test("a sale.update delivery is form-encoded and its X-Signature verifies", async () => {
    const registered = await asSeed.post<{ data: { id: string; type: string } }>(`${API}/webhooks`, {
      active: true,
      type: "sale.update",
      url: inject("receiverUrl"),
    });
    expect(registered.status, registered.text).toBe(201);
    expect(registered.body.data.type).toBe("sale.update");

    // The same type and url again is the documented 409, whose body has exactly
    // one member -- the shape the Webhooks tag's own schema declares.
    const duplicate = await asSeed.post<{ error: string }>(`${API}/webhooks`, {
      active: true,
      type: "sale.update",
      url: inject("receiverUrl"),
    });
    expect(duplicate.status, duplicate.text).toBe(409);
    expect(duplicate.body).toEqual({ error: "A webhook with this type and URL already exists." });

    const before = deliveries(inject("receiverLog")).length;
    const created = await asSeed.post<{ data: Sale }>(`${API}/sales`, aSale(lightspeed.registerMainId));
    expect(created.status, created.text).toBe(200);
    expect((await base.post("/__unit/webhooks/drain", {})).status).toBe(200);

    const arrived = deliveries(inject("receiverLog")).slice(before);
    const delivery = arrived.find((row) => {
      const fields = new URLSearchParams(row.body.toString("utf8"));
      const payload = fields.get("payload");
      return payload !== null && (JSON.parse(payload) as Sale).id === created.body.data.id;
    });
    expect(delivery, "no sale.update delivery arrived").toBeDefined();

    // DOCUMENTED: application/x-www-form-urlencoded, required field `payload`,
    // "a JSON-encoded object with entity details".
    expect(header(delivery!, "content-type")).toBe("application/x-www-form-urlencoded");
    const fields = new URLSearchParams(delivery!.body.toString("utf8"));
    const entity = JSON.parse(fields.get("payload") ?? "{}") as Sale;
    expect(entity.state).toBe("closed");

    // DOCUMENTED: "X-Signature: signature=<value>,algorithm=HMAC-SHA256".
    const signature = header(delivery!, "x-signature") ?? "";
    expect(signature.endsWith(",algorithm=HMAC-SHA256")).toBe(true);
    const sent = Buffer.from(signature.split(",")[0].replace("signature=", ""));
    const computed = Buffer.from(lightspeedSignature(lightspeed.clientSecret, delivery!.body));
    expect(sent.length).toBe(computed.length);
    expect(timingSafeEqual(sent, computed)).toBe(true);
  });

  test("a rate limit is deterministic and Retry-After is a date, not a number", async () => {
    const armed = await base.post("/__unit/chaos/rules", {
      id: "ts-lightspeed-429",
      scope: "request",
      fault: "rate_limit",
      match: { route: `GET ${API}/payment_types` },
      when: { nth: [1] },
    });
    expect([200, 201]).toContain(armed.status);

    const limited = await asSeed.get<{ error: string }>(`${API}/payment_types`);
    expect(limited.status, limited.text).toBe(429);
    // DOCUMENTED, and the trap: an RFC 1123 HTTP-date, not delta-seconds.
    // `parseInt` on it is NaN, and a retry loop that trusts a number breaks.
    const retryAfter = limited.headers.get("retry-after") ?? "";
    expect(Number.isNaN(Number(retryAfter))).toBe(true);
    expect(retryAfter.endsWith(" GMT")).toBe(true);
    expect(Number.isNaN(Date.parse(retryAfter))).toBe(false);
    // The documented title. The sentence beside it is the injected fault's
    // here; the real limiter sends "Rate limiting enforced".
    expect(limited.body.error).toBe("Too Many Requests");
    expect(limited.headers.get("x-ratelimit-limit")).toBe("650");

    expect((await asSeed.get(`${API}/payment_types`)).status).toBe(200);
  });
});
