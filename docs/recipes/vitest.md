# Vitest

[`examples/vitest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/vitest-consumer)
is a complete, runnable project: the same suite as
[`examples/pytest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/pytest-consumer),
in TypeScript, sharing nothing with the fake but HTTP. `npm install && npm
test` inside it.

## Two ways to start it, chosen by an environment variable

A Vitest `globalSetup` starts one unit per vendor before the suite and stops
them after — [served](../start/bindings.md#served), not in-process, because
the code under test is a separate language:

```sh
npm test                       # `vendorfake serve` as a child process (needs Python + vendorfake on PATH)
VENDORFAKE_IMAGE=vendorfake npm test   # the container, via testcontainers
```

The child-process mode is for a laptop without Docker
(`VENDORFAKE_PYTHON`, else the repository's `.venv`, else `python3` on
`PATH`); the container mode is what CI should run. Both hand every test file
the same thing — a base URL per vendor — through Vitest's `inject()`:

```ts
declare module "vitest" {
  export interface ProvidedContext {
    vendorfake: Record<"square" | "clover", string>;
  }
}

// in globalSetup:
project.provide("vendorfake", { square: squareUrl, clover: cloverUrl });

// in a test file:
const url = inject("vendorfake").square;
```

## The webhook receiver

Vitest runs test files in worker processes, so a receiver started inside a
`test()` cannot be reached by a container. `globalSetup` starts one HTTP
receiver instead, before any test file runs, and appends every delivery —
headers and the raw body, base64-encoded — to a JSONL file the tests read
back. In container mode the fake reaches the receiver through
Testcontainers' host-port forwarding
(`TestContainers.exposeHostPorts` → `host.testcontainers.internal`); in
subprocess mode it is plain loopback.

## A test

```ts
import { beforeAll, describe, expect, inject, test } from "vitest";
import { api } from "./helpers";

let base: ReturnType<typeof api>;

beforeAll(() => {
  base = api(inject("vendorfake").square);
});

describe("square", () => {
  test("the unit is healthy", async () => {
    const health = await base.get<{ status: string; vendor: string }>("/__unit/health");
    expect(health.status).toBe(200);
    expect(health.body.vendor).toBe("square");
  });
});
```

Signature verification is re-implemented in `node:crypto` in the example
rather than shipped as a helper, deliberately: it is the second, independent
implementation checking the same bytes vendorfake's own Python signer
produced, which is a stronger proof than importing vendorfake's answer and
comparing it to itself.
