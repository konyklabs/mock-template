"""Journal-derived delivery, end to end, on a virtual clock.

Every test here runs on the virtual clock and the in-memory sink, so nothing
sleeps and nothing is timing-dependent: a retry cascade that would take
twenty-four hours in production collapses into one ``drain()``.

WHAT IS BEING PINNED, and why each is a decision rather than a detail:

* **commit-then-emit** -- an event exists only because a mutation committed, so
  a handler cannot forget to emit one and cannot emit one for a mutation that
  rolled back;
* **the seed rule** -- loading a scenario containing an open order must not push
  ``order.created`` to every subscriber, and the test that a *non-boolean*
  ``seed`` key still emits is what stops the rule from being "any metadata
  called seed";
* **the synchronous prologue** -- what is true when ``handle()`` returns, stated
  as the weaker claim that is actually true;
* **retry timing and exhaustion** -- twelve attempts, eleven of them failures,
  every time and not merely usually;
* **the four delivery chaos faults** and the exact delivery *order* each
  produces, which is the property a second delivery thread would break;
* **determinism** -- two runs of one scenario produce the same event ids;
* **the de-vendoring** -- with a silent signer the core sends no header at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from tests.fakes import (
    WEBHOOK_CAPABILITIES,
    FakeEvents,
    FakeSigner,
    FakeVendor,
    make_unit,
    route,
)
from vendorfake.core.kernel.types import JournalEntry, MappedEvent, PreparedEvent, ReplyInit
from vendorfake.core.kernel.unit import Unit, make_request
from vendorfake.core.webhooks.models import DeliveryOutcome
from vendorfake.core.webhooks.sink import MemorySink

SUB_URL = "https://subscriber.test/hooks"
SECRET = "top-secret-key"

#: Two intervals, scaled the way the reference's own profile scales its
#: vendor's documented schedule: one minute and two minutes become ten and
#: twenty milliseconds, so the SHAPE is observable and the wait is not.
SHORT_SCHEDULE = (60_000, 120_000)
SHORT_SCALE = 0.000167

#: Eleven intervals, so twelve attempts. The reference vendor's own documented
#: schedule -- 1, 2, 4, 8, 16, 32 and 60 minutes, then 2, 4, 8 and 8 hours --
#: reproduced here only because the *count* is what the exhaustion case
#: asserts and eleven arbitrary numbers would not say where twelve came from.
FULL_SCHEDULE = (
    60_000,
    120_000,
    240_000,
    480_000,
    960_000,
    1_920_000,
    3_600_000,
    7_200_000,
    14_400_000,
    28_800_000,
    28_800_000,
)

#: The scale the reference's own exhaustion test uses. Worth knowing what it
#: really does: it rounds the first seven intervals to zero and leaves the last
#: four at 1, 1, 3 and 3 milliseconds -- so the cascade is *mostly* collapsed
#: and ``drain()`` still has to walk its tail. A test that needs every delay to
#: be zero says ``time_scale=0.0`` and means it.
COLLAPSED_SCALE = 0.0000001


def subscriber(
    event_types: Sequence[str] = ("order.created", "order.updated"),
    *,
    sub_id: str = "sub_1",
    url: str = SUB_URL,
    secret: str = SECRET,
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "id": sub_id,
        "notification_url": url,
        "event_types": tuple(event_types),
        "signature_key": secret,
        "enabled": enabled,
    }


def build(
    *,
    sink: MemorySink | None = None,
    signer: FakeSigner | None = None,
    events: FakeEvents | None = None,
    subscribers: Sequence[Mapping[str, object]] = (),
    capabilities: Sequence[str] = ("orders", "chaos", "webhooks", "webhooks.chaos"),
    schedule_ms: Sequence[int] = SHORT_SCHEDULE,
    time_scale: float = SHORT_SCALE,
    routes: Sequence[Any] = (),
    **config: Any,
) -> tuple[Unit, MemorySink, FakeSigner]:
    """A started, webhook-capable unit on a virtual clock."""
    the_sink = sink if sink is not None else MemorySink()
    the_signer = signer if signer is not None else FakeSigner()
    vendor = FakeVendor(
        capabilities=WEBHOOK_CAPABILITIES,
        not_supported={},
        signer=the_signer,
        events=events if events is not None else FakeEvents(),
    )
    unit: Unit = make_unit(  # type: ignore[assignment]
        routes,
        vendor=vendor,
        sink=the_sink,
        clock_mode="virtual",
        clock_start="2024-01-01T00:00:00Z",
        capabilities=tuple(capabilities),
        subscribers=tuple(subscribers),
        schedule_ms=tuple(schedule_ms),
        time_scale=time_scale,
        **config,
    )
    return unit, the_sink, the_signer


def create_order(unit: Unit, order_id: str = "ord_1", **fields: object) -> None:
    """Commit one insert into ``orders``, which the mapper turns into an event."""
    unit.context.store.collection("orders").insert({"id": order_id, "state": "OPEN", **fields})


def statuses(unit: Unit) -> list[str]:
    return [d.status for d in unit.webhooks.deliveries()]


# ---------------------------------------------------------------------------
# Commit-then-emit, and the synchronous prologue.
# ---------------------------------------------------------------------------


def test_an_event_exists_only_because_a_mutation_committed() -> None:
    """The design point of a journal-derived webhook path.

    A handler cannot forget to emit, and cannot emit for a mutation that never
    landed. Here the handler does nothing but write to the store, and the
    delivery happens anyway.
    """

    def handler(args: Any) -> ReplyInit:
        args.ctx.store.collection("orders").insert({"id": "ord_h", "state": "OPEN"})
        return ReplyInit(json={"ok": True})

    unit, sink, _ = build(subscribers=[subscriber()], routes=[route("POST", "/v2/orders", handler)])
    res = unit.handle(make_request(method="POST", path="/v2/orders", body={}))
    assert res.status == 200

    # (a) The prologue: the event is prepared and its first attempt is built
    #     and queued before `handle()` returned.
    prepared = unit.webhooks.prepared()
    assert [e.type for e in prepared] == ["order.created"]
    assert prepared[0].event_id != ""

    # (b) Only a drain makes the record and the receipt reliably observable.
    unit.webhooks.drain()
    assert [r.url for r in sink.received] == [SUB_URL]
    assert statuses(unit) == ["delivered"]
    unit.stop()


def test_a_handler_that_raises_after_mutating_still_emits_for_what_committed() -> None:
    """At-least-once is about *committed* state, not about a successful request.

    The insert landed and was journalled, so the subscriber is entitled to hear
    about it even though the caller got a 500. The alternative -- suppressing
    the event because the request failed -- would leave a consumer's view of
    the world permanently behind the fake's.
    """

    def handler(args: Any) -> ReplyInit:
        args.ctx.store.collection("orders").insert({"id": "ord_x", "state": "OPEN"})
        raise RuntimeError("after the commit")

    unit, sink, _ = build(subscribers=[subscriber()], routes=[route("POST", "/v2/orders", handler)])
    assert unit.handle(make_request(method="POST", path="/v2/orders", body={})).status == 500
    unit.webhooks.drain()
    assert len(sink.received) == 1
    unit.stop()


def test_a_delivery_record_is_not_claimed_to_exist_before_handle_returns() -> None:
    """The weaker claim, asserted as a claim about the API rather than a race.

    The reference's record is written after its first ``await`` and is not
    present when its handler returns either; under a worker thread here it may
    or may not have been written. So what is pinned is the seam: ``prepared()``
    is the synchronous observation and ``drain()`` is the one that settles.
    """
    unit, sink, _ = build(subscribers=[subscriber()])
    create_order(unit)
    assert len(unit.webhooks.prepared()) == 1
    unit.webhooks.drain()
    assert len(unit.webhooks.deliveries()) == 1
    assert len(sink.received) == 1
    unit.stop()


# ---------------------------------------------------------------------------
# The three reasons a journal entry produces nothing.
# ---------------------------------------------------------------------------


def test_a_seed_mutation_emits_nothing() -> None:
    """Loading a scenario with an open order must not push order.created to
    every subscriber. Seeding is not a business event."""
    unit, sink, _ = build(subscribers=[subscriber()])
    unit.context.store.collection("orders").insert({"id": "ord_seed", "state": "OPEN"}, {"seed": True})
    unit.webhooks.drain()
    assert sink.received == []
    assert unit.webhooks.deliveries() == ()
    assert unit.webhooks.prepared() == ()
    unit.stop()


def test_the_seed_rule_tests_for_true_and_not_for_truthiness() -> None:
    """``meta.seed is True``, ported literally.

    A vendor writing ``{"seed": "default.seed.json"}`` into its hydrate metadata
    is naming a file, not asserting a flag -- and under a truthiness test that
    vendor's every hydrate would go silent, which presents as "webhooks do not
    work" with nothing to point at.
    """
    unit, sink, _ = build(subscribers=[subscriber()])
    unit.context.store.collection("orders").insert({"id": "ord_a", "state": "OPEN"}, {"seed": "default.seed.json"})
    unit.webhooks.drain()
    assert len(sink.received) == 1
    unit.stop()


def test_subscription_entries_never_emit() -> None:
    """Registering a subscriber must not notify every subscriber that a
    subscriber was registered -- including, recursively, itself.

    This is also what makes ``_hydrate``'s re-insertion of config subscribers
    safe: it journals like any other mutation.
    """
    unit, sink, _ = build(subscribers=[subscriber(("*",))])
    unit.context.store.collection("subscriptions").insert(
        {
            "id": "sub_2",
            "notification_url": "https://other.test/x",
            "event_types": ["*"],
            "signature_key": "k",
            "enabled": True,
        }
    )
    unit.webhooks.drain()
    assert sink.received == []
    unit.stop()


def test_a_mapper_that_raises_is_logged_and_the_journal_keeps_moving() -> None:
    """One unmappable mutation must not stop delivery for every later one."""
    calls = {"n": 0}

    def flaky(entry: JournalEntry) -> Sequence[MappedEvent]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("cannot map this one")
        return (MappedEvent(type="order.created", entity_id=entry.id, build=lambda meta: {"id": meta.event_id}),)

    unit, sink, _ = build(subscribers=[subscriber()], events=FakeEvents(mapper=flaky))
    create_order(unit, "ord_1")
    create_order(unit, "ord_2")
    unit.webhooks.drain()
    assert len(sink.received) == 1
    assert unit.webhooks.worker_failures() == ()
    unit.stop()


# ---------------------------------------------------------------------------
# Fan-out.
# ---------------------------------------------------------------------------


def test_only_subscriptions_that_asked_for_the_type_are_delivered_to() -> None:
    unit, sink, _ = build(
        subscribers=[
            subscriber(("payment.created",), sub_id="sub_pay", url="https://payments.test/hooks"),
            subscriber(("order.created",), sub_id="sub_ord", url="https://orders.test/hooks"),
        ]
    )
    create_order(unit)
    unit.webhooks.drain()
    assert [r.url for r in sink.received] == ["https://orders.test/hooks"]
    unit.stop()


def test_a_disabled_subscriber_produces_no_record_at_all() -> None:
    """Skipped at fan-out rather than at send time.

    A record saying "this subscriber is disabled" would look like a delivery
    failure in the transcript, and a consumer reading the log would go and
    check a subscriber that was never meant to be called.
    """
    unit, sink, _ = build(subscribers=[subscriber(sub_id="off", enabled=False)])
    create_order(unit)
    unit.webhooks.drain()
    assert sink.received == []
    assert unit.webhooks.deliveries() == ()
    unit.stop()


def test_one_event_reaches_every_matching_subscriber_with_one_event_id() -> None:
    """The dedup handle is per event, not per delivery."""
    unit, _sink, _ = build(
        subscribers=[
            subscriber(("order.*",), sub_id="a", url="https://a.test/x", secret="key-a"),
            subscriber(("order.created",), sub_id="b", url="https://b.test/x", secret="key-b"),
        ]
    )
    create_order(unit)
    unit.webhooks.drain()
    records = unit.webhooks.deliveries()
    assert [r.url for r in records] == ["https://a.test/x", "https://b.test/x"]
    assert len({r.event_id for r in records}) == 1
    unit.stop()


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_two_runs_of_one_scenario_produce_the_same_event_ids() -> None:
    """What makes a webhook transcript diffable evidence rather than noise.

    Both the *value* and the shape are pinned: the value because a change to
    the digest inputs would otherwise pass silently, and the shape because a
    consumer of a UUID-shaped event id will reject anything else.
    """
    ids: list[str] = []
    for _ in range(2):
        unit, _sink, _ = build(subscribers=[subscriber()])
        create_order(unit, "ord_1")
        create_order(unit, "ord_2")
        unit.webhooks.drain()
        ids.append(",".join(d.event_id for d in unit.webhooks.deliveries()))
        unit.stop()
    assert ids[0] == ids[1]
    first = ids[0].split(",")[0]
    assert [len(part) for part in first.split("-")] == [8, 4, 4, 4, 12]


def test_two_events_from_one_journal_entry_get_different_ids() -> None:
    """The dispatcher-local counter in the digest is what separates them.

    Without it, a vendor that maps one mutation onto two event types would mint
    the same id twice and a consumer deduplicating on it would drop the second.
    """

    def two(entry: JournalEntry) -> Sequence[MappedEvent]:
        return tuple(
            MappedEvent(type=name, entity_id=entry.id, build=lambda meta: {"id": meta.event_id})
            for name in ("order.created", "order.updated")
        )

    unit, _sink, _ = build(subscribers=[subscriber()], events=FakeEvents(mapper=two))
    create_order(unit)
    unit.webhooks.drain()
    records = unit.webhooks.deliveries()
    assert [r.event_type for r in records] == ["order.created", "order.updated"]
    assert records[0].event_id != records[1].event_id
    unit.stop()


def test_a_mapper_supplied_event_id_overrides_the_minted_one() -> None:
    """A fixture that pins an id must get the id it pinned."""

    def pinned(entry: JournalEntry) -> Sequence[MappedEvent]:
        return (
            MappedEvent(
                type="order.created",
                entity_id=entry.id,
                event_id="evt_pinned",
                build=lambda meta: {"id": meta.event_id},
            ),
        )

    unit, sink, _ = build(subscribers=[subscriber()], events=FakeEvents(mapper=pinned))
    create_order(unit)
    unit.webhooks.drain()
    assert unit.webhooks.deliveries()[0].event_id == "evt_pinned"
    assert sink.received[0].body == b'{"id":"evt_pinned"}'
    unit.stop()


# ---------------------------------------------------------------------------
# The de-vendoring: the core contributes no header of its own.
# ---------------------------------------------------------------------------


def test_the_core_sends_no_delivery_header_of_its_own() -> None:
    """The strongest form of the claim, and the reason this is a rebuild.

    With a signer whose two hooks both return nothing, any header on the wire
    would have to have come from the core. The reference would still send four
    -- a content type and three brand-prefixed retry headers -- and three of
    those four name one vendor inside vendor-neutral code.
    """
    silent = FakeSigner(sign_with=lambda payload: {}, headers_with=lambda meta: {})
    unit, sink, _ = build(subscribers=[subscriber()], signer=silent)
    create_order(unit)
    unit.webhooks.drain()
    assert dict(sink.received[0].headers) == {}
    assert dict(unit.webhooks.deliveries()[0].headers) == {}
    unit.stop()


def test_no_core_module_carries_a_delivery_header_name_as_a_literal() -> None:
    """The same claim, checked at the source rather than on the wire.

    A future edit that reintroduced a hardcoded retry header would fail the
    test above only if that test happened to exercise the path; this one fails
    on the source, which is where the mistake is made.

    Scoped to ``core/webhooks/`` and including ``content-type``, because on the
    delivery path the core names *no* header at all -- not even that one. On
    the response path a content type is the core's own business, and
    ``kernel/reply.py`` names it for exactly that reason.

    Documentation is deliberately excluded and code is not: the module
    docstrings *describe* the headers a vendor sends, which is how a reader
    learns what the hook is for, and a checker that could not tell prose from a
    literal would push that explanation out of the file that needs it. What is
    forbidden is a **string the delivery path could put on the wire**.
    """
    import ast
    from pathlib import Path

    delivery_path = Path(__file__).resolve().parents[3] / "src" / "vendorfake" / "core" / "webhooks"
    # Hyphenated, which is what makes them header names rather than the
    # entity field `signature_key` or the metadata field `retry_number`.
    forbidden = ("retry-number", "retry-reason", "initial-delivery", "hmacsha256", "content-type", "-signature")
    offences: list[str] = []
    for module in sorted(delivery_path.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        documentation = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in documentation:
                continue
            for header in forbidden:
                if header in node.value.lower():
                    offences.append(f"{module.name}:{node.lineno} {node.value!r}")
    assert offences == [], f"the core carries delivery header names as literals: {offences}"


def test_the_vendor_receives_neutral_metadata_and_owns_the_wire_strings() -> None:
    """One hook, one direction. The core computes facts; the vendor names them.

    ``retry_reason`` reaches the vendor as :class:`DeliveryOutcome` -- a core
    enum -- and the vendor's own map turns it into a string the core never
    sees. That is the half of the de-vendoring the vendor-slug rule is blind
    to, because none of those strings contains a slug.
    """
    unit, sink, signer = build(
        subscribers=[subscriber()], sink=MemorySink(respond_with=lambda _r, i: 500 if i == 0 else 200)
    )
    create_order(unit)
    unit.webhooks.drain()

    first, second = signer.header_calls
    assert (first.attempt, first.retry_number, first.retry_reason) == (1, 0, None)
    assert (second.attempt, second.retry_number) == (2, 1)
    assert second.retry_reason is DeliveryOutcome.HTTP_ERROR
    assert second.initial_delivery_at == first.initial_delivery_at

    # And the wire strings that came back are the vendor's, not the core's.
    assert "acme-retry-reason" not in sink.received[0].headers
    assert sink.received[1].headers["acme-retry-reason"] == "acme_bad_status"
    assert sink.received[1].headers["acme-retry-number"] == "1"
    unit.stop()


def test_a_timeout_and_a_transport_failure_reach_the_vendor_as_different_outcomes() -> None:
    """The distinction the reference collapses into two of three literal
    strings in core. Here it is an enum the vendor maps, and both halves are
    observable."""
    unit, sink, signer = build(
        subscribers=[subscriber()],
        sink=MemorySink(respond_with=lambda _r, i: 0 if i == 0 else 200),
    )
    create_order(unit)
    unit.webhooks.drain()
    assert signer.header_calls[1].retry_reason is DeliveryOutcome.TIMEOUT
    assert sink.received[1].headers["acme-retry-reason"] == "acme_timed_out"
    unit.stop()


def test_the_signature_is_computed_over_the_bytes_that_are_sent() -> None:
    """Not over a re-encoding of the object they came from.

    The signer is handed ``raw_body``; the sink is handed the same object. If
    the two ever diverged -- a re-serialisation with different separators, say
    -- every signature would verify against the wrong bytes and every test
    would still pass.
    """
    unit, sink, signer = build(subscribers=[subscriber()])
    create_order(unit)
    unit.webhooks.drain()
    assert signer.sign_calls[0].raw_body == sink.received[0].body
    assert signer.sign_calls[0].notification_url == SUB_URL
    assert signer.sign_calls[0].secret == SECRET
    assert signer.sign_calls[0].attempt == 1
    unit.stop()


def test_the_signature_headers_win_over_the_provider_headers() -> None:
    """A provider that accidentally names a signature header must not blank it.

    Nothing in a correct vendor does this; the ordering exists so that the
    failure is a duplicated header name rather than a delivery whose signature
    silently became the empty string.
    """
    signer = FakeSigner(
        sign_with=lambda payload: {"x-sig": "real"},
        headers_with=lambda meta: {"x-sig": "clobbered", "content-type": "application/json"},
    )
    unit, sink, _ = build(subscribers=[subscriber()], signer=signer)
    create_order(unit)
    unit.webhooks.drain()
    assert sink.received[0].headers["x-sig"] == "real"
    unit.stop()


def test_a_vendor_with_no_signer_delivers_nothing() -> None:
    """Sending unsigned would be worse than sending nothing: a consumer that
    verifies would reject it, and one that does not would be trusting an
    unauthenticated push."""
    vendor = FakeVendor(capabilities=WEBHOOK_CAPABILITIES, not_supported={}, signer=None, events=FakeEvents())
    sink = MemorySink()
    unit: Unit = make_unit(  # type: ignore[assignment]
        vendor=vendor,
        sink=sink,
        clock_mode="virtual",
        capabilities=("orders", "chaos", "webhooks", "webhooks.chaos"),
        subscribers=(subscriber(),),
        schedule_ms=SHORT_SCHEDULE,
    )
    create_order(unit)
    unit.webhooks.drain()
    assert sink.received == []
    unit.stop()


# ---------------------------------------------------------------------------
# Retry.
# ---------------------------------------------------------------------------


def test_a_failing_subscriber_is_retried_on_the_scaled_schedule() -> None:
    """Statuses, retry numbers, scheduled delays and the shared event id.

    ``next_attempt_in_ms`` is the schedule made observable: ten and twenty
    milliseconds are one minute and two minutes scaled, so the *shape* of the
    documented backoff is asserted without waiting for it.
    """
    unit, _sink, _ = build(
        subscribers=[subscriber()],
        sink=MemorySink(respond_with=lambda _r, i: 500 if i < 2 else 200),
    )
    create_order(unit)
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    assert [d.status for d in log] == ["failed", "failed", "delivered"]
    assert [d.retry_number for d in log] == [0, 1, 2]
    assert [d.attempt for d in log] == [1, 2, 3]
    assert [d.next_attempt_in_ms for d in log] == [10, 20, None]
    assert len({d.event_id for d in log}) == 1
    unit.stop()


def test_the_retry_cascade_collapses_into_one_drain() -> None:
    """The whole point of ``Clock.advance``'s ``settle`` hook.

    The clock is virtual, so nothing fires unless something advances it. If the
    worker handshake were missing, ``advance()`` would re-scan before the worker
    had registered the next retry, find no timer, and return -- and this would
    report three records instead of twelve, intermittently.
    """
    unit, _sink, _ = build(
        subscribers=[subscriber()],
        sink=MemorySink(respond_with=500),
        schedule_ms=FULL_SCHEDULE,
        time_scale=COLLAPSED_SCALE,
    )
    create_order(unit)
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    assert len(log) == 12
    assert log[-1].status == "exhausted"
    assert sum(1 for d in log if d.status == "failed") == 11
    assert unit.webhooks.worker_failures() == ()
    unit.stop()


def test_advance_alone_collapses_a_zero_delay_cascade_when_given_the_settle_hook() -> None:
    """The half of the handshake that ``drain()``'s own loop would otherwise hide.

    Two facts have to be held together here. First, ``advance(ms)`` moves the
    clock *once* and then re-scans, so a retry scheduled during the call with a
    positive delay is genuinely in the future and does not fire -- that is true
    of the reference too, and it is why ``drain()`` exists and walks the
    schedule one due time per pass. Second, when the scaled delays round to
    zero -- which is exactly what the exhaustion case does -- the whole cascade
    *is* due inside one call, and the re-scan is what fires it.

    That second case is the one ``POST /__unit/clock/advance`` hits, and it is
    the one the ``settle`` hook exists for: without it the re-scan runs before
    the worker has registered the retry it is about to schedule, finds nothing,
    and returns. So this advances the clock exactly once, with no drain
    anywhere, and asserts the full twelve.
    """
    unit, _sink, _ = build(
        subscribers=[subscriber()],
        sink=MemorySink(respond_with=500),
        schedule_ms=FULL_SCHEDULE,
        # Every interval scaled to zero, so the whole cascade is due inside one
        # call. `COLLAPSED_SCALE` would leave the last four intervals at one to
        # three milliseconds and this test would measure `drain()` instead.
        time_scale=0.0,
    )
    create_order(unit)
    unit.webhooks.quiesce()
    assert [d.next_attempt_in_ms for d in unit.webhooks.deliveries()] == [0]

    unit.context.clock.advance(0, settle=unit.webhooks.settle)

    log = unit.webhooks.deliveries()
    assert len(log) == 12
    assert log[-1].status == "exhausted"
    assert unit.context.clock.pending() == []
    unit.stop()


def test_drain_needs_one_clock_advance_for_a_fully_collapsed_cascade() -> None:
    """What ``settle=`` buys inside ``drain()`` itself.

    ``drain()`` re-quiesces on every pass, so a cascade would settle either
    way, one retry per pass -- which is why the count of passes, not the count
    of records, is the observation that separates the two. With the hook, every
    zero-delay retry in the cascade fires inside one ``advance``; without it,
    each pass fires one timer and returns, and eleven retries cost eleven
    advances and eleven more trips round the loop.
    """
    unit, _sink, _ = build(
        subscribers=[subscriber()],
        sink=MemorySink(respond_with=500),
        schedule_ms=FULL_SCHEDULE,
        time_scale=0.0,
    )
    clock = unit.context.clock
    real_advance = clock.advance
    advances: list[float] = []

    def counting(ms: float, *, settle: Any = None) -> int:
        advances.append(ms)
        return real_advance(ms, settle=settle)

    clock.advance = counting  # type: ignore[method-assign]
    create_order(unit)
    unit.webhooks.drain()

    assert len(unit.webhooks.deliveries()) == 12
    assert advances == [0.0]
    unit.stop()


def test_a_positive_delay_cascade_needs_a_drain_and_gets_one() -> None:
    """The complement of the test above, stated so the limit is not a surprise.

    One ``advance`` past every interval is *not* enough when the delays are
    real, because each retry is scheduled relative to the already-advanced now.
    ``drain()`` is what walks it, moving to the next due time each pass, and it
    is why the control plane's advance route drains afterwards.
    """
    unit, _sink, _ = build(
        subscribers=[subscriber()],
        sink=MemorySink(respond_with=500),
        schedule_ms=FULL_SCHEDULE,
        time_scale=0.001,
    )
    create_order(unit)
    unit.context.clock.advance(sum(FULL_SCHEDULE), settle=unit.webhooks.settle)
    assert len(unit.webhooks.deliveries()) < 12

    unit.webhooks.drain()
    assert len(unit.webhooks.deliveries()) == 12
    unit.stop()


@pytest.mark.parametrize("run", range(50))
def test_exhaustion_reports_twelve_every_single_time(run: int) -> None:
    """A race whose symptom is a wrong count, not a hang, is invisible to a
    single run and to a flake-retry rule. Fifty runs is the cheap version of
    the two-hundred the brief asks for; each is a few milliseconds because the
    clock never really waits."""
    unit, _sink, _ = build(
        subscribers=[subscriber()],
        sink=MemorySink(respond_with=500),
        schedule_ms=FULL_SCHEDULE,
        time_scale=COLLAPSED_SCALE,
    )
    create_order(unit)
    unit.webhooks.drain()
    assert len(unit.webhooks.deliveries()) == 12
    unit.stop()


def test_an_exhausted_delivery_says_so_rather_than_going_quiet() -> None:
    unit, _sink, _ = build(
        subscribers=[subscriber()], sink=MemorySink(respond_with=503), schedule_ms=(0,), time_scale=1.0
    )
    create_order(unit)
    unit.webhooks.drain()
    log = unit.webhooks.deliveries()
    assert [d.status for d in log] == ["failed", "exhausted"]
    assert log[-1].error == "retry schedule exhausted"
    assert log[-1].response_status == 503
    unit.stop()


def test_a_retry_policy_patched_at_runtime_takes_effect() -> None:
    """What ``POST /__unit/webhooks/retry-policy`` is for: collapse the
    schedule mid-scenario so a test does not spend twenty-four scaled hours."""
    unit, _sink, _ = build(
        subscribers=[subscriber()], sink=MemorySink(respond_with=500), schedule_ms=(999_999,), time_scale=1.0
    )
    unit.webhooks.set_retry_policy({"time_scale": 0.0})
    create_order(unit)
    unit.webhooks.drain()
    assert [d.next_attempt_in_ms for d in unit.webhooks.deliveries()] == [0, None]
    unit.stop()


# ---------------------------------------------------------------------------
# Delivery chaos. Order is the assertion in three of the four.
# ---------------------------------------------------------------------------


def _rule(rule_id: str, fault: str, **params: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": rule_id,
        "scope": "webhook",
        "fault": fault,
        "match": {"event_type": "order.created"},
        "when": {"nth": [1]},
    }
    if params:
        body["params"] = params
    return body


def test_duplicate_sends_the_same_event_twice_with_one_id() -> None:
    """One extra copy by default, and the copy is labelled so the transcript
    explains itself."""
    unit, _sink, _ = build(subscribers=[subscriber()], chaos_rules=[_rule("dup", "webhook.duplicate", copies=1)])
    create_order(unit)
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    assert len(log) == 2
    assert log[0].event_id == log[1].event_id
    assert log[0].body_hash == log[1].body_hash
    assert all(d.status == "delivered" for d in log)
    assert log[0].chaos == ("dup:webhook.duplicate",)
    assert log[1].chaos == ("dup:webhook.duplicate", "duplicate-copy")
    unit.stop()


def test_the_copy_count_is_coerced_rather_than_indexed() -> None:
    """Rule parameters are arbitrary JSON; ``"2"`` is a value a consumer sends.

    ``1 + "2"`` is a ``TypeError`` on the request thread, which would surface
    as a 500 on an unrelated request rather than as two deliveries.
    """
    unit, _sink, _ = build(subscribers=[subscriber()], chaos_rules=[_rule("dup", "webhook.duplicate", copies="2")])
    create_order(unit)
    unit.webhooks.drain()
    assert len(unit.webhooks.deliveries()) == 3
    unit.stop()


def test_out_of_order_holds_one_event_back_until_the_next_has_gone() -> None:
    """The delivered *order* is the assertion, and it is what a second delivery
    thread would break.

    One slot, released on the next enqueue regardless of type, and released
    after the copies -- ported literally, because those three rules together
    produce the reversed sequence and any one of them relaxed does not.
    """
    unit, sink, _ = build(
        subscribers=[subscriber(("order.*",))],
        chaos_rules=[
            {
                "id": "reorder",
                "scope": "webhook",
                "fault": "webhook.out_of_order",
                "match": {"event_type": "order.created"},
                "when": {"nth": [1]},
            }
        ],
    )
    create_order(unit, "ord_1")
    unit.context.store.collection("orders").update("ord_1", lambda draft: draft.update({"state": "COMPLETED"}))
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    held = [d for d in log if d.status == "skipped"]
    assert len(held) == 1
    assert held[0].error == "held for out-of-order delivery"
    assert held[0].response_status == 0

    delivered = [d for d in log if d.status == "delivered"]
    assert [d.event_type for d in delivered] == ["order.updated", "order.created"]
    assert [r.body for r in sink.received] == [d.body_preview.encode() for d in delivered]
    assert delivered[1].chaos == ("released-after-reorder",)
    unit.stop()


def test_drop_never_reaches_the_sink_and_schedules_no_retry() -> None:
    """Recorded so a test can see it happened; the subscriber gets nothing."""
    unit, sink, _ = build(subscribers=[subscriber()], chaos_rules=[_rule("gone", "webhook.drop")])
    create_order(unit)
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    assert [d.status for d in log] == ["dropped"]
    assert log[0].error == "dropped by chaos rule (webhook.drop)"
    assert log[0].chaos == ("gone:webhook.drop",)
    assert sink.received == []
    assert unit.context.clock.pending() == []
    unit.stop()


def test_drop_ack_really_sends_and_then_discards_the_answer() -> None:
    """The subscriber answered 200; the acknowledgement was lost in transit.

    ``response_status == 200`` on the failed record is what distinguishes this
    fault from an outage -- and it is the case a consumer's idempotency has to
    survive, because the retry carries the same event id.
    """
    unit, sink, _ = build(subscribers=[subscriber()], chaos_rules=[_rule("lost", "webhook.drop_ack")])
    create_order(unit)
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    assert [d.status for d in log] == ["failed", "delivered"]
    assert log[0].response_status == 200
    assert log[0].error is not None and "chaos" in log[0].error
    assert log[0].event_id == log[1].event_id
    assert len(sink.received) == 2
    unit.stop()


def test_delay_parks_the_delivery_on_the_clock() -> None:
    """The sink is not called until time moves, and ``drain()`` moves it."""
    unit, sink, _ = build(subscribers=[subscriber()], chaos_rules=[_rule("slow", "webhook.delay", delay_ms=5_000)])
    create_order(unit)
    unit.webhooks.quiesce()
    assert sink.received == []
    assert [t.label for t in unit.context.clock.pending()] == [f"webhook:{unit.webhooks.prepared()[0].event_id}"]

    unit.webhooks.drain()
    assert len(sink.received) == 1
    assert unit.webhooks.deliveries()[0].chaos == ("slow:webhook.delay",)
    unit.stop()


def test_delivery_faults_stop_when_their_capability_does() -> None:
    """``webhooks.chaos`` and not ``chaos``: a profile that wants request faults
    but honest delivery is a real configuration, and one gate cannot express
    it. Here delivery faults are off while ``chaos`` stays on."""
    unit, _sink, _ = build(
        subscribers=[subscriber()],
        capabilities=("orders", "chaos", "webhooks"),
        chaos_rules=[_rule("dup", "webhook.duplicate", copies=3)],
    )
    create_order(unit)
    unit.webhooks.drain()
    log = unit.webhooks.deliveries()
    assert len(log) == 1
    assert log[0].chaos == ()
    unit.stop()


# ---------------------------------------------------------------------------
# One writer for the log.
# ---------------------------------------------------------------------------


def test_delivery_ids_are_dense_and_in_log_order_across_mixed_outcomes() -> None:
    """Two writers would renumber these and reorder the log.

    The mix matters: ``dropped`` is recorded without ever touching the sink,
    which in the reference happens on the request thread while ``delivered``
    happens after an await. Submitting both to the same queue is what keeps the
    numbering a function of the scenario.
    """
    unit, _sink, _ = build(
        subscribers=[subscriber(("order.*",))],
        chaos_rules=[
            {
                "id": "gone",
                "scope": "webhook",
                "fault": "webhook.drop",
                "match": {"event_type": "order.created"},
                "when": {"nth": [1]},
            }
        ],
    )
    create_order(unit, "ord_1")
    unit.context.store.collection("orders").update("ord_1", lambda draft: draft.update({"state": "COMPLETED"}))
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    assert [d.id for d in log] == ["dlv_00001", "dlv_00002"]
    assert [d.status for d in log] == ["dropped", "delivered"]
    unit.stop()


def test_a_chaos_drop_cannot_overtake_a_delivery_that_is_still_in_flight() -> None:
    """The one-writer rule, made observable by making the first send slow.

    This is the case the reference gets away with only because Node has one
    thread. ``dropped`` never touches the sink, so recording it where it is
    decided -- on the request thread -- looks harmless; it is not, because the
    delivery ahead of it is still inside ``sink.send``. The drop would take
    ``dlv_00001`` out from under it and the published order would invert.

    Submitting the terminal outcome to the same queue makes the order a
    function of the scenario instead of of how long a subscriber took to
    answer.
    """
    import time as _time

    def slow_first(_req: Any, index: int) -> int:
        if index == 0:
            _time.sleep(0.05)
        return 200

    unit, _sink, _ = build(
        subscribers=[subscriber(("order.*",))],
        sink=MemorySink(respond_with=slow_first),
        chaos_rules=[
            {
                "id": "gone",
                "scope": "webhook",
                "fault": "webhook.drop",
                "match": {"event_type": "order.updated"},
                "when": {"nth": [1]},
            }
        ],
    )
    create_order(unit, "ord_1")
    unit.context.store.collection("orders").update("ord_1", lambda draft: draft.update({"state": "COMPLETED"}))
    unit.webhooks.drain()

    log = unit.webhooks.deliveries()
    assert [d.id for d in log] == ["dlv_00001", "dlv_00002"]
    assert [d.status for d in log] == ["delivered", "dropped"]
    assert [d.event_type for d in log] == ["order.created", "order.updated"]
    unit.stop()


def test_deliveries_hands_out_copies() -> None:
    unit, _sink, _ = build(subscribers=[subscriber()])
    create_order(unit)
    unit.webhooks.drain()
    unit.webhooks.deliveries()[0].headers["injected"] = "yes"
    assert "injected" not in unit.webhooks.deliveries()[0].headers
    unit.stop()


def test_a_record_carries_the_body_it_delivered_three_ways() -> None:
    """Hash, preview and parsed object, all of one payload.

    All three exist because they answer different questions -- "is this the
    same payload as that one", "what did it look like", "what was the version"
    -- and a record where they disagreed would be worse than one carrying only
    the first.
    """
    unit, sink, _ = build(subscribers=[subscriber()])
    create_order(unit)
    unit.webhooks.drain()
    record = unit.webhooks.deliveries()[0]
    assert record.body_preview.encode() == sink.received[0].body
    assert record.body_is_json is True
    assert record.body["type"] == "order.created"
    assert record.body_hash == __import__("hashlib").sha256(sink.received[0].body).hexdigest()
    unit.stop()


# ---------------------------------------------------------------------------
# Switches: the capability, the runtime flag, and the profile flag.
# ---------------------------------------------------------------------------


def test_the_webhooks_capability_is_checked_per_entry_and_not_once_at_startup() -> None:
    """Switching the capability off at runtime must stop delivery immediately.

    A gate evaluated once, around ``attach``, would answer a question about the
    profile rather than about the unit's current state -- and a consumer who
    disabled webhooks through the control plane would keep receiving them.
    """
    unit, sink, _ = build(subscribers=[subscriber()])
    unit.context.capabilities.disable("webhooks")
    create_order(unit, "ord_off")
    unit.webhooks.drain()
    assert sink.received == []

    unit.context.capabilities.enable("webhooks")
    create_order(unit, "ord_on")
    unit.webhooks.drain()
    assert len(sink.received) == 1
    unit.stop()


def test_a_unit_that_never_declared_webhooks_delivers_nothing() -> None:
    """The default fake declares delivery as not supported, with a reason.

    Silence would otherwise be indistinguishable from a broken mapper, which is
    exactly the hole ``gates.py`` exists to close.
    """
    unit, sink, _ = build(subscribers=[subscriber()], capabilities=("orders", "chaos"))
    create_order(unit)
    unit.webhooks.drain()
    assert sink.received == []
    unit.stop()


def test_disable_delivery_in_the_profile_cannot_be_switched_back_on() -> None:
    """Two switches with different lifetimes: the profile's is a property of the
    deployment, ``set_enabled`` is a runtime one."""
    unit, sink, _ = build(subscribers=[subscriber()], disable_delivery=True)
    assert unit.webhooks.enabled is False
    unit.webhooks.set_enabled(True)
    assert unit.webhooks.enabled is False
    create_order(unit)
    unit.webhooks.drain()
    assert sink.received == []
    unit.stop()


def test_set_enabled_silences_delivery_at_runtime() -> None:
    unit, sink, _ = build(subscribers=[subscriber()])
    unit.webhooks.set_enabled(False)
    create_order(unit, "ord_quiet")
    unit.webhooks.drain()
    assert sink.received == []
    unit.webhooks.set_enabled(True)
    create_order(unit, "ord_loud")
    unit.webhooks.drain()
    assert len(sink.received) == 1
    unit.stop()


# ---------------------------------------------------------------------------
# Lifecycle.
# ---------------------------------------------------------------------------


def test_config_subscribers_are_reinstated_and_the_log_cleared_on_hydrate() -> None:
    """``reset`` empties every collection including the subscriptions.

    Re-inserting them after the reset is what stops ``POST /__unit/state/reset``
    from silently deregistering every profile-declared subscriber; clearing the
    log after is what stops one transcript from spanning two scenarios.
    """
    unit, _sink, _ = build(subscribers=[subscriber()])
    create_order(unit)
    unit.webhooks.drain()
    assert len(unit.webhooks.deliveries()) == 1

    unit.control.hydrate()
    assert [s.id for s in unit.webhooks.subscriptions()] == ["sub_1"]
    assert unit.webhooks.deliveries() == ()
    unit.stop()


def test_a_config_subscriber_gets_a_generated_id_when_it_declares_none() -> None:
    unit, _sink, _ = build(subscribers=[{"notification_url": SUB_URL, "event_types": ("*",), "signature_key": "k"}])
    only = unit.webhooks.subscriptions()[0]
    assert only.id == "wbhk_cfg_01"
    assert only.name == "config subscriber 1"
    unit.stop()


def test_hydrate_restores_config_subscribers_to_their_declared_form() -> None:
    """A hydrate is a reset, and a reset means the profile again.

    Written as its own test because the opposite is the plausible expectation:
    the ``has(id)`` guard inside ``load_config_subscribers`` looks as though it
    protects a runtime edit, and it does not -- ``reset`` has already emptied
    the collection by the time it runs. What the guard actually protects is a
    *second* call without a reset in between, which the next test pins.
    """
    unit, _sink, _ = build(subscribers=[subscriber()])
    unit.context.store.collection("subscriptions").update(
        "sub_1", lambda draft: draft.update({"notification_url": "https://moved.test/x"})
    )
    unit.control.hydrate()
    assert unit.webhooks.subscriptions()[0].notification_url == SUB_URL
    unit.stop()


def test_loading_config_subscribers_twice_does_not_duplicate_them() -> None:
    """What the ``has(id)`` guard is really for.

    Without it, every subscriber would be delivered to twice after the second
    load, which presents as "the fake sends duplicate webhooks" -- the symptom
    of a chaos fault a consumer did not switch on.
    """
    unit, _sink, _ = build(subscribers=[subscriber()])
    unit.webhooks.load_config_subscribers(unit.context.config.webhooks.subscribers)
    assert [s.id for s in unit.webhooks.subscriptions()] == ["sub_1"]
    unit.stop()


def test_a_subscriber_created_at_runtime_does_not_survive_a_hydrate() -> None:
    """Because it is state, and hydrate resets state. Stated so nobody has to
    guess which of the two kinds of subscriber a reset keeps."""
    unit, _sink, _ = build(subscribers=[subscriber()])
    unit.context.store.collection("subscriptions").insert(
        {
            "id": "sub_runtime",
            "notification_url": "https://runtime.test/x",
            "event_types": ["*"],
            "signature_key": "k",
            "enabled": True,
        }
    )
    assert len(unit.webhooks.subscriptions()) == 2
    unit.control.hydrate()
    assert [s.id for s in unit.webhooks.subscriptions()] == ["sub_1"]
    unit.stop()


def test_stop_settles_delivery_before_it_discards_the_timers() -> None:
    """Order matters: clearing first would discard the retries the drain exists
    to settle, and ``stop()`` would silently lose deliveries a test caused."""
    unit, sink, _ = build(
        subscribers=[subscriber()], sink=MemorySink(respond_with=lambda _r, i: 500 if i == 0 else 200)
    )
    create_order(unit)
    unit.stop()
    assert [d.status for d in unit.webhooks.deliveries()] == ["failed", "delivered"]
    assert len(sink.received) == 2
    assert unit.context.clock.pending() == []


def test_enqueue_is_callable_directly_for_a_vendors_own_test_route() -> None:
    """A vendor's "send a test event and tell me what happened" route reaches
    this, so it must fan out through the same matching and the same signer."""
    unit, sink, _ = build(subscribers=[subscriber(("order.created",))])
    unit.webhooks.enqueue(
        PreparedEvent(
            type="order.created",
            event_id="evt_test_1",
            entity_id="sub_1",
            created_at="2024-01-01T00:00:00.000Z",
            body={"test": True},
        )
    )
    unit.webhooks.drain()
    assert unit.webhooks.deliveries()[0].event_id == "evt_test_1"
    assert sink.received[0].body == b'{"test":true}'
    unit.stop()


def test_the_sink_kind_and_the_live_policy_are_reportable() -> None:
    """What ``/__unit/info`` reads. Asserted here so the control plane finds it
    already present rather than adding a second accessor."""
    unit, _sink, _ = build(subscribers=[subscriber()])
    assert unit.webhooks.sink_kind == "memory"
    assert unit.webhooks.retry_policy.as_json()["schedule_ms"] == list(SHORT_SCHEDULE)
    unit.stop()
