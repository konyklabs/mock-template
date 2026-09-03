# The `vendorfake` pytest plugin

Installing vendorfake registers exactly one `pytest11` entry point,
`vendorfake` (module `vendorfake.pytest`). It gives a consumer suite a marker
and three fixtures, and does nothing else: no `--conformance-*` options, no
session hook, no autouse fixture. A suite that never uses any of the three
fixtures runs identically whether the plugin is loaded or disabled with
`-p no:vendorfake`.

This is the second, smaller half of what installing the wheel used to
auto-load. Before 0.2, `vendorfake_conformance` was the `pytest11` entry
point, and it carried five `--conformance-*` options and a
`pytest_sessionfinish` hook into every pytest run that happened to have
vendorfake installed — whether or not that suite had ever heard of the
conformance registry. The conformance suite's pytest form still exists; it is
loaded explicitly now, with `-p vendorfake.conformance.plugin`. See
[the CLI reference](reference/cli.md) for `vendorfake conformance` /
`vendorfake-conformance`.

## The marker

```python
@pytest.mark.vendorfake(vendor, profile=None, env=None, seed=None, clock_start=None, unmatched=None, capabilities=None)
```

Every argument matches [`vendorfake.testing.unit`](start/bindings.md#in-process-sync)'s own:
`vendor` is required and positional (`"square"`, `"clover"` or `"toast"`);
`profile`, `env`, `seed`, `clock_start`, `unmatched` and `capabilities` are the
same keyword arguments `unit()` takes, with the same defaults and the same
meaning -- `unmatched` selects the unmatched-request policy (e.g.
`"vendor-404"`) and `capabilities` narrows to the smallest shipped profile
that is a superset of what it names.

`clock_start` carries the same precondition it does on `unit()`: it requires
a virtual clock, and setting it against the default real clock is a loud
refusal at fixture setup, not a mode switch. Pair it with `env`:

```python
@pytest.mark.vendorfake("square", clock_start="2026-01-01T00:00:00Z", env={"VENDORFAKE_CLOCK": "virtual"})
def test_a_token_expires_on_schedule(vendorfake_unit): ...
```

## The fixtures

`vendorfake_unit` is function-scoped and yields the
[`StartedUnit`](concepts/driver.md) the marker describes — built fresh per
test, the same grain as calling `unit()` directly, which is what makes
per-test `reset()` unnecessary here (a shared, session-scoped unit needs
it — see
[Sharing one unit across tests](concepts/chaos-rules-and-faults.md#sharing-one-unit-across-tests)). Requesting it without the
marker is a test author's mistake, not a missing precondition, so it fails
loudly and names the fix rather than skipping:

```python
import pytest


@pytest.mark.vendorfake("square")
def test_an_order_is_created(vendorfake_unit):
    seed = vendorfake_unit.seed
    created = vendorfake_unit.client.post(
        "/v2/orders",
        headers=seed.auth,
        json={
            "idempotency_key": "ticket-1",
            "order": {
                "location_id": seed.location_id,
                "line_items": [{"catalog_object_id": seed.tea_mug_variation_id, "quantity": "1"}],
            },
        },
    )
    assert created.status_code == 200
```

`vendorfake_async_unit` yields the same kind of
[`StartedUnit`](concepts/driver.md) as `vendorfake_unit`, for a test that
drives it through `async_client` instead of `client`:

```python
import pytest


@pytest.mark.vendorfake("clover")
async def test_items_are_readable(vendorfake_async_unit):
    seed = vendorfake_async_unit.seed
    answered = await vendorfake_async_unit.async_client.get(seed.path("/items"), headers=seed.auth)
    assert answered.status_code == 200
```

### Narrowing the seed under a type checker

Both fixtures are declared `StartedUnit[Seed]`: the vendor is a runtime
marker argument, so there is nothing for a checker to narrow on and
`vendorfake_unit.seed.merchant_id` is a type error even on a `"clover"`
marker. Three ways out, in order of preference:

- Stay on the structural `Seed`. `seed.credentials` (`app_id`, `app_secret`,
  `grant`) and `seed.token` (`access_token`, `refresh_token: str | None`,
  `tenant_id`) are the cross-vendor views, and a test parametrized over
  vendors should need nothing else.
- Narrow once, at the top of the test, with an assertion the checker
  understands and the runtime enforces:

  ```python
  from vendorfake.testing import CloverSeed


  @pytest.mark.vendorfake("clover")
  def test_the_merchant_is_seeded(vendorfake_unit):
      seed = vendorfake_unit.seed
      assert isinstance(seed, CloverSeed)
      assert seed.merchant_id  # narrowed
  ```

- Call `unit("clover")` from a fixture of your own instead: the literal
  overload yields `StartedUnit[CloverSeed]` with no assertion at all.

`typing.cast` works too, but it is the one option that can be wrong without
anything failing; prefer the `isinstance`, which costs one line and cannot
lie.

The fixture function itself is a plain, synchronous `def`, not `async def` —
it yields an object that owns an `httpx.AsyncClient` rather than being a
coroutine itself. That is what lets it work under `pytest-asyncio` (strict or
auto mode) and under `anyio`'s plugin without this package depending on
either or guessing which one is installed; an `async def` fixture would need
each runner's own decorator, and the two are not interchangeable. See
[Recipes → Async pytest](async-consumers.md) for the full picture, including
the `@pytest.mark.anyio` (or `pytest-asyncio`) marker the test function itself
still needs.

`vendorfake_webhook_receiver` is function-scoped and yields the same
`WebhookReceiver` object `vendorfake.testing.webhook_receiver()` does — a real
HTTP endpoint on loopback, for the other half of a webhook test:

```python
@pytest.mark.vendorfake("square")
def test_a_webhook_is_delivered_and_verifies(vendorfake_unit, vendorfake_webhook_receiver):
    vendorfake_unit.subscribe(vendorfake_webhook_receiver.url, ["order.created"], signature_key="k")
    ...
    vendorfake_unit.drain()
    (delivery,) = vendorfake_webhook_receiver.received
```

## Choosing between this and `vendorfake.testing.unit()`

Either is a complete way to hold a unit; nothing about the fixtures is more
capable than the function. Reach for the marker when a test needs exactly one
unit and no fixture composition of your own — it is one line instead of a
`with` block. Reach for `unit()` directly (typically from your own fixture,
the way [`examples/pytest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/pytest-consumer)
does) when a
test needs two units, a non-default sink, or to hold the unit across more than
one test via a fixture you control the scope of.
