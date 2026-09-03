# The `vendorfake` pytest plugin

Installing vendorfake registers exactly one `pytest11` entry point,
`vendorfake` (module `vendorfake.pytest`). It gives a consumer suite a marker
and two fixtures, and does nothing else: no `--conformance-*` options, no
session hook, no autouse fixture. A suite that never uses either fixture runs
identically whether the plugin is loaded or disabled with `-p no:vendorfake`.

This is the second, smaller half of what installing the wheel used to
auto-load. Before 0.2, `vendorfake_conformance` was the `pytest11` entry
point, and it carried five `--conformance-*` options and a
`pytest_sessionfinish` hook into every pytest run that happened to have
vendorfake installed — whether or not that suite had ever heard of the
conformance registry. The conformance suite's pytest form still exists; it is
loaded explicitly now, with `-p vendorfake.conformance.plugin`. See "Running
the conformance suite against your unit" in the [README](../README.md).

## The marker

```python
@pytest.mark.vendorfake(vendor, profile="full", env=None, seed=None, clock_start=None)
```

Every argument matches [`vendorfake.testing.unit`](../README.md#pytest)'s own:
`vendor` is required and positional (`"square"`, `"clover"` or `"toast"`);
`profile`, `env`, `seed` and `clock_start` are the same keyword arguments
`unit()` takes, with the same defaults.

## The fixtures

`vendorfake_unit` is function-scoped and yields the
[`StartedUnit`](../README.md#pytest) the marker describes — built fresh per
test, the same grain as calling `unit()` directly. Requesting it without the
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
the way [`examples/pytest-consumer`](../examples/pytest-consumer) does) when a
test needs two units, a non-default sink, or to hold the unit across more than
one test via a fixture you control the scope of.
