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

/** Everything the receiver has been sent so far, oldest first. */
export function deliveries(log: string): Delivery[] {
  let text: string;
  try {
    text = readFileSync(log, "utf8");
  } catch {
    return [];
  }
  return text
    .split("\n")
    .filter((line) => line.length > 0)
    .map((line) => {
      const record = JSON.parse(line) as { path: string; headers: Delivery["headers"]; bodyBase64: string };
      return { path: record.path, headers: record.headers, body: Buffer.from(record.bodyBase64, "base64") };
    });
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
