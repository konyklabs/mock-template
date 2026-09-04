# Provenance labels

Every behaviour in this project is either something a vendor documents, or
something this project decided because the vendor's documentation is
silent. Provenance is the label that says which, and it is published in
three related places rather than left as something only the source code
remembers.

## In the source

At the site of any behaviour reproducing something a vendor publishes, the
docstring cites the page: marked `DOCUMENTED`. At the site of anything
invented because the vendor's documentation does not say, the docstring
says so and explains the choice: marked `JUDGMENT`. Behaviour at the
transport level that no vendor documents at all — because it is not a
vendor behaviour, it is what any HTTP dependency can do — is labelled
`provenance: transport` where the code exposes it. This is a source-reading
convention as much as a runtime one: grep any vendor surface module for
`JUDGMENT` to see every place this project decided something Square,
Clover or Toast never told it.

## On an error response: `status_provenance`

Every shaped error carries a `status_provenance` field — `"documented"` or
`"judgment"` — alongside `kind` and any extra `info`. By default it rides
as the `Vendorfake-Status-Provenance` response header (and
`Vendorfake-Error-Kind`, `Vendorfake-Error-Info`); a profile's
`errors.sidecar` (or `VENDORFAKE_ERROR_SIDECAR`) can move it into the body
instead, or carry it in both places:

```sh
curl -si -X POST http://localhost:8080/orders/v2/prices \
  -H 'Authorization: Bearer unit-seeded-toast-access-token-read-only' \
  -H "Toast-Restaurant-External-ID: $R" -d '...' | grep -iE '^(HTTP|vendorfake-)'
# HTTP/1.1 403 Forbidden
# vendorfake-error-kind: forbidden_scope
# vendorfake-status-provenance: documented
```

A `documented` status is the vendor's own answer for this failure — cite
the page. A `judgment` status is this project's choice where the vendor
never said. Toast's example: a missing scope answers **403** and that is
what the vendor documents; whether a malformed guid answers 400 with a
specific message is not documented anywhere and is therefore `judgment`,
labelled at the site in `toast/errors.py`.

## On a fault: `provenance: vendor | transport`

The [fault catalogue](chaos-rules-and-faults.md)'s own provenance field is
the same idea at a different layer. `GET /__unit/chaos` and
`GET /__unit/info` publish it per fault, and
[the generated fault reference](../reference/faults.md) lists it for every
built-in fault:

- `provenance: vendor` — a fault that reproduces a failure mode the vendor
  itself documents (`rate_limit`, `timeout`, `token_expiry`, ...). Square
  really does answer 429s; Clover really does time out.
- `provenance: transport` — a fault that reproduces something no vendor
  documents because it isn't a vendor behaviour at all: an HTML error page
  behind a 502, a response missing a documented field, a connection that
  drops mid-transfer. No vendor's API reference describes its own garbage,
  so nothing under `provenance: vendor` could ever model one — these five
  faults exist because that gap is worth rehearsing anyway.

## Why publish this instead of just deciding quietly

A consumer whose retry logic branches on a vendor's documented error code
needs to know whether this fake's answer for an edge case is something the
vendor promised, or something this project guessed at reasonably. Treating
a `judgment` response as gospel is a risk worth seeing on the wire, not
discovering the day the real vendor's sandbox disagrees.
