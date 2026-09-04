# Playwright

There is no shipped Playwright example in this repository yet (the Vitest
one under
[`examples/vitest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/vitest-consumer)
is the closest runnable reference for the TypeScript side) — the pattern
below is the same [served](../start/bindings.md#served) binding Vitest's
`globalSetup` uses, adapted to Playwright's own setup hooks.

## Starting the unit once, for the whole run

Playwright's `globalSetup`/`globalTeardown` run once per test run, outside
any worker process — the same place Vitest's `globalSetup` starts a unit,
and for the same reason: a browser test drives your application, not
vendorfake directly, so the fake only needs to exist somewhere your
application's configuration can reach.

```ts
// playwright.config.ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  globalSetup: require.resolve("./global-setup"),
  globalTeardown: require.resolve("./global-teardown"),
  webServer: {
    // your application under test, pointed at the URL global-setup wrote out
    command: "npm run start",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
  },
});
```

```ts
// global-setup.ts
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

export default async function globalSetup() {
  const proc = spawn("vendorfake", ["serve", "--vendor", "square", "--port", "0"], { stdio: "pipe" });
  const url = await new Promise<string>((resolve) => {
    proc.stdout!.on("data", (chunk: Buffer) => {
      const m = /listening on (http:\/\/\S+)/.exec(chunk.toString());
      if (m) resolve(m[1]);
    });
  });
  writeFileSync(".vendorfake-pid", String(proc.pid));
  writeFileSync(".vendorfake-url", url);
  process.env.SQUARE_BASE_URL = url; // whatever your app reads
}
```

`--port 0` picks a free port and prints it, which is what makes this safe to
run in parallel CI shards without a port collision — see
[the CLI reference](../reference/cli.md#vendorfake-serve). Point your
application's Square base URL configuration at the printed address before
`webServer` starts it.

## A browser test

The test itself never talks to vendorfake directly — it drives the browser
against your application, and your application talks to the fake:

```ts
import { expect, test } from "@playwright/test";

test("checkout completes and the order shows PAID", async ({ page }) => {
  await page.goto("/checkout");
  await page.getByRole("button", { name: "Pay" }).click();
  await expect(page.getByText("Payment complete")).toBeVisible();
});
```

Assert on the fake's own state from a Playwright `request` fixture when a
test needs to confirm the *backend* transition, not just the UI:

```ts
test("the order is COMPLETED in vendorfake", async ({ request }) => {
  const orderId = /* however your app exposes it */ "CAIShCa1UcfqSiyfCVPNUIknxWD";
  const order = await request.get(`${process.env.SQUARE_BASE_URL}/v2/orders/${orderId}`, {
    headers: { authorization: "Bearer EAAAl-unit-seeded-access-token-full-scopes" },
  });
  expect((await order.json()).order.state).toBe("COMPLETED");
});
```

## Faults in an end-to-end run

Arm a chaos rule from `globalSetup` (or a `beforeEach` hook, against the
control plane's `POST /__unit/chaos/rules`) before the browser test that
needs to see a retry banner or a rate-limit toast — see
[Concepts → Chaos rules and faults](../concepts/chaos-rules-and-faults.md).
`POST /__unit/chaos/reset` between tests keeps one test's rule from leaking
into the next, and `POST /__unit/state/reset` beside it puts single-use
state (Clover's refresh token) back for the next test — see
[Sharing one unit across tests](../concepts/chaos-rules-and-faults.md#sharing-one-unit-across-tests).
