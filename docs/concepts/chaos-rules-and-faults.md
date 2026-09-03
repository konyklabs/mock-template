# Chaos rules and faults

Faults are **rules, not dice**: the same seed, the same profile and the
same rule fire the same fault on the same call, every run. That is what
makes "the third create fails" something a test can assert rather than
something that flakes.

## Arming a rule

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "flaky-create-order", "scope": "request", "fault": "rate_limit",
  "match": {"route": "POST /v2/orders"}, "when": {"nth": [1, 2]},
  "params": {"retry_after_seconds": 2}
}'
# the next two POST /v2/orders -> 429 with Retry-After; the third succeeds
```

or, in Python, `driver.add_chaos_rule({...})` — see [Driver](driver.md).
`GET /__unit/chaos` lists the active catalogue, each fault's `scope` and
`provenance` included; `POST /__unit/chaos/reset` disarms everything.
`driver.reset_chaos()` is the same call. The `chaos-demo`
[profile](profile.md) ships a preloaded set.

## The grammar

A rule has an `id`, a `scope` (`request` or `webhook`), a `fault` name,
and two optional clauses:

- **`match`** — which subjects the rule applies to: `route`
  (`"POST /v2/orders"`, `*`-glob'd, e.g. `"POST /v2/orders*"`), `path`,
  `method`, `capability`, `event_type` (webhook scope: a vendor event type,
  glob'd — `"O:*"`), `header` (compared lower-cased), `body_contains`.
  Every condition given is ANDed; an absent `match` applies to every
  subject in its scope — `{"scope": "request", "fault": "server_error"}`
  with no `match` means "fail everything".
- **`when`** — when a *matching* subject actually fires: `nth` (fire on
  these 1-based occurrences), `every` (fire on every Nth match, `>= 1` —
  `% 0` is refused at submission rather than left to produce a `NaN` or a
  500), `after` (skip this many matches first), `times` (stop firing after
  this many), `probability` (seeded-random, so even that run is replayable
  from `GET /__unit/info`). Conditions are ANDed; an absent `when` fires on
  every match.

Clover routes are matched with the tenant placeholder in the template,
e.g. `"route": "POST /v3/merchants/{mId}/orders"`.

## The fault catalogue

[The generated fault reference](../reference/faults.md) is the exact,
current list — every fault's scope, provenance, parameters and
description, from the same tables the control plane reads. Three families:

**Vendor faults** (`provenance: vendor`) reproduce something the vendor
itself documents: `rate_limit`, `server_error`, `unavailable`, `timeout`,
`token_expiry` (one 401 without touching the stored token — the transient
case a deactivate-on-401 handler gets wrong).

**Delivery faults** (`webhook` scope, `provenance: vendor`):
`webhook.duplicate`, `webhook.delay`, `webhook.out_of_order`,
`webhook.drop_ack`, `webhook.drop`.

**Transport faults** (`provenance: transport`) reproduce something no
vendor documents because it isn't a vendor behaviour at all — "the network
returned garbage": `malformed_body` (invalid JSON, an HTML error page, an
empty or truncated body), `body_mutation` (RFC 6901 JSON-pointer edits to
a successful response — remove a field, blank it, retype it), and three
that are not really a *response*: `connection_reset`, `empty_response`,
`slow_body`. See [Provenance labels](provenance-labels.md) for why this
distinction is published rather than left implicit. Every faulted
response, all three families included, carries `Vendorfake-Fault` and
`Vendorfake-Rule` headers, so a test can tell a faulted answer from a real
one without parsing the body.

## Rehearsing a timeout without waiting

The `timeout` fault's relationship to the [clock](clock.md) is its own
worked example — a real client-side timeout that costs a test a
millisecond rather than the delay named in the rule. See
[Clock → The clock and the timeout fault](clock.md#the-clock-and-the-timeout-fault).

## Rehearsing a 401 deactivation

`token_expiry` answers one request with the vendor's documented "expired
token" error **without touching the stored token** — the next call
succeeds again, which is exactly the transient case a
deactivate-on-connection-on-401 handler gets wrong:

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "expire-on-next-read", "scope": "request", "fault": "token_expiry",
  "match": {"route": "GET /v2/orders/{order_id}"}, "when": {"nth": [1]}
}'
```

For a *permanent* revocation, use the vendor's own revoke endpoint instead;
for an expiry a client's own clock would notice, advance a
[virtual clock](clock.md) past `expires_at`.
