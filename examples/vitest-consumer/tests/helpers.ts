/**
 * A thin fetch wrapper and the seeded scenario, shared by both suites.
 *
 * Only `fetch` and the standard library: this suite's value is that it shares
 * nothing with the fake but HTTP, so it verifies the fake the way a second
 * implementation would.
 */

import { readFileSync } from "node:fs";

export interface Reply<T = unknown> {
  status: number;
  headers: Headers;
  body: T;
  text: string;
}

export function api(baseUrl: string, defaultHeaders: Record<string, string> = {}) {
  const call = async <T = unknown>(
    method: string,
    path: string,
    options: { json?: unknown; headers?: Record<string, string>; query?: Record<string, string> } = {},
  ): Promise<Reply<T>> => {
    const url = new URL(path, baseUrl);
    for (const [key, value] of Object.entries(options.query ?? {})) url.searchParams.set(key, value);
    const headers: Record<string, string> = { ...defaultHeaders, ...(options.headers ?? {}) };
    if (options.json !== undefined) headers["content-type"] = "application/json";
    const response = await fetch(url, {
      method,
      headers,
      body: options.json === undefined ? undefined : JSON.stringify(options.json),
      redirect: "manual",
    });
    const text = await response.text();
    let body: unknown = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = text;
    }
    return { status: response.status, headers: response.headers, body: body as T, text };
  };
  return {
    get: <T = unknown>(path: string, options?: Parameters<typeof call>[2]) => call<T>("GET", path, options),
    post: <T = unknown>(path: string, json?: unknown, options?: Parameters<typeof call>[2]) =>
      call<T>("POST", path, { ...options, json }),
  };
}

export interface Delivery {
  path: string;
  headers: Record<string, string | string[] | undefined>;
  body: Buffer;
}

/**
 * Everything the receiver has been sent so far, oldest first.
 *
 * The receiver appends whole lines, but a read can still land mid-write on
 * the last one; a line that does not parse is treated as not yet there, the
 * same way a missing file is. Complete records are never affected because
 * every earlier line ends with a newline the writer already flushed.
 */
export function deliveries(log: string): Delivery[] {
  let text: string;
  try {
    text = readFileSync(log, "utf8");
  } catch {
    return [];
  }
  const records: Delivery[] = [];
  for (const line of text.split("\n")) {
    if (line.length === 0) continue;
    let record: { path: string; headers: Delivery["headers"]; bodyBase64: string };
    try {
      record = JSON.parse(line);
    } catch {
      continue; // torn line: the writer has not finished it yet
    }
    records.push({ path: record.path, headers: record.headers, body: Buffer.from(record.bodyBase64, "base64") });
  }
  return records;
}

export function header(delivery: Delivery, name: string): string | undefined {
  const value = delivery.headers[name.toLowerCase()];
  return Array.isArray(value) ? value[0] : value;
}

/** The shipped scenarios. Same values the README's curl commands use. */
export const square = {
  applicationId: "sandbox-sq0idb-unit-square-application",
  applicationSecret: "sandbox-sq0csb-unit-square-secret",
  accessToken: "EAAAl-unit-seeded-access-token-full-scopes",
  merchantId: "MLQW2MYBY81PZ",
  locationId: "18YC4JDH91E1H",
  teaMugVariationId: "2TZFAOHWGG7PAK2QEXWYPZSP",
};

export const clover = {
  clientId: "UNITCLOVERAPP",
  clientSecret: "unit-clover-app-secret",
  accessToken: "unit-seeded-clover-access-token-full-permissions",
  merchantId: "HRVSTRYE12345",
  orderTypeDineInId: "KFRPRVCZ73JHM",
  itemEspressoId: "ESPRESSO00300",
  itemCroissantId: "CROISSANT0450",
  modifierOatId: "MODIFIEROAT01",
  serviceChargeId: "SVCCHARGE0001",
  tenderExternalId: "TENDEREXTRN01",
  employeeBaristaId: "EMPLBARISTA01",
  path: (suffix = "") => `/v3/merchants/HRVSTRYE12345${suffix}`,
};

/**
 * Lightspeed's ids ARE this suite's to know: they are the seeded scenario's,
 * documented on the Lightspeed page and stable across units by the same
 * determinism contract every other vendor's are. The one thing not listed is a
 * bearer -- the tests read a full-scope credential off `/__unit/auth`, because
 * a token is minted and a seeded id is not.
 */
export const lightspeed = {
  clientId: "unit-lightspeed-client-id",
  clientSecret: "unit-lightspeed-client-secret",
  redirectUri: "https://consumer.example/callback",
  domainPrefix: "unit-lightspeed",
  cashierUserId: "1a000000-0000-1000-8000-000000000001",
  taxId: "1a000000-0000-1000-8000-0000000000a1",
  outletMainId: "1a000000-0000-1000-8000-000000000101",
  registerMainId: "1a000000-0000-1000-8000-000000000201",
  registerSecondId: "1a000000-0000-1000-8000-000000000202",
  paymentTypeCashId: "1a000000-0000-1000-8000-000000000301",
  productTrailMixId: "1a000000-0000-1000-8000-000000000701",
  customerAdaId: "1a000000-0000-1000-8000-000000000911",
};

/**
 * Toast's are only the two readable strings a partner is issued. Everything
 * else it needs is a guid, and a guid is the fake's to publish rather than
 * this suite's to know: the restaurant comes from `/__unit/auth`, the menu and
 * configuration ids from the menu and the configuration lists.
 */
export const toast = {
  clientId: "unit-toast-client-id",
  clientSecret: "unit-toast-client-secret",
  /** Toast scopes by header, not by a path segment; lowercased to index `headers`. */
  restaurantHeader: "toast-restaurant-external-id",
};
