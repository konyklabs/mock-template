# Sixty-second quickstart

Serve the Square unit (defaults to the `full` profile on port 8080):

```sh
vendorfake serve --vendor square      # or: VENDORFAKE_VENDOR=square vendorfake serve
```

Every command names a vendor (`--vendor square|clover|toast`, or
`VENDORFAKE_VENDOR`); with none installed unit refuses and lists what it
found — it never guesses.

The default scenario is pre-seeded — a merchant, two locations, a small
catalog, two orders, and a full-scope access token
`EAAAl-unit-seeded-access-token-full-scopes` — so the first call needs no
setup at all:

```sh
curl -s http://localhost:8080/v2/locations \
  -H "Authorization: Bearer EAAAl-unit-seeded-access-token-full-scopes"
# -> {"locations": [{"id": "18YC4JDH91E1H", "name": "Grant Park", ...
```

Create and pay an order — a real state transition, not a canned response:

```sh
SEED=EAAAl-unit-seeded-access-token-full-scopes

curl -s -X POST http://localhost:8080/v2/orders \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' -d '{
  "idempotency_key": "order-quickstart-1",
  "order": {"location_id": "18YC4JDH91E1H",
            "line_items": [{"catalog_object_id": "2TZFAOHWGG7PAK2QEXWYPZSP", "quantity": "1"}]}
}'
# -> {"order": {"id": "CAIShCa1UcfqSiyfCVPNUIknxWD", "state": "OPEN", "version": 1, ...

curl -s -X POST http://localhost:8080/v2/orders/CAIShCa1UcfqSiyfCVPNUIknxWD/pay \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' \
  -d '{"idempotency_key": "pay-quickstart-1", "order_version": 1}'
# -> {"order": {"id": "CAIShCa1UcfqSiyfCVPNUIknxWD", "state": "COMPLETED", "version": 2, ...
```

Both transitions fired real deliveries at any subscriber registered for
`order.created` / `order.updated`, signed the way Square signs them and
retried on Square's documented schedule if nothing was listening. See the
per-vendor walkthroughs referenced from [the route reference]
(../reference/routes-square.md) for the OAuth dance, webhook subscriptions,
and the Clover and Toast equivalents.

The rest is discoverable, not memorised:

```sh
vendorfake routes --vendor square           # every route, with summaries
vendorfake info --vendor square             # capabilities, auth, signing scheme, fault catalogue
vendorfake openapi --vendor square          # an OpenAPI 3.1 document
```

or the generated [route](../reference/routes-square.md),
[profile](../reference/profiles.md) and [fault](../reference/faults.md)
reference pages.

Next: [which binding to use](bindings.md) for a test suite rather than a
terminal.
