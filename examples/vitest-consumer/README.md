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

`setup/global.ts` starts one fake per vendor before the suite and stops them
after. Which way is chosen by the environment:

| | Command | Needs |
|---|---|---|
| Container (what CI should run) | `VENDORFAKE_IMAGE=vendorfake:verify npm test` | Docker, and the image (`docker build -t vendorfake:verify ../..`) |
| Child process | `npm test` | `vendorfake` installed in a Python: `VENDORFAKE_PYTHON`, else the checkout's `.venv`, else `python3` |

On a Mac with colima, testcontainers-node does not read Docker contexts, so
set both `DOCKER_HOST=unix://$HOME/.colima/default/docker.sock` and
`TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`.

## What is here

| File | What it rehearses |
|---|---|
| `setup/global.ts` | Starts the fakes, and a webhook receiver in this process (test workers cannot host one a container can reach); deliveries are appended to a JSONL file the tests read, raw bytes intact |
| `tests/square.test.ts` | Health; OAuth exchange → order → `POST /v2/payments` → COMPLETED; an `order.created` delivery whose HMAC is recomputed with `node:crypto` and compared with `timingSafeEqual`; a deterministic 429 |
| `tests/clover.test.ts` | Token exchange → atomic order → payment → `locked`/`PAID`; an `O:CREATE` delivery with `X-Clover-Auth`; a transient 401 |
| `tests/helpers.ts` | A `fetch` wrapper and the seeded credentials and ids |

In container mode the receiver is reached from inside the container through
testcontainers' host-port forwarding (`host.testcontainers.internal`), which
is why `TestContainers.exposeHostPorts` runs before the containers start.
