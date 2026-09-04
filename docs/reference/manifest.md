# Manifest

One document carrying everything a test needs to *address* a unit: the
credentials that work, the webhook signing keys, and every entity id by
collection. Nothing in it is answerable only by a fake, which is the point —
the same shape describes a vendor sandbox account.

## The shape

```json
{
  "schema": "vendorfake.manifest/1",
  "vendorfake": "0.5.0",
  "vendor": "square",
  "profile": "full",
  "base_url": "http://localhost:8080",
  "credentials": [ /* exactly what GET /__unit/auth returns under `credentials` */ ],
  "webhooks": { "signature_keys": ["…"] },
  "ids": { "<collection>": ["<id>", "…"] }
}
```

| Field | What it carries |
| --- | --- |
| `schema` | `vendorfake.manifest/1`. Branch on it; a later shape gets a later number |
| `vendorfake` | The installed distribution's version, or `unknown` in a tree with no metadata |
| `vendor`, `profile` | Which vendor surface and which profile answered |
| `base_url` | The address the caller reached the unit at, or `null` |
| `credentials` | The `credentials` array of [`GET /__unit/auth`](control-plane.md): a label, a mode, the headers to send, the scopes granted, a summary |
| `webhooks.signature_keys` | Every distinct signing key on a seeded subscriber, in seed order |
| `ids` | Every collection in the store, ids only, in stored order |

`base_url` is the one field a unit cannot infer: behind a container port
mapping it does not know its own address. The served route fills it from the
request's `Host` header (with `X-Forwarded-Proto` where a proxy set one); the
command line fills it from `--base-url`, and leaves it `null` without.

## The two ways to get it

**Over the wire**, from a running unit:

```sh
curl -s http://localhost:8080/__unit/manifest
```

**In process**, with no server, which is what a compose setup step or a CI job
writes to a file:

```sh
vendorfake manifest --vendor square --base-url http://localhost:8080 > square.json
```

`--json` is implicit: the output *is* the document, and nothing else goes to
stdout. Both are built by one function, so the two cannot drift.

## The two worlds

The manifest exists so one end-to-end script runs unchanged against the fake
and against a real vendor sandbox.

- **Against a unit**, the script reads the manifest and then the vendor's own
  API. It never reads the control plane — no `/__unit/state`, no
  `/__unit/clock/advance` — because none of that exists in the other world.
- **In the deployed world**, a setup script writes the same shape from the
  sandbox account: the credentials it provisioned, the webhook keys it
  registered, the ids of the fixtures it created. The end-to-end script reads
  that file and cannot tell the difference.

`examples/pytest-consumer/test_container.py` is written to that rule.
`VENDORFAKE_MANIFEST_SQUARE` (or `_CLOVER`, `_TOAST`) names a file and the
fixture reads it instead of asking the control plane. That variable belongs to
the example's own test suite, not to vendorfake: nothing in the distribution
reads it.

The fidelity corpus reads this document too: `vendorfake.fidelity run
--manifest square.json` takes the profile, the credentials and the address
from it instead of the control plane, which is what lets the same cases run
against a sandbox account — see [fidelity](../concepts/fidelity.md).

A script that needs chaos, a virtual clock or a state digest is a *unit* test
of your error handling, and belongs in the control plane's world. Keep the two
suites apart rather than making one script conditional.

## A real document

`vendorfake manifest --vendor square --base-url http://localhost:8080`,
abbreviated — scope lists and every collection past two ids are elided with
`…`. The token expiries are relative to the moment the unit started, so yours
will read a month ahead of your own clock rather than these dates:

```json
{
  "schema": "vendorfake.manifest/1",
  "vendorfake": "0.5.0",
  "vendor": "square",
  "profile": "full",
  "base_url": "http://localhost:8080",
  "credentials": [
    {
      "label": "client-secret",
      "mode": "client-secret",
      "headers": {"authorization": "Client sandbox-sq0csb-unit-square-secret"},
      "scopes": ["MERCHANT_PROFILE_READ", "ORDERS_READ", "…"],
      "summary": "The application secret, which POST /oauth2/revoke authenticates with."
    },
    {
      "label": "tok_seed_full",
      "mode": "bearer",
      "headers": {"authorization": "Bearer EAAAl-unit-seeded-access-token-full-scopes"},
      "scopes": ["MERCHANT_PROFILE_READ", "ORDERS_READ", "…"],
      "summary": "Access token for merchant MLQW2MYBY81PZ, expiring 2026-10-04T15:57:09Z."
    },
    {
      "label": "tok_seed_readonly",
      "mode": "bearer",
      "headers": {"authorization": "Bearer EAAAl-unit-seeded-access-token-read-only"},
      "scopes": ["MERCHANT_PROFILE_READ", "ORDERS_READ", "…"],
      "summary": "Access token for merchant MLQW2MYBY81PZ, expiring 2026-10-04T15:57:09Z."
    }
  ],
  "webhooks": {"signature_keys": []},
  "ids": {
    "merchants": ["MLQW2MYBY81PZ"],
    "locations": ["18YC4JDH91E1H", "057P5VYJ4A5X1"],
    "catalog_objects": ["W62UWFY35CWMYGVWK6TWJDNI", "2TZFAOHWGG7PAK2QEXWYPZSP", "…"],
    "orders": ["CAISENgvlJ6jLWAzERDzjyHVybY", "CAISEM82RcpmcFBM0TfOyiHV3es"],
    "loyalty_programs": ["d619f755-2d17-41f3-990d-c04ecedd64dd"],
    "loyalty_accounts": ["79b807d2-d786-46a9-933b-918028d7a8c5", "5f2b7c14-9a3e-4d68-8c01-7d54c2a90b31"],
    "inventory_counts": ["2TZFAOHWGG7PAK2QEXWYPZSP:18YC4JDH91E1H", "HURXQOOAIC4IZSI2BEXQRYFY:18YC4JDH91E1H"],
    "tokens": ["tok_seed_full", "tok_seed_readonly"]
  }
}
```

Square seeds no webhook subscriber, so `signature_keys` is empty there; Clover,
Toast and Lightspeed each seed one. Every credential above is scenario data
with no real-world counterpart — that is why publishing them is safe.
