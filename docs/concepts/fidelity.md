# Fidelity: checking the unit against the vendor's own documents

The [conformance suite](../reference/control-plane.md) proves the unit composes with the core. It says
nothing about whether the unit answers what the *vendor* says it answers.
That is a second, separate check (D-006), with two halves:

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
