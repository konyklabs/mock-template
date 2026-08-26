"""Webhook delivery, derived from the journal and owned by nobody else.

FOR: turning committed state mutations into signed outbound events, delivering
them at least once, and recording every attempt in a log a consumer can read
back. Events are derived from the state journal, **not** fired by hand from
route handlers, so an event can only exist if the mutation behind it committed.
Delivery is at-least-once: an attempt is retried until a success status or
schedule exhaustion, and a delivery whose acknowledgement is lost is retried and
therefore duplicated. Every attempt carries the same ``event_id``, which is the
consumer's dedup handle.

INVARIANT: **the core sends no delivery headers of its own.** Every header goes
through the vendor: ``signer.sign(...)`` for the signature and
``signer.headers(meta)`` for everything else, over the neutral
:class:`DeliveryMetadata`. See ``webhooks/models.py`` for why that is one hook
and not two, and for the retry-reason vocabulary that moved with it.

SECOND INVARIANT: **the delivery log has exactly one writer.** Every record --
including the two chaos outcomes that never touch the sink, ``skipped`` and
``dropped`` -- is written on the delivery worker's thread, because the log is
numbered ``dlv_00001`` in write order and ``deliveries()`` is published in that
order. The reference writes ``skipped`` and ``dropped`` from the request thread
and gets away with it only because Node has one thread; two writers here would
renumber the ids and reorder the log, and the ported chaos tests assert both.

THE SYNCHRONOUS PROLOGUE, and exactly what it does and does not promise.
The reference leans on a JavaScript rule Python does not have: an ``async``
function body runs synchronously up to its first ``await``, so ``attempt()``
does the mapping, the id minting, the body build, the signing and the call into
``sink.send`` before ``handle()`` can return. Python has no equivalent, so the
prologue is explicit: :meth:`enqueue` runs on the request thread and completes
the *whole* preparation of the first attempt -- event id, body bytes, signature,
headers, work item on the queue -- before it returns. What it does **not** do is
send. So the two assertions this design supports are:

(a) after ``handle()`` returns, :meth:`prepared` shows the event, with its id
    minted and its first attempt fully built and queued; and
(b) :meth:`drain` is the only thing that makes :class:`DeliveryRecord`\\ s and
    the sink's own record of receipt reliably observable.

The stronger claim -- that a delivery record exists before ``handle()``
returns -- is *not* made, and asserting it would be asserting a race: under one
worker thread the record may or may not have been written by then. The
reference does not give that guarantee either; its record is written after its
first ``await``. Every reference test already drains first, so nothing is lost.

RETRIES ARE PREPARED ON THE WORKER, first attempts on the request thread. The
signature covers the attempt number for any vendor that declares
``attempt_bound``, so a retry must be re-signed rather than re-sent, and the
place to do that is the job that decided to retry. That also keeps the timer
callback trivial -- it submits an already-built work item -- which matters
because the timer callback runs on ``Clock.advance``'s caller.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from vendorfake.core.capability.gates import CoreCapability
from vendorfake.core.chaos.engine import ChaosSubject
from vendorfake.core.chaos.selector import FaultSelector
from vendorfake.core.kernel.types import EventMeta, JournalEntry, PreparedEvent, SignInput, UnitContext
from vendorfake.core.state.store import Store
from vendorfake.core.time.clock import Clock
from vendorfake.core.util.json import dump_json, sha256_hex
from vendorfake.core.util.numbers import as_float, as_int
from vendorfake.core.webhooks.models import (
    SUBSCRIPTION_COLLECTION,
    DeliveryMetadata,
    DeliveryOutcome,
    DeliveryRecord,
    Subscription,
    matches_event_type,
)
from vendorfake.core.webhooks.retry import MutableRetryPolicy, RetryPolicy, retry_delay_ms, schedule_exhausted
from vendorfake.core.webhooks.sink import DeliverySink, SinkRequest
from vendorfake.core.webhooks.worker import DeliveryWorker

if TYPE_CHECKING:
    from vendorfake.core.config.models import SubscriberConfig

__all__ = ["WebhookDispatcher"]

_TIMER_PREFIX = "webhook"
"""Every timer this module schedules carries a label starting with this.

Load-bearing, not decoration: :meth:`WebhookDispatcher.drain` decides whether
the unit has settled by asking the clock which timers are pending and filtering
on this prefix. A timer scheduled here under some other label would make
``drain()`` return with a retry still on the clock.
"""

_DRAIN_PASSES = 500
"""Upper bound on :meth:`WebhookDispatcher.drain`'s loop, ported from
``dispatcher.ts:122``. It exists so a subscriber that fails forever cannot turn
a drain into a hang; twelve attempts need twelve passes, so hitting five hundred
means something is wrong rather than slow."""

_REAL_MODE_POLL_MS = 250.0
"""How long :meth:`WebhookDispatcher.drain` sleeps at most between passes on a
real clock. The reference's ``Math.min(next + 1, 250)``: long enough not to
spin, short enough that a ten-millisecond retry is not waited out for a
quarter of a second."""

_BODY_PREVIEW_LIMIT = 400
"""Characters of the delivered payload kept on each record."""

#: Reference defaults for the two delivery faults that take a parameter:
#: ``Number(decision.params.copies ?? 1)`` and
#: ``Number(decision.params.delayMs ?? 50)``. One extra copy and fifty
#: milliseconds respectively.
DEFAULT_DUPLICATE_COPIES = 1
DEFAULT_WEBHOOK_DELAY_MS = 50.0


@dataclass(frozen=True, slots=True)
class _Queued:
    """One delivery of one event to one subscriber, before it is built."""

    event: PreparedEvent
    subscription: Subscription
    #: 0 for the first send. ``attempt`` is this plus one.
    retry_number: int
    initial_delivery_at: str
    #: Set by ``webhook.drop_ack``: send for real, then discard the answer.
    drop_ack: bool
    #: Why the previous attempt failed. ``None`` on the first send.
    retry_reason: DeliveryOutcome | None
    #: Chaos labels recorded against this attempt, e.g. ``["dup:webhook.duplicate"]``.
    chaos_applied: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Attempt:
    """A fully built work item: nothing left to decide but whether it worked."""

    queued: _Queued
    body: bytes
    body_text: str
    headers: Mapping[str, str]


class WebhookDispatcher:
    """The journal listener, the fan-out, the retry loop and the delivery log."""

    __slots__ = (
        "_clock",
        "_delivery_seq",
        "_disabled",
        "_enabled_flag",
        "_event_seq",
        "_get_context",
        "_held_for_reorder",
        "_log",
        "_log_lock",
        "_on_log",
        "_prepared",
        "_request_lock",
        "_retry",
        "_selector",
        "_sink",
        "_store",
        "_worker",
    )

    def __init__(
        self,
        *,
        store: Store,
        clock: Clock,
        selector: FaultSelector,
        sink: DeliverySink,
        retry: RetryPolicy,
        get_context: Callable[[], UnitContext],
        disabled: bool = False,
        on_log: Callable[[DeliveryRecord], None] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._selector = selector
        self._sink = sink
        self._retry = MutableRetryPolicy.of(retry)
        self._get_context = get_context
        self._disabled = disabled
        self._on_log = on_log

        self._worker = DeliveryWorker()
        #: Written only on the worker thread; read under `_log_lock` so a
        #: control-plane read never sees a half-appended record.
        self._log: list[DeliveryRecord] = []
        self._log_lock = threading.Lock()
        self._delivery_seq = 0
        #: Request-side state: the event counter and the one reorder slot.
        #: Re-entrant because `enqueue` runs inside a journal listener, which
        #: already holds the store's lock, and may itself read the store.
        self._request_lock = threading.RLock()
        self._event_seq = 0
        self._held_for_reorder: _Queued | None = None
        self._prepared: list[PreparedEvent] = []
        self._enabled_flag = True

    # -- switches and reporting ---------------------------------------------

    @property
    def enabled(self) -> bool:
        """Both switches must be on. ``disabled`` comes from the profile and
        cannot be turned back on at runtime; ``set_enabled`` is the runtime one."""
        return self._enabled_flag and not self._disabled

    def set_enabled(self, on: bool) -> None:
        """Silence or resume delivery without touching the capability."""
        self._enabled_flag = on

    @property
    def sink_kind(self) -> str:
        return self._sink.kind

    @property
    def sink(self) -> DeliverySink:
        """The sink itself, for the one control route that programs it.

        Published rather than private because ``POST /__unit/webhooks/sink``
        has to reach a :class:`~vendorfake.core.webhooks.sink.MemorySink` to
        program its next answers, and a forced retry driven from *outside* the
        process is the only way a language-independent conformance check can
        observe the retry schedule at all. The dispatcher does not care what it
        hands back: it narrows nothing and promises nothing beyond the
        :class:`~vendorfake.core.webhooks.sink.DeliverySink` protocol, so a
        caller wanting more must check for it.
        """
        return self._sink

    @property
    def retry_policy(self) -> MutableRetryPolicy:
        return self._retry

    def set_retry_policy(self, patch: Mapping[str, Any]) -> MutableRetryPolicy:
        """Patch the live policy and return it, so a caller can report the result."""
        return self._retry.apply(patch)

    def deliveries(self) -> tuple[DeliveryRecord, ...]:
        """Every attempt, oldest first, each a private copy.

        Copies because the reference copies (``dispatcher.ts:109``) and for the
        same reason: a caller that mutated a record's ``headers`` would rewrite
        the evidence a later assertion reads.
        """
        with self._log_lock:
            return tuple(record.copy() for record in self._log)

    def clear_log(self) -> None:
        """Forget every delivery. Called on hydrate, so a re-seeded unit starts
        with a clean transcript rather than one that spans two scenarios."""
        with self._log_lock:
            self._log.clear()

    def prepared(self) -> tuple[PreparedEvent, ...]:
        """Every event prepared for delivery, in preparation order.

        The observable half of the synchronous prologue: this is populated
        before ``handle()`` returns, while a :class:`DeliveryRecord` is not.
        See the module docstring for why the weaker claim is the true one.
        """
        with self._request_lock:
            return tuple(self._prepared)

    def subscriptions(self) -> tuple[Subscription, ...]:
        """Every registered subscriber, in insertion order."""
        return tuple(Subscription.from_entity(e) for e in self._store.collection(SUBSCRIPTION_COLLECTION).all())

    # -- wiring --------------------------------------------------------------

    def load_config_subscribers(self, subscribers: Sequence[SubscriberConfig]) -> None:
        """Seed profile-declared subscribers into the store.

        Inserted rather than held aside, so that a config subscriber and one
        created through a vendor's own API are the same kind of thing to
        everything downstream. An id that already exists is skipped rather than
        overwritten: hydrate runs on every reset, and overwriting would discard
        a runtime change on the first ``POST /__unit/state/reset``.
        """
        collection = self._store.collection(SUBSCRIPTION_COLLECTION)
        for index, sub in enumerate(subscribers):
            subscriber_id = sub.id if sub.id is not None else f"wbhk_cfg_{index + 1:02d}"
            if collection.has(subscriber_id):
                continue
            entity: dict[str, Any] = {
                "id": subscriber_id,
                "name": sub.name if sub.name is not None else f"config subscriber {index + 1}",
                "notification_url": sub.notification_url,
                "event_types": list(sub.event_types),
                "signature_key": sub.signature_key,
                "enabled": sub.enabled,
            }
            collection.insert(entity, {"source": "config"})

    def attach(self) -> None:
        """Wire the dispatcher to the store journal. Called once, by the kernel.

        THE ``webhooks`` CAPABILITY GATE IS HERE, inside the listener rather
        than around the registration. The registry is mutable at runtime --
        ``POST /__unit/capabilities`` can switch ``webhooks`` off and on again
        -- so a gate evaluated once at construction would answer a question
        about the profile rather than about the unit's current state, and a
        capability turned off after start-up would go on delivering.

        Four other reasons a journal entry produces nothing, in the order the
        reference checks them and for reasons that are not interchangeable:

        * the vendor has no event mapper or no signer, so there is nothing to
          build or nothing to sign it with;
        * the entry mutates the subscription collection, so registering a
          subscriber does not notify every subscriber that a subscriber was
          registered;
        * the entry is marked ``seed``, because **loading a scenario that
          contains an open order must not push an ``order.created`` to every
          subscriber**. Compared with ``is True`` and not for truthiness: a
          vendor writing ``{"seed": "default.seed.json"}`` into its hydrate
          metadata is naming a file, not asserting a flag;
        * mapping raised, which is logged against the entry's ``seq`` and
          swallowed, because one unmappable mutation must not stop the journal.
        """

        def listener(entry: JournalEntry) -> None:
            if not self.enabled:
                return
            ctx = self._get_context()
            if ctx.vendor.events is None or ctx.vendor.signer is None:
                return
            if not ctx.capabilities.is_enabled(CoreCapability.WEBHOOKS.value):
                return
            if entry.collection == SUBSCRIPTION_COLLECTION:
                return
            if entry.meta is not None and entry.meta.get("seed") is True:
                return
            try:
                events = self._prepare(entry, ctx)
            except Exception as exc:
                ctx.log.error("event mapping failed", {"seq": entry.seq, "error": f"{type(exc).__name__}: {exc}"})
                return
            for event in events:
                self.enqueue(event)

        self._store.on_journal(listener)

    # -- preparation ---------------------------------------------------------

    def _prepare(self, entry: JournalEntry, ctx: UnitContext) -> tuple[PreparedEvent, ...]:
        """Ask the vendor what this mutation means, then assign ids and stamps.

        Two phases, and the split is the vendor contract: the id belongs to the
        dispatcher, because it must be stable across retries for a consumer to
        deduplicate on, while its position inside the envelope belongs to the
        vendor.
        """
        mapper = ctx.vendor.events
        if mapper is None:  # pragma: no cover - the listener checked first
            return ()
        out: list[PreparedEvent] = []
        for mapped in mapper.map(entry, ctx):
            event_id = mapped.event_id if mapped.event_id is not None else self._mint_event_id(entry, mapped.type)
            created_at = ctx.clock.iso_ms()
            out.append(
                PreparedEvent(
                    type=mapped.type,
                    event_id=event_id,
                    entity_id=mapped.entity_id,
                    created_at=created_at,
                    body=mapped.build(EventMeta(event_id=event_id, created_at=created_at)),
                )
            )
        return tuple(out)

    def _mint_event_id(self, entry: JournalEntry, event_type: str) -> str:
        """Deterministic event ids, ported from ``dispatcher.ts:197``.

        Two runs of the same scenario produce the same ids, which makes a
        webhook transcript diffable evidence rather than noise. The digest
        covers the type, the collection, the entity id, the journal sequence
        and a dispatcher-local counter; the counter is what separates two
        events derived from the *same* journal entry, which a vendor mapping one
        mutation onto two event types produces.

        Formatted into the five UUID groups rather than left as a hex string
        because consumers of the reference vendor's webhooks expect a
        UUID-shaped ``event_id``, and the shape is the only part of that
        expectation the core can honour without knowing the vendor. It is
        deliberately *not* a real UUID: nothing here claims a version or a
        variant nibble, and a consumer that parses it as a v4 will find it is
        not one.
        """
        with self._request_lock:
            self._event_seq += 1
            seq = self._event_seq
        digest = sha256_hex(f"{event_type}|{entry.collection}|{entry.id}|{entry.seq}|{seq}")
        return "-".join((digest[0:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]))

    # -- the synchronous prologue -------------------------------------------

    def enqueue(self, event: PreparedEvent) -> None:
        """Fan one event out to every subscriber that asked for its type.

        Runs on the request thread and completes the whole preparation of each
        first attempt before returning; see the module docstring. A subscriber
        whose ``enabled`` is false is skipped here rather than at send time, so
        a disabled subscriber produces no delivery record at all rather than a
        record explaining that it was disabled.
        """
        with self._request_lock:
            self._prepared.append(event)
            matching = [s for s in self.subscriptions() if s.enabled and matches_event_type(s.event_types, event.type)]
            for subscription in matching:
                self._queue(event, subscription)

    def enqueue_to(self, event: PreparedEvent, subscription_id: str) -> None:
        """Deliver one event to exactly one subscriber, with no fan-out.

        For the routes that name their recipient rather than describing it --
        TestWebhookSubscription is the one -- where a broadcast would send a
        subscriber an event nobody asked it for, signed with its own key so it
        looks genuine, and would leave the caller reading somebody else's
        status code back.

        The event type is *not* matched against ``event_types``: the caller
        named this subscription explicitly, and filtering a targeted send would
        report the subscriber as silent when it was simply never asked. A
        disabled subscriber is still skipped, so it records no delivery at all
        -- the same rule :meth:`enqueue` applies.
        """
        with self._request_lock:
            self._prepared.append(event)
            target = next((s for s in self.subscriptions() if s.id == subscription_id), None)
            if target is None or not target.enabled:
                return
            self._queue(event, target)

    def _queue(self, event: PreparedEvent, subscription: Subscription) -> None:
        """Prepare and schedule one subscriber's first attempt.

        The caller holds ``_request_lock``.
        """
        self._apply_chaos_and_schedule(
            _Queued(
                event=event,
                subscription=subscription,
                retry_number=0,
                initial_delivery_at=self._clock.iso_ms(),
                drop_ack=False,
                retry_reason=None,
                chaos_applied=(),
            )
        )

    def _apply_chaos_and_schedule(self, queued: _Queued) -> None:
        """Ported from ``dispatcher.ts:218``, with two threading changes.

        The chaos gate is ``webhooks.chaos`` and it is applied by the single
        fault-selection choke point, which is why this method calls
        ``selector.select_webhook`` rather than the engine.

        ``held_for_reorder`` is ONE slot, released on the *next* enqueue
        regardless of event type, and the release is scheduled **after** the
        duplicate copies. That order is what produces the delivered-version
        sequence the reference's reordering test asserts, and it is ported
        literally rather than tidied.

        The two outcomes that never touch the sink -- ``skipped`` and
        ``dropped`` -- are submitted to the delivery worker as terminal jobs
        instead of being recorded here, so that the log keeps one writer.
        """
        chaos_applied: list[str] = []
        delay_ms = 0.0
        copies = 1

        decision = self._selector.select_webhook(
            ChaosSubject(
                scope="webhook",
                event_type=queued.event.type,
                path=queued.subscription.notification_url,
            )
        )
        if decision is not None:
            chaos_applied.append(f"{decision.rule_id}:{decision.fault}")
            params = decision.params
            if decision.fault == "webhook.duplicate":
                copies = 1 + as_int(params.get("copies"), DEFAULT_DUPLICATE_COPIES)
            elif decision.fault == "webhook.delay":
                delay_ms = as_float(params.get("delay_ms"), DEFAULT_WEBHOOK_DELAY_MS)
            elif decision.fault == "webhook.drop_ack":
                queued = replace(queued, drop_ack=True)
            elif decision.fault == "webhook.out_of_order":
                # Hold this event back until the next one has gone out.
                held = replace(queued, chaos_applied=tuple(chaos_applied))
                self._held_for_reorder = held
                self._submit_terminal(held, "skipped", "held for out-of-order delivery")
                return
            elif decision.fault == "webhook.drop":
                # Never touches the sink: recorded so a test can see it
                # happened, but the subscriber gets nothing and no retry is
                # scheduled.
                dropped = replace(queued, chaos_applied=tuple(chaos_applied))
                self._submit_terminal(dropped, "dropped", "dropped by chaos rule (webhook.drop)")
                return

        release = self._held_for_reorder
        self._held_for_reorder = None

        for index in range(copies):
            applied = tuple(chaos_applied) if index == 0 else (*chaos_applied, "duplicate-copy")
            copy_of = replace(queued, chaos_applied=applied)
            attempt = self._build_attempt(copy_of)
            if attempt is None:
                continue
            if delay_ms > 0:
                self._schedule(attempt, delay_ms, f"{_TIMER_PREFIX}:{queued.event.event_id}")
            else:
                self._worker.submit(partial(self._run_attempt, attempt))

        if release is not None:
            released = replace(release, chaos_applied=("released-after-reorder",))
            attempt = self._build_attempt(released)
            if attempt is not None:
                self._worker.submit(partial(self._run_attempt, attempt))

    def _build_attempt(self, queued: _Queued) -> _Attempt | None:
        """Serialise, sign, and collect headers. The whole vendor surface, here.

        Returns ``None`` when the vendor has no signer, matching the
        reference's ``if (!signer) return``: a vendor with no signing scheme
        has no way to produce a delivery a consumer could verify, and sending
        an unsigned one would be worse than sending none.

        Header order is provider first, signature second, so that a provider
        which accidentally names a signature header cannot overwrite the
        signature. The core contributes nothing to the mapping.
        """
        ctx = self._get_context()
        signer = ctx.vendor.signer
        if signer is None:
            return None
        body = dump_json(queued.event.body)
        meta = DeliveryMetadata(
            event=queued.event,
            subscription_id=queued.subscription.id,
            notification_url=queued.subscription.notification_url,
            attempt=queued.retry_number + 1,
            retry_number=queued.retry_number,
            retry_reason=queued.retry_reason,
            initial_delivery_at=queued.initial_delivery_at,
        )
        headers: dict[str, str] = dict(signer.headers(meta))
        headers.update(
            signer.sign(
                SignInput(
                    notification_url=queued.subscription.notification_url,
                    raw_body=body,
                    secret=queued.subscription.signature_key,
                    attempt=queued.retry_number + 1,
                    event=queued.event,
                )
            )
        )
        return _Attempt(queued=queued, body=body, body_text=body.decode("utf-8"), headers=headers)

    def _schedule(self, attempt: _Attempt, delay_ms: float, label: str) -> None:
        """Put a built attempt on the clock. The callback only submits.

        Trivial on purpose: the callback runs on whichever thread fires the
        timer, which for a virtual clock is ``Clock.advance``'s caller, and
        anything slower than an enqueue there would stretch a re-scan loop.
        """
        self._clock.after(delay_ms, label, partial(self._worker.submit, partial(self._run_attempt, attempt)))

    def _submit_terminal(self, queued: _Queued, status: str, error: str) -> None:
        """Record an outcome that never reaches the sink, on the worker.

        Through the queue rather than directly so that the delivery log's ids
        and order stay a function of the scenario rather than of thread
        scheduling: a ``dropped`` recorded from the request thread would take a
        ``dlv_`` number out from under an attempt already in flight.
        """
        self._worker.submit(partial(self._record, queued, status, 0, error=error))

    # -- the worker's half ---------------------------------------------------

    def _run_attempt(self, attempt: _Attempt) -> None:
        """Send once, record the outcome, and schedule the retry if there is one.

        Runs on the delivery worker. Everything it does happens before the
        worker clears its busy flag, so a ``quiesce()`` that returns has
        observed both the record and the next retry's timer -- which is the
        property ``Clock.advance(settle=...)`` relies on to collapse a retry
        cascade into one call.

        ``drop_ack`` is applied *after* the send, not instead of it: the
        subscriber really answered, and the point of the fault is that the
        acknowledgement was lost in transit rather than never sent. Its
        ``response_status`` on the record is therefore the real 200, which is
        what makes the fault distinguishable from an outage.
        """
        queued = attempt.queued
        result = self._sink.send(
            SinkRequest(
                url=queued.subscription.notification_url,
                headers=attempt.headers,
                body=attempt.body,
                timeout_ms=self._retry.timeout_ms,
            )
        )
        ok = not queued.drop_ack and 200 <= result.status < 300
        outcome = DeliveryOutcome.of(result.status, timed_out=result.timed_out)

        if ok:
            self._record(queued, "delivered", result.status, headers=attempt.headers, body_text=attempt.body_text)
            return

        if schedule_exhausted(self._retry, queued.retry_number):
            self._record(
                queued,
                "exhausted",
                result.status,
                error=result.error if result.error is not None else "retry schedule exhausted",
                headers=attempt.headers,
                body_text=attempt.body_text,
            )
            return

        delay = retry_delay_ms(self._retry, queued.retry_number)
        error = result.error
        if error is None and queued.drop_ack:
            error = "acknowledgement dropped by chaos rule"
        self._record(
            queued,
            "failed",
            result.status,
            error=error,
            headers=attempt.headers,
            body_text=attempt.body_text,
            next_attempt_in_ms=delay,
        )
        nxt = replace(
            queued, retry_number=queued.retry_number + 1, retry_reason=outcome, drop_ack=False, chaos_applied=("retry",)
        )
        built = self._build_attempt(nxt)
        if built is None:  # pragma: no cover - a signer cannot vanish mid-flight
            return
        self._schedule(built, delay, f"{_TIMER_PREFIX}-retry:{queued.event.event_id}#{nxt.retry_number}")

    def _record(
        self,
        queued: _Queued,
        status: str,
        response_status: int,
        *,
        error: str | None = None,
        headers: Mapping[str, str] | None = None,
        body_text: str = "",
        next_attempt_in_ms: int | None = None,
    ) -> None:
        """Append one record. The only writer, and only from the worker thread.

        ``body_hash`` is the hash of the delivered *text* and is empty when
        there was no delivery -- a ``skipped`` record has nothing to hash, and
        hashing the empty string would give every one of them the same
        plausible-looking digest.
        """
        with self._log_lock:
            self._delivery_seq += 1
            delivery_id = f"dlv_{self._delivery_seq:05d}"
        body, body_is_json = _parse_body(body_text)
        record = DeliveryRecord(
            id=delivery_id,
            event_id=queued.event.event_id,
            event_type=queued.event.type,
            entity_id=queued.event.entity_id,
            subscription_id=queued.subscription.id,
            url=queued.subscription.notification_url,
            attempt=queued.retry_number + 1,
            retry_number=queued.retry_number,
            at=self._clock.iso_ms(),
            status=status,
            response_status=response_status,
            body_hash=sha256_hex(body_text) if body_text else "",
            body_preview=body_text[:_BODY_PREVIEW_LIMIT],
            headers=dict(headers) if headers is not None else {},
            body=body,
            body_is_json=body_is_json,
            chaos=queued.chaos_applied,
            error=error,
            next_attempt_in_ms=next_attempt_in_ms,
        )
        with self._log_lock:
            self._log.append(record)
        if self._on_log is not None:
            self._on_log(record.copy())

    # -- settling ------------------------------------------------------------

    def quiesce(self, timeout: float | None = None) -> bool:
        """The worker's handshake, re-exported so ``Clock.advance`` can take it.

        Passed as ``settle=`` and therefore called with the clock's lock
        released; see ``webhooks/worker.py`` for the full lock-order argument.
        """
        return self._worker.quiesce(timeout)

    def drain(self, *, timeout_ms: float | None = None) -> None:
        """Settle everything: in-flight attempts AND retries still on the clock.

        Both halves, because without the second a test asserting on the retry
        schedule would have to sleep for a guessed duration and hope. The loop
        is the reference's (``dispatcher.ts:121``) with the worker handshake
        substituted for its ``await Promise.all(inFlight)``:

        1. wait for the worker -- queue empty and no job running;
        2. ask the clock for pending timers whose label is ours;
        3. if there are none, everything has settled and we are done;
        4. otherwise move to the earliest one -- by advancing the virtual clock
           (passing :meth:`quiesce` so the re-scan sees the retry the worker
           registers) or by sleeping on a real one -- and go round again.

        The bound is passes and not wall time by default, matching the
        reference; ``timeout_ms`` adds a wall-clock ceiling for a caller that
        would rather return with work outstanding than wait, which is what a
        vendor's "send a test event and tell me what happened" route wants.

        This blocks on machinery another request must feed, so every route that
        calls it must declare ``serialized=False``. That is not a style rule:
        under the pipeline's request lock, a drain against an unreachable
        subscriber would hold the whole unit for ``timeout_ms`` times the retry
        schedule.
        """
        deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000.0
        for _ in range(_DRAIN_PASSES):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return
            if not self._worker.quiesce(remaining):
                return
            pending = [t for t in self._clock.pending() if t.label.startswith(_TIMER_PREFIX)]
            if not pending:
                return
            next_due = max(0.0, min(t.due_in_ms for t in pending))
            if self._clock.mode == "virtual":
                self._clock.advance(next_due, settle=self.settle)
            else:
                time.sleep(min(max(next_due + 1.0, 1.0), _REAL_MODE_POLL_MS) / 1000.0)

    def settle(self) -> None:
        """The callable to hand to ``Clock.advance(ms, settle=...)``.

        Public and named, because the control plane's
        ``POST /__unit/clock/advance`` must pass it: that route advances the
        clock *without* a preceding drain, and without this hook the re-scan
        would run before the worker had registered the retry it is about to
        schedule -- so a twelve-attempt cascade would report three and the
        route would answer as though the subscriber had stopped failing.

        A separate method from :meth:`quiesce` because ``advance`` takes
        ``Callable[[], None]`` and ``quiesce`` returns a bool. The verdict is
        discarded deliberately: with no timeout there is nothing to report, and
        swallowing a bool here is clearer than a lambda that pretends there
        never was one.
        """
        self._worker.quiesce()

    def stop(self) -> None:
        """Stop accepting new deliveries and let the worker thread finish.

        Called from ``Unit.stop`` after its drain. Idempotent, and it does not
        drain: a caller that wants everything delivered calls :meth:`drain`
        first, and one that is shutting down after a failure should not be made
        to wait for a subscriber that is the reason it is shutting down.
        """
        self._worker.stop()

    def worker_failures(self) -> tuple[str, ...]:
        """Delivery jobs that raised. Empty is the only acceptable value, and a
        test asserts it -- a swallowed exception on the worker would otherwise
        present as a missing delivery record."""
        return self._worker.failures()


def _parse_body(body_text: str) -> tuple[Any, bool]:
    """Parse the delivered payload back, reporting whether it was JSON.

    A vendor whose payload is not JSON keeps ``body_preview`` only, which is
    the reference's behaviour; the second element is what lets the record say
    "not JSON" rather than "the JSON document ``null``".
    """
    if not body_text:
        return None, False
    try:
        return json.loads(body_text), True
    except ValueError:
        return None, False
