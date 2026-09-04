# Fidelity: checking the unit against the vendor's own documents

The [conformance suite](../reference/control-plane.md) proves the unit composes with the core. It says
nothing about whether the unit answers what the *vendor* says it answers.
That is a second, separate check (D-006) -- honest about its own reach: in
this repository's own vendor suites every JSON response is validated against
the vendor's published OpenAPI schema for that operation and status (a
consumer opts in with `served(validate=True)`, `vendorfake serve --validate`
or the `ValidatingClient`), and a corpus of
documented request/response facts is asserted, each citing the page and date
it was read from -- but nothing has yet been compared against a real vendor's
live traffic (the corpus schema carries a `recorded` provenance for exactly
that, and no case uses it yet), request bodies are validated only behind a
flag, and the state machines, cursors, error statuses and retry intervals a
vendor's own documentation leaves unstated are this project's reading of it,
labelled `JUDGMENT` at the site. Two halves:

- **Contract.** Every response the Square test suite produces is validated
  against Square's published OpenAPI document -- the schema for that
  operation and status -- and a mismatch fails the test that produced it.
  What ships is a scoped, prose-stripped extract of the document (only the
  operations the unit models and the schemas they reach) and a pin naming
  the upstream bytes it was cut from.
- **Behaviour.** A corpus of documented cases, each carrying the page it was
  read from, when, and whether the page states the fact (`documented`) or is
  silent and the unit chose (`judgment`). A schema is a type-and-enum oracle
  (an empty body passes most of them); the corpus is what asserts presence
  and value.

## Clover

Clover has no fidelity leg. It publishes no machine-readable specification,
so there is nothing to cut an extract from and nothing this page describes
applies to it -- Clover's test suite runs unvalidated, the same as any suite
that never wraps its client in this package's `ValidatingClient`.

Both render as one matrix per route. The target ships in the wheel, beside
the conformance ones; or point the corpus at a unit you already have running:

```sh
python -m vendorfake.fidelity report --target vendorfake.testing.fidelity:square_target
python -m vendorfake.fidelity run --target vendorfake.testing.fidelity:square_target --case orders.create.minimal
python -m vendorfake.fidelity run --base-url http://localhost:8080 --anchor vendorfake.square.fidelity
python -m vendorfake.fidelity pin --check --offline --target vendorfake.testing.fidelity:square_target
pytest --pyargs vendorfake.fidelity -p vendorfake.fidelity.plugin --fidelity-target vendorfake.testing.fidelity:square_target
```

The report's tail, as run on 2026-09-02:

```
POST /v2/orders                     | spec: operation CreateOrder | validated: 8 | documented: 5 | judgment: 1
GET /oauth2/authorize               | spec: EXCUSED (The seller-facing authorization page. It is a browser redirect flow ...)
...
routes: 35 (33 operation, 2 excused, 0 UNDECLARED)
cases: 13 passed, 0 failed (documented 12/12, judgment 1/1)
contract: fidelity: 26 validated, 0 deviated, 0 excused, 11 internal, 0 undeclared, 0 unmatched, 0 skipped non json over 12 routes
pin: https://raw.githubusercontent.com/square/connect-api-specification/master/api.json version 2.0 sha256 a0d0db22c202 fetched 2026-09-02
OK
```

Three words in that output carry the honesty of the whole thing. **EXCUSED**
is a vendor route the published document does not describe, served anyway,
with its reason in `square/fidelity/declaration.json`; **UNDECLARED** is a
route with neither a schema nor a reason, and the report exits non-zero on
one; **deviated** counts the errors a *declared deviation* absorbed -- a
place where the vendor's prose and the vendor's spec disagree and the unit
follows the observed API (Square's `VERSION_MISMATCH`, named on the
optimistic-concurrency page and absent from the `ErrorCode` enumeration).
Each deviation names its page.

**The request half is a flag, and it is off.** `ValidatingClient(...,
validate_requests=True)` also checks the body a call *sent*, against the
operation's own `requestBody` schema, whenever the unit answered 2xx: a
request the unit refused is not a fidelity question, but one it accepted and
the vendor's schema rejects is a fake more permissive than the API it stands
in for, and a consumer's test that passes here fails in production. It is
counted separately (`request validated`, `request deviated`), excused by the
same declared deviations, and off for every vendor in this distribution --
turning it on today is red for all three, mostly where a vendor's own document
does not model the sparse body its update endpoint takes, and partly where the
unit accepts a lowercase enum member the vendor spells in capitals.

**A served unit can validate too.** `vendorfake serve --validate` puts the
same check behind the socket: the binding hands each answered exchange to a
`ResponseObserver` after the unit has answered, so it can read the request and
the response and change neither, and a violation comes back to the caller as a
500 in the vendor's own error shape rather than as a log line nobody reads.
`served(validate=True)` passes the flag to the child, and the ledger summary
is printed on the way out. A vendor with no fidelity leg has no schema to check
against, so both refuse rather than serving a unit whose flag reads as
satisfied.

`pin --check --offline` is what CI runs: the committed extract and pin must
agree with each other and with the declaration. Whether *upstream* has moved
is a scheduled question, never a pull request's -- `pin` without `--offline`
re-fetches, re-cuts and rewrites both files, and the diff is the review.

**Toast's extract is fetched, never committed.** Toast's API terms do not
permit a copy of its specification files in a public repository, so
`toast/fidelity/` ships `declaration.json` (the seven published files it
names), `pin.json` (their sha256, size, version and fetch date -- facts, not
copies) and the corpus, and nothing else. The extract is cut at run time
from a fresh fetch into `~/.cache/vendorfake/fidelity/` (or
`$VENDORFAKE_FIDELITY_CACHE`); the wheel check fails if an extract ever
ships. A cold cache needs the network once:

```sh
python -m vendorfake.fidelity fetch  --target vendorfake.testing.fidelity:toast_target
python -m vendorfake.fidelity report --target vendorfake.testing.fidelity:toast_target
```

If upstream has moved since the pin, `fetch` says so on stderr and the run
uses the fresh document. While the pinned cut is cached (CI caches it keyed
on the pin), a vendor release changes nothing; after the cache is evicted the
suite validates against the fresh document and may go red for a reason that
is Toast's, not yours -- `fetch`'s `UPSTREAM MOVED` line is the tell, and
`pin` is the answer. That is the price of never committing the document.

## Recorded cases

A case's provenance answers "who says so". `documented` is the vendor's own
page; `judgment` is this project choosing where the page is silent; `recorded`
is the third answer, and the strictest: a real account actually answered this.

A recording is only evidence if it can be placed and reproduced, so a
`recorded` source carries five more fields, and the schema refuses the case
without any one of them:

| Field | What it pins down |
| --- | --- |
| `environment` | `sandbox` or `production` — which account answered |
| `api_version` | The version the exchange was made under |
| `recorded` | The day of the exchange, which is not the day the page was fetched |
| `script` | What produced it, so it can be produced again |
| `redaction` | What was replaced by `${any}`, `${re:...}` or `${vars.*}` |

The report counts the three separately, per route and in the totals, so a
matrix says how much of a vendor's surface is asserted from documents and how
much from traffic. **Nothing in this distribution is recorded yet:** every
`recorded` column reads `0/0`, which is the honest number.

A failing case now says *how* it diverged, not only that it did:
`status`, `header`, `value`, `missing`, `unexpected`, `capture`, `request`
(the case could not be asked at all) and `schema` (the contract leg refused
the body). `run` prints the class in the mark, `[FAIL missing]`, and a tally
under the totals.

Webhooks go the other way and no corpus case can reach them, so a captured
delivery is its own document — a `vendorfake.webhook-golden/1` file carrying
the URL, the exact bytes, the header names the signature occupies, and
`secret_env`, the name of the variable holding the signing key: a recording
never carries the key itself into a commit (a stub golden may inline `secret`). `verify_golden` hands the vendor's own signer the same three inputs
and compares:

```sh
python -m vendorfake.fidelity webhooks --target vendorfake.testing.fidelity:square_target --golden goldens/
```

A golden claiming `recorded` needs the same five fields a case does. The one
golden in the repository is a stub's output and says so in its `source.note`;
a fabricated recording would be worse than none, because the point of the
format is that the bytes are evidence.

## Running against a sandbox

`--base-url` used to mean "a vendorfake somebody else is running", because the
runner asked the control plane for the profile, the reset and the credentials.
Those three questions are now a `World`, and the control plane is only one
answer to them. The other is a [manifest](../reference/manifest.md), which a
sandbox account's setup script can write just as a unit can:

```sh
python -m vendorfake.fidelity run --manifest square-sandbox.json --anchor vendorfake.square.fidelity
```

The document supplies the profile, the credentials `$auth` resolves against,
and the address when `--base-url` is omitted. What it cannot supply is a
reset, so that world says so in a caveat the report prints: cases run against
whatever state the account holds, and one that mutates leaves it mutated.
Write cases for that world to be re-runnable, or point them at a throwaway
account.
