# pytest consumer example

A restaurant-ordering integration's test suite, written the way you would write
it against the vendor sandboxes, run against `vendorfake` instead.
Twenty-eight tests, about three seconds:

```sh
cd examples/pytest-consumer
uv sync            # installs vendorfake from the checkout two levels up
uv run pytest
```

From your own project, depend on the public source instead of the path in
`pyproject.toml`:

```toml
dependencies = ["vendorfake @ git+https://github.com/konyklabs/vendorfake"]
```

## What is here

| File | Vendor | What it rehearses |
|---|---|---|
| `conftest.py` | – | One in-process unit per test (`vendorfake.testing.unit`) and a loopback webhook receiver |
| `test_square.py` | Square | OAuth exchange → create order → `POST /v2/payments` → read back COMPLETED; `GET /v2/catalog/list`; an `order.created` webhook verified with `verify_square_signature`; a 429 your retry loop survives with the idempotency key holding; a transient 401 that must not deactivate the connection |
| `test_clover.py` | Clover | Token exchange → atomic order → payment → `locked`/`PAID`; `items?expand=modifierGroups`; an `O:CREATE` webhook verified with `verify_clover_auth`; the documented 429 with `X-RateLimit-*`; a transient 401 |
| `test_toast.py` | Toast | Machine-client login → quote → order → payment, with the money asserted as decimal dollars (8.99 → 9.55, not 955); the published menu's prices; a bearer without `Toast-Restaurant-External-ID` refused, but not as bad auth; one payment still sent as a list; an `order_updated` webhook verified with `verify_toast_signature`; a 429 and a transient 401 |
| `test_lightspeed.py` | Lightspeed | Authorize stand-in → form-encoded code exchange → a refresh that **revokes the bearer it was issued with**; a forward sync over the version cursor (`after=<previous version.max>`, ending on an empty `data` with a null version pair); a sale with its payments inline and the `PaymentErrorResponse` refusal at a closed register; a `sale.update` delivery, form-encoded, verified with `verify_lightspeed_signature`; a 429 whose `Retry-After` is an RFC 1123 date rather than a number |
| `test_cross_vendor.py` | Square, Clover, Toast | One parametrized body over Square, Clover and Toast: the app credentials read through `seed.credentials`, a token obtained, and the refresh-versus-relogin branch taken from `credentials.grant`. No `isinstance` anywhere |
| `test_container.py` | all three | The order-and-pay path against the image, via Testcontainers, addressed entirely from the manifest. Skipped unless `VENDORFAKE_IMAGE` is set |

Against the container (needs Docker and a built image — `docker build -t vendorfake:verify ../..`):

```sh
VENDORFAKE_IMAGE=vendorfake:verify uv run --extra container pytest test_container.py
```

## The manifest, and why `test_container.py` hard-codes nothing

`test_container.py` holds no token, no merchant path and no guid. It reads
them from the manifest — one document carrying the credentials, the webhook
signing keys and every entity id, by collection. Against the fake it comes
from `GET /__unit/manifest`; against a real vendor sandbox there is no control
plane to ask, so a setup script writes the same shape to a file and
`VENDORFAKE_MANIFEST_SQUARE=/path/to/square.json` (or `_CLOVER`, `_TOAST`)
points the fixture at it. Below the fixture the two runs are the same requests.
See [the manifest reference](../../docs/reference/manifest.md).

On a Mac with colima, Testcontainers' reaper sidecar needs to be told where
the socket really is: prefix the command with
`TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`.

## Why in-process by default

`vendorfake.testing.unit("square")` builds the fake in the test process and
drives it through `httpx.Client` with no socket; the conformance suite proves
that binding answers byte-for-byte what the served one does. Webhooks still go
out over real HTTP to the receiver, so signature verification is exercised
on real bytes. When your service needs a URL, `vendorfake.testing.served()`
runs `vendorfake serve` in a child process and yields one.
