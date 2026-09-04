"""Webhook delivery, derived from the journal, so an event exists only if the mutation
behind it committed. Delivery is at-least-once and every attempt carries the same
``event_id``, the consumer's dedup handle. **The core sends no delivery headers of its
own**: ``signer.sign(...)`` for the signature, ``signer.headers(meta)`` for the rest.
**The delivery log has exactly one writer**, the worker thread, ids being ``dlv_00001``
in write order. :meth:`enqueue` runs on the request thread and completes each first
attempt's preparation but does not send, so :meth:`prepared` is populated once the
request returns while a record is only observable after :meth:`drain`.
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
    BodyEncodingSigner,
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
"""Every timer this module schedules is labelled with this prefix, which is how
:meth:`WebhookDispatcher.drain` decides the unit has settled."""

_DRAIN_PASSES = 500
"""Bound on :meth:`WebhookDispatcher.drain`'s loop, so a drain cannot become a hang."""

_REAL_MODE_POLL_MS = 250.0
_FIRST_ATTEMPT_POLL_S = 0.005
"""How long :meth:`WebhookDispatcher.drain` sleeps at most between passes, real clock."""

_BODY_PREVIEW_LIMIT = 400

DEFAULT_DUPLICATE_COPIES = 1
DEFAULT_WEBHOOK_DELAY_MS = 50.0


@dataclass(frozen=True, slots=True)
class _Queued:
    event: PreparedEvent
    subscription: Subscription
    retry_number: int
    initial_delivery_at: str
    #: Set by ``webhook.drop_ack``: send for real, then discard the answer.
    drop_ack: bool
    retry_reason: DeliveryOutcome | None
    chaos_applied: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Attempt:
    queued: _Queued
    body: bytes
    body_text: str
    headers: Mapping[str, str]


class WebhookDispatcher:
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
        #: Written only on the worker thread; read under `_log_lock`.
        self._log: list[DeliveryRecord] = []
        self._log_lock = threading.Lock()
        self._delivery_seq = 0
        #: Re-entrant: `enqueue` runs inside a journal listener and may read the store.
        self._request_lock = threading.RLock()
        self._event_seq = 0
        self._held_for_reorder: _Queued | None = None
        self._prepared: list[PreparedEvent] = []
        self._enabled_flag = True

    @property
    def enabled(self) -> bool:
        """Both switches must be on; profile ``disabled`` cannot be undone at runtime."""
        return self._enabled_flag and not self._disabled

    def set_enabled(self, on: bool) -> None:
        self._enabled_flag = on

    @property
    def sink_kind(self) -> str:
        return self._sink.kind

    @property
    def sink(self) -> DeliverySink:
        """The sink itself, so ``POST /__unit/webhooks/sink`` can program a ``MemorySink``."""
        return self._sink

    @property
    def retry_policy(self) -> MutableRetryPolicy:
        return self._retry

    def set_retry_policy(self, patch: Mapping[str, Any]) -> MutableRetryPolicy:
        return self._retry.apply(patch)

    def deliveries(self) -> tuple[DeliveryRecord, ...]:
        """Every attempt, oldest first, each a private copy of the evidence."""
        with self._log_lock:
            return tuple(record.copy() for record in self._log)

    def clear_log(self) -> None:
        with self._log_lock:
            self._log.clear()

    def prepared(self) -> tuple[PreparedEvent, ...]:
        """Every event prepared for delivery, in order, populated before the request returns."""
        with self._request_lock:
            return tuple(self._prepared)

    def subscriptions(self) -> tuple[Subscription, ...]:
        return tuple(Subscription.from_entity(e) for e in self._store.collection(SUBSCRIPTION_COLLECTION).all())

    def load_config_subscribers(self, subscribers: Sequence[SubscriberConfig]) -> None:
        """Seed profile subscribers as ordinary entities; an existing id is skipped, hydrate
        running on every reset."""
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
        """Wire the dispatcher to the store journal; called once, by the kernel. The
        ``webhooks`` capability is gated inside the listener, the registry being mutable at
        runtime. Four other reasons an entry produces nothing: no mapper or no signer; it
        mutates the subscription collection, so registering a subscriber notifies nobody;
        it is marked ``seed``, compared with ``is True`` since ``{"seed": "x.json"}`` names
        a file; or mapping raised, logged and swallowed so the journal cannot stop."""

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

    def _prepare(self, entry: JournalEntry, ctx: UnitContext) -> tuple[PreparedEvent, ...]:
        """Ask the vendor what this mutation means, then assign ids and stamps: the id is
        the dispatcher's, being stable across retries, its envelope place the vendor's."""
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
        """Deterministic event ids, so two runs of one scenario produce a diffable
        transcript. The digest covers type, collection, entity id, journal sequence and a
        local counter separating two events mapped from one entry. UUID-shaped, not a UUID."""
        with self._request_lock:
            self._event_seq += 1
            seq = self._event_seq
        digest = sha256_hex(f"{event_type}|{entry.collection}|{entry.id}|{entry.seq}|{seq}")
        return "-".join((digest[0:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]))

    def enqueue(self, event: PreparedEvent) -> None:
        """Fan one event out to every subscriber that asked for its type, completing each
        first attempt's preparation first. A disabled subscriber records nothing at all."""
        if not self.enabled:
            return

        # Resolved before ``_request_lock`` is taken, for the lock order set out on
        # :meth:`enqueue_to`.
        matching = [s for s in self.subscriptions() if s.enabled and matches_event_type(s.event_types, event.type)]

        with self._request_lock:
            self._prepared.append(event)
            for subscription in matching:
                self._queue(event, subscription)

    def enqueue_to(self, event: PreparedEvent, subscription_id: str) -> None:
        """Deliver one event to exactly one subscriber, for the routes that name their
        recipient. The event type is *not* matched against ``event_types``, the caller
        having named this subscription; a disabled subscriber is still skipped.
        ``self.enabled`` is checked here as well as in :meth:`enqueue`, which
        ``POST /__unit/webhooks/emit`` reaches without passing the listener's guard."""
        if not self.enabled:
            return

        # LOCK ORDER: the store lock is taken and released BEFORE ``_request_lock``, because
        # the journal path holds them the other way round -- ``Store.append_journal``
        # dispatches listeners under ``Store.lock``, whose listener takes ``_request_lock``.
        target = next((s for s in self.subscriptions() if s.id == subscription_id), None)
        if target is None or not target.enabled:
            return

        with self._request_lock:
            self._prepared.append(event)
            self._queue(event, target)

    def _queue(self, event: PreparedEvent, subscription: Subscription) -> None:
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
        """Apply the delivery faults, then queue or schedule the attempt. Selection goes
        through ``selector.select_webhook``, which applies the ``webhooks.chaos`` gate.
        ``held_for_reorder`` is ONE slot, released on the next enqueue whatever its type and
        after the duplicate copies. Outcomes that never touch the sink go to the worker as
        terminal jobs, so the log keeps one writer."""
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
                held = replace(queued, chaos_applied=tuple(chaos_applied))
                self._held_for_reorder = held
                self._submit_terminal(held, "skipped", "held for out-of-order delivery")
                return
            elif decision.fault == "webhook.drop":
                # Never touches the sink: recorded, but nothing is sent or retried.
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
        """Serialise, sign and collect headers -- the whole vendor surface. ``None`` when the
        vendor has no signer. Provider headers first, signature second."""
        ctx = self._get_context()
        signer = ctx.vendor.signer
        if signer is None:
            return None
        # The vendor's encoding when it declares one. These exact bytes are signed and sent.
        body = (
            signer.encode_body(queued.event) if isinstance(signer, BodyEncodingSigner) else dump_json(queued.event.body)
        )
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
        """Put a built attempt on the clock. The callback only submits, running on whichever
        thread fires the timer."""
        self._clock.after(delay_ms, label, partial(self._worker.submit, partial(self._run_attempt, attempt)))

    def _submit_terminal(self, queued: _Queued, status: str, error: str) -> None:
        """Record an outcome that never reaches the sink, on the worker, so the ids stay ordered."""
        self._worker.submit(partial(self._record, queued, status, 0, error=error))

    def _run_attempt(self, attempt: _Attempt) -> None:
        """Send once, record the outcome, schedule the retry if there is one. Runs entirely
        before the worker's busy flag clears, so a returning ``quiesce()`` has seen both the
        record and the next retry's timer. ``drop_ack`` is applied *after* the send, the
        record keeping the real status that distinguishes it from an outage."""
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
        """Append one record, from the worker thread only. ``body_hash`` is empty, not the
        hash of the empty string, when there was no delivery."""
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

    def quiesce(self, timeout: float | None = None) -> bool:
        return self._worker.quiesce(timeout)

    def drain(self, *, timeout_ms: float | None = None) -> None:
        """Settle everything: in-flight attempts AND retries still on the clock. Each pass
        waits for the worker, then moves to the earliest pending timer of ours, advancing a
        virtual clock with :meth:`settle` or sleeping on a real one. The bound is passes,
        not wall time; ``timeout_ms`` adds a ceiling. Every route calling this must declare
        ``serialized=False``, or a drain on an unreachable subscriber holds the unit."""
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

    def await_first_attempt(self, event_id: str, subscription_id: str, *, timeout_ms: float) -> DeliveryRecord | None:
        """The first delivery record for ``event_id`` to ``subscription_id``, or ``None`` after
        ``timeout_ms``. Never moves the clock, so a caller waits for one attempt at most."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            for record in self.deliveries():
                if record.event_id == event_id and record.subscription_id == subscription_id:
                    return record
            if time.monotonic() >= deadline:
                return None
            time.sleep(_FIRST_ATTEMPT_POLL_S)

    def settle(self) -> None:
        """The callable to hand to ``Clock.advance(ms, settle=...)``, which
        ``POST /__unit/clock/advance`` must pass: it advances without a preceding drain, so
        without this a twelve-attempt cascade reports three."""
        self._worker.quiesce()

    def stop(self) -> None:
        """Stop accepting new deliveries and let the worker finish; idempotent, and it does
        not drain."""
        self._worker.stop()

    def worker_failures(self) -> tuple[str, ...]:
        return self._worker.failures()


def _parse_body(body_text: str) -> tuple[Any, bool]:
    """Parse the payload back; the flag separates "not JSON" from "the JSON document null"."""
    if not body_text:
        return None, False
    try:
        return json.loads(body_text), True
    except ValueError:
        return None, False
