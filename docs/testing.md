# Testing

How this repository's suite is organised, what each tier protects, and the two
rules that decide whether a test exists. `tools/self-test.sh` runs all of it;
`uv run pytest` runs the pytest tiers, about four minutes.

## Tiers

| Tier | Where | Protects | Runs |
|---|---|---|---|
| **Vendor behaviour** | `tests/unit/<vendor>/` | What a vendor's surface answers: the documented status, body, header, state transition, signature, retry interval. Each test names the behaviour and, where the vendor documents it, the page. | every PR, ~6 s together |
| **Kernel** | `tests/unit/core/`, `tests/unit/asgi/` | The invariants the vendor-independent core states about itself: the pipeline order, the request lock, deterministic ids, the journal as event source, copy discipline, chaos selection, delivery and retry, config resolution, the ASGI adapter's byte-for-byte conversion. | every PR |
| **Consumer surface** | `tests/unit/testing/`, `tests/unit/test_*.py`, `tests/parity/` | What a consumer imports: `unit()`, `async_unit()`, `served()`, `serve_in_thread()`, the pytest plugin, seeds, the CLI, the public-API pin, and the parity of one behaviour across the three bindings. | every PR |
| **Conformance** | `tests/conformance/`, `src/vendorfake/conformance/checks/` | That any vendor module composes with the core: the C-numbered contracts run over every profile and binding, plus one mutant per contract proving the contract can go red. Says nothing about fidelity to the real vendor. | full self-test |
| **Fidelity** | `tests/fidelity/`, `src/vendorfake/<vendor>/fidelity/` | That answers validate against the vendor's published schema and that the documented corpus cases hold. See [fidelity](concepts/fidelity.md) for what that does and does not mean. | full self-test |
| **Integration** | `tests/integration/` (marker `integration`) | Only what needs a real socket: the served binding's parity with the in-process one, transport faults over uvicorn, form bodies through a real parser. | full self-test |

## The rule for a test to exist

A test names the behaviour it protects and fails when that behaviour is removed.
Its name is the claim; its assertion is on the observable a consumer or a vendor
document could disagree with; deleting the code it covers turns it red.

## The rule for deleting a test

A test goes when any one of these is true:

- it asserts shape or prose only: a docstring's words, a comment, a count in a
  sentence, the AST of the module, that a name exists;
- it would pass against a stub that echoes its own fixture: the expected value
  is computed by the code under test, or the mock returns what the assertion
  expects;
- it duplicates a branch another test already fails on, including a
  parametrisation whose extra cases exercise no new branch.

One exception is kept on purpose: the public-API contract test
(`tests/unit/test_public_api.py`) pins `__all__` of every public module, so a
change to what vendorfake promises is a reviewable diff rather than a surprise.

## Conventions

- Type narrowing is asserted statically in `tests/typing/narrowing.py` under
  `mypy --strict`, positives with `assert_type` and negatives with a
  `# type: ignore[<code>]` that `warn_unused_ignores` turns red the day the
  error stops occurring. No test shells out to mypy.
- A behaviour that differs between bindings is a parity case in `tests/parity/`,
  marked `xfail(strict=True)` with the finding it tracks until the contract
  holds, so the divergence is on record and its fix is a one-line change.
- Coverage is measured in the self-test's pytest step with a floor
  (`--cov-fail-under`) set at the measured number and only ever raised.
- Fidelity ledgers and conformance reports print in the self-test output; a
  claim about fidelity cites that output, never the test count.
