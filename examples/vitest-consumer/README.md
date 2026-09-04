# Vitest consumer example

The same restaurant-ordering integration as [`../pytest-consumer`](../pytest-consumer),
in TypeScript, over HTTP. Nothing here imports `vendorfake`: the suite shares
only the protocol with the fake, which is what makes it independent
verification -- a second language, a second HTTP client, a second HMAC
(`node:crypto`).

```sh
cd examples/vitest-consumer
npm install
npm test
```

`setup/global.ts` starts one fake per vendor -- Square, Clover, Toast and
Lightspeed -- before the suite and stops them after. Which way is chosen by the environment:

| | Command | Needs |
|---|---|---|
| Container (what CI should run) | `VENDORFAKE_IMAGE=vendorfake:verify npm test` | Docker, and the image (`docker build -t vendorfake:verify ../..`) |
| Child process | `npm test` | `vendorfake` installed in a Python: `VENDORFAKE_PYTHON`, else the checkout's `.venv`, else `python3` |

On a Mac with colima, testcontainers-node does not read Docker contexts, so
set both `DOCKER_HOST=unix://$HOME/.colima/default/docker.sock` and
`TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`. If the reaper
then fails to start — it cannot always bind that socket, and the error names
`operation not supported` — add `TESTCONTAINERS_RYUK_DISABLED=true`, and clean
up any containers it would have reaped yourself.

## What is here

| File | What it rehearses |
|---|---|
| `setup/global.ts` | Starts the fakes, and a webhook receiver in this process (test workers cannot host one a container can reach); deliveries are appended to a JSONL file the tests read, raw bytes intact |
| `tests/square.test.ts` | Health; OAuth exchange → order → `POST /v2/payments` → COMPLETED; an `order.created` delivery whose HMAC is recomputed with `node:crypto` and compared with `timingSafeEqual`; a deterministic 429 |
| `tests/clover.test.ts` | Token exchange → atomic order → payment → `locked`/`PAID`; an `O:CREATE` delivery with `X-Clover-Auth`; a transient 401 |
| `tests/toast.test.ts` | Machine-client login → prices → order → payment, with the money asserted as decimal dollars on the wire (8.99 → 9.55, not 955); a bearer without `Toast-Restaurant-External-ID` refused, but not as bad auth; a missing bearer as the documented 401; an `order_updated` delivery whose `Toast-Signature` is recomputed with `node:crypto`; a deterministic 429 |
| `tests/lightspeed.test.ts` | Authorize stand-in → **form-encoded** code exchange at `/api/1.0/token` → a refresh that revokes the bearer it was issued with; a forward sync over the version cursor; a sale with its payments inline and the `PaymentErrorResponse` refusal at a closed register; a `sale.update` delivery whose `X-Signature` hex HMAC is recomputed with `node:crypto`, with four parity vectors pinned as literals against `tests/unit/lightspeed/test_signer.py`; a 429 whose `Retry-After` is an RFC 1123 date, so `Number()` on it is `NaN` |
| `tests/helpers.ts` | A `fetch` wrapper and the seeded credentials and ids |

In container mode the receiver is reached from inside the container through
testcontainers' host-port forwarding (`host.testcontainers.internal`), which
is why `TestContainers.exposeHostPorts` runs before the containers start.
