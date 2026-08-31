"""The ``/__unit/*`` control plane: the same thirty routes for every vendor.

FOR: making everything a consumer needs in order to *drive* a fake -- what it
is, what it can do, what it has recorded, what it will do wrong next, and how
to put it back -- reachable over the same channel as the vendor's own API,
with no second port, no client library and no in-process object graph.

INVARIANT: **anything a conformance check needs to observe is observable
here.** That is what makes the suite a specification rather than a Python
artifact: a check drives a unit through a URL and asserts on what comes back,
so the same checks can one day be executed by a non-Python consumer against a
container. The moment a check has to reach for a ``Unit`` object, the contract
it tests has stopped being a contract about the *unit* and become a contract
about this codebase.

Namespaced under ``/__unit/`` because no real vendor serves a path segment
beginning with a double underscore, and ``kernel/router.py`` refuses any vendor
route that tries. Keeping it inside the same unit rather than on a second port
means a consumer's existing base URL reaches it with no extra plumbing.

Every route here is ``internal=True``, which makes the kernel skip auth, chaos
and idempotency. That is not a convenience: a control call must never be the
thing that trips the fault it is trying to configure, and a chaos rule matching
``*`` would otherwise make a unit unrecoverable through its own control plane.

THE THREE ROUTES THAT DECLARE ``serialized=False``
--------------------------------------------------
``POST /__unit/webhooks/drain`` and ``POST /__unit/clock/advance`` block inside
the handler on machinery *another request must feed*. The reference gets away
without the distinction because Node's event loop yields at every ``await``; a
real lock does not, and either route would hold the whole unit for the full
delivery timeout against an unreachable subscriber. The third is a vendor's
"send a test event and tell me what happened", which is phase 4's to declare.
The store, the delivery log and the clock each keep their own lock; the request
lock exists only so that id minting and journal ordering are deterministic,
which is exactly what those two routes do not touch.

``POST /__unit/clock/advance`` PASSES ``settle=``, AND MAY BE ASKED NOT TO DRAIN
--------------------------------------------------------------------------------
The reference does ``await clock.advance(ms)`` then ``await webhooks.drain()``.
Here deliveries run on one worker thread, so ``advance``'s re-scan can run
*before* the worker has registered the retry it is about to schedule -- and a
twelve-attempt cascade would report three while the route answered as though
the subscriber had stopped failing. ``ctx.webhooks.settle`` is the handshake
that closes that window, and it is why the dispatcher publishes ``settle`` as a
named public method rather than hiding it.

The drain that follows is the reference's and stays the default, but it is a
flag: draining a *virtual* clock means advancing to every pending timer in
turn, so "advance ten milliseconds" can run a cascade spanning the whole
declared retry schedule. ``{"drain": false}`` advances by exactly what was
asked, fires only what that made due, and settles the worker -- which is the
only way to observe that a retry did NOT happen before its interval.

NINE ROUTES THE REFERENCE DOES NOT HAVE
---------------------------------------
``GET /__unit/errors``
    The vendor's shaping of every one of the twenty core error kinds, read over
    the wire instead of by importing the vendor's table -- each row with the
    provenance of its status, from ``ErrorShaper.describe``.
``GET /__unit/machines`` and ``POST /__unit/machines/probe``
    Declared lifecycles, with ``terminal`` derived from ``to == []`` in the
    machine itself, and a way to evaluate a transition without mutating
    anything. The reference's state machine is a module-level singleton that
    nothing registers, so there is no data source at all without
    ``VendorDefinition.machines``.
``POST /__unit/echo``
    Any content type in, the parsed fields and both query views out. This is what makes the
    form-encoded-body guarantee testable on a profile whose vendor has no
    form-accepting route -- the point being that vendor #2 inherits the
    guarantee rather than vendor #1 happening to own an OAuth endpoint.
``POST /__unit/webhooks/emit`` and ``POST /__unit/webhooks/sink``
    An emitter, so a profile with no mutating route can still make a delivery
    happen, and a way to program the memory sink's next answers, so a forced
    retry can be driven from outside the process.
``GET /__unit/auth``
    Credentials that would authenticate right now. Without it a consumer -- or
    a conformance check -- can read that a route requires a bearer token and
    has no way whatever to obtain one, so the entire authentication layer is
    describable and undrivable.
``POST /__unit/state/update`` and ``POST /__unit/state/page``
    The store's write path and its cursor, reached without a vendor body.
    Optimistic concurrency and the cursor's query fingerprint are rules of the
    CORE; asking them only through whichever endpoint a vendor happens to
    expose would make them contracts about that vendor.

WIRE CASING IS snake_case, HERE AS EVERYWHERE
---------------------------------------------
The reference spells five response keys in camelCase (``uptimeMs``,
``displayName``, ``apiVersion``, ``journalSeq``, ``firedTimers``) and several
request keys likewise. This build chose one convention across profile
documents, environment variables, chaos rules, route reports, retry policies
and delivery records in earlier stages; ``RouteInfo.as_json``,
``MutableRetryPolicy.as_json`` and ``DeliveryRecord.as_json`` were already
written that way and are already tested. Carrying camelCase at the top level of
a document whose nested values are snake_case would be worse than either
convention on its own. Paths are byte-identical to the reference; keys are
snake_case. Recorded as ``provenance: judgment``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vendorfake.core.capability.gates import CORE_GATED_CAPABILITIES
from vendorfake.core.capability.registry import CONTROL_CAPABILITY, apply_capability_delta
from vendorfake.core.chaos.rules import BUILTIN_FAULTS, ChaosRule, matched_routes, parse_rule
from vendorfake.core.control.schemas import (
    CapabilitiesBody,
    ChaosResetBody,
    ChaosRulesBody,
    ClockAdvanceBody,
    MachineProbeBody,
    RetryPolicyPatchBody,
    SinkProgramBody,
    StatePageBody,
    StateRestoreBody,
    StateUpdateBody,
    SubscriptionCreateBody,
    WebhookEmitBody,
    journal_entry_as_json,
    parse_or_raise,
    require_finite,
    snapshot_as_json,
)
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    MagicTriggerSpec,
    PreparedEvent,
    ReplyInit,
    Route,
    Signer,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.kernel.unit import ControlBinding
from vendorfake.core.state.machine import MachineDef, StateMachine
from vendorfake.core.time.clock import PendingTimer
from vendorfake.core.util.json import compact, sha256_hex
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION, Subscription
from vendorfake.core.webhooks.sink import MemorySink

__all__ = ["CONTROL_PREFIX", "control_plane_routes"]

CONTROL_PREFIX = "/__unit/"
"""Every path below begins with this. Restated from ``kernel/router.py`` for a
reader; the router owns the enforcement."""

_MEMORY_SINK_KIND = "memory"
"""The one sink ``POST /__unit/webhooks/sink`` can program. Compared against
:attr:`MemorySink.kind` by a test, so a rename cannot silently orphan it."""


def control_plane_routes(
    binding: ControlBinding,
    *,
    framework_answered: Callable[[], int] | None = None,
) -> tuple[Route, ...]:
    """Build the control plane against one unit's :class:`ControlBinding`.

    A factory rather than a module-level table because two of the routes need
    unit internals that a route handler must not have -- re-seeding the store
    and enumerating the router -- and the binding is the enumerable list of
    exactly those. The reference reached them through a
    ``WeakMap<UnitContext, ControlBinding>``; a typed argument is the same
    guarantee without a global side table.

    ``framework_answered`` is the transport adapter's tripwire: a counter of
    requests the *web framework* answered by itself instead of handing to the
    unit. It is reported at ``/__unit/health`` rather than kept in the serving
    process, because a list inside a uvicorn child is unreadable from the
    parent of an out-of-process test, and the only place the number matters is
    over HTTP. The default reports ``0``, which is not a stub but the true
    answer: with no framework in the picture there is nothing that could have
    answered.
    """
    started = time.monotonic()
    #: Distinct per-unit, so two units in one process do not mint the same
    #: synthetic event id. Boxed in a list because a closure cannot rebind.
    emitted = [0]

    def c(
        method: str,
        path: str,
        summary: str,
        handler: Callable[[HandlerArgs], ReplyInit],
        *,
        operation_id: str,
        serialized: bool = True,
    ) -> Route:
        return Route(
            method=method,
            path=path,
            capability=CONTROL_CAPABILITY,
            handler=handler,
            internal=True,
            summary=summary,
            operation_id=operation_id,
            serialized=serialized,
        )

    # -- identity ----------------------------------------------------------

    def health(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        return json_(
            {
                "status": "ok",
                "vendor": ctx.vendor.name,
                "profile": ctx.config.profile,
                "uptime_ms": round((time.monotonic() - started) * 1000),
                "framework_answered": 0 if framework_answered is None else framework_answered(),
            }
        )

    def info(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        route_keys = _chaos_route_keys(binding)
        return json_(
            compact(
                {
                    "vendor": compact(
                        {
                            "name": ctx.vendor.name,
                            "display_name": ctx.vendor.display_name,
                            "api_version": ctx.vendor.api_version,
                        }
                    ),
                    "profile": ctx.config.profile,
                    "capabilities": [view.as_json() for view in ctx.capabilities.view()],
                    "not_supported": dict(ctx.vendor.not_supported),
                    "auth": dict(ctx.vendor.auth.describe()),
                    "signer": None if ctx.vendor.signer is None else _signer_as_json(ctx.vendor.signer),
                    "magic": _magic_as_json(ctx.vendor.magic),
                    "chaos": {
                        "seed": ctx.config.chaos.seed,
                        "enabled": ctx.chaos.is_enabled,
                        "strict_rules": ctx.config.chaos.strict_rules,
                        "rules": _rules_as_json(ctx, route_keys),
                        "faults": [fault.as_json() for fault in BUILTIN_FAULTS],
                    },
                    "webhooks": {
                        "enabled": ctx.webhooks.enabled,
                        "sink": ctx.webhooks.sink_kind,
                        "retry": ctx.webhooks.retry_policy.as_json(),
                        "subscriptions": len(ctx.webhooks.subscriptions()),
                    },
                    "clock": {
                        "mode": ctx.clock.mode,
                        "now": ctx.clock.iso_ms(),
                        "pending_timers": [_timer_as_json(timer) for timer in ctx.clock.pending()],
                    },
                    "state": {
                        "entities": ctx.store.stats(),
                        "journal_seq": ctx.store.journal_seq,
                        "digest": ctx.store.entity_digest(),
                    },
                }
            )
        )

    def routes(args: HandlerArgs) -> ReplyInit:
        table = binding.list_routes()
        return json_({"count": len(table), "routes": [row.as_json() for row in table]})

    def errors(args: HandlerArgs) -> ReplyInit:
        """Every core error kind, shaped by *this* vendor, read over the wire.

        The whole of C05's data source. The kinds are enumerated from the enum
        rather than from the vendor's table, so a vendor that has forgotten one
        answers with the shape it produces for an unknown kind -- or fails
        loudly here -- instead of simply not appearing in the report.
        """
        ctx = args.ctx
        # Provenance comes from `describe()`, never from the shaped body: the
        # sidecar that would carry it there is switchable, and a consumer who
        # turned it off still gets to ask which statuses the vendor documents.
        described = ctx.vendor.errors.describe()
        shaped: list[dict[str, Any]] = []
        for kind in UnitErrorKind:
            result = ctx.vendor.errors.shape(UnitError(kind, detail=f"conformance probe for {kind.value}"), ctx)
            provenance = described.get(kind.value, {}).get("provenance")
            if provenance is None:
                # Unreachable after the unit's startup check of describe();
                # a 500 that says what is missing rather than a 200 with null.
                raise UnitError(
                    UnitErrorKind.INTERNAL,
                    detail=f"ErrorShaper.describe() publishes no provenance for {kind.value!r}; the unit's "
                    "startup check should have refused this vendor.",
                    info={"kind": kind.value},
                )
            shaped.append(
                {
                    "kind": kind.value,
                    "status": result.status,
                    "provenance": provenance,
                    "body": result.body,
                    "headers": dict(result.headers),
                }
            )
        no_route = ctx.vendor.errors.not_found(args.req, ctx)
        return json_(
            {
                "count": len(shaped),
                "kinds": shaped,
                "no_route": {"status": no_route.status, "body": no_route.body, "headers": dict(no_route.headers)},
            }
        )

    # -- capabilities ------------------------------------------------------

    def capabilities_get(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        return json_(
            {
                "profile": ctx.config.profile,
                "capabilities": [view.as_json() for view in ctx.capabilities.view()],
                "not_supported": dict(ctx.vendor.not_supported),
                "core_gates": [gate.as_json() for gate in CORE_GATED_CAPABILITIES],
            }
        )

    def capabilities_post(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(CapabilitiesBody, args.body(), source="POST /__unit/capabilities")
        # Order is the reference's and it is contract; see CapabilitiesBody.
        if body.set is not None:
            ctx.capabilities.set_enabled(body.set)
        if body.delta is not None:
            ctx.capabilities.set_enabled(apply_capability_delta(ctx.capabilities.enabled_names(), body.delta))
        for name in body.enable or ():
            ctx.capabilities.enable(name)
        for name in body.disable or ():
            ctx.capabilities.disable(name)
        return json_({"capabilities": [view.as_json() for view in ctx.capabilities.view()]})

    # -- chaos -------------------------------------------------------------

    def chaos_get(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        return json_(
            {
                "enabled": ctx.chaos.is_enabled,
                "seed": ctx.config.chaos.seed,
                "strict_rules": ctx.config.chaos.strict_rules,
                "rules": _rules_as_json(ctx, _chaos_route_keys(binding)),
                "events": [event.as_json() for event in ctx.chaos.events()],
                "faults": [fault.as_json() for fault in BUILTIN_FAULTS],
            }
        )

    def chaos_rules_post(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(ChaosRulesBody, args.body(), source="POST /__unit/chaos/rules")
        document = body.rule_document()
        if body.rules is not None and document is not None:
            # Stricter than the reference's `else if`, which honours `rules`
            # and silently drops the bare rule. Two instructions in one body is
            # a caller who does not know which one will win, and finding out
            # from a transcript is worse than finding out from a 400.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Send either 'rules' (replace the whole set) or one bare rule object, not both.",
                field="rules",
                info={"bare_rule_fields": sorted(document)},
            )
        if body.enabled is not None:
            ctx.chaos.set_enabled(body.enabled)
        route_keys = _chaos_route_keys(binding)
        if body.rules is not None:
            parsed = [_validated_rule(document, ctx, route_keys) for document in body.rules]
            ctx.chaos.replace(parsed)
        elif document is not None:
            ctx.chaos.add(_validated_rule(document, ctx, route_keys))
        return json_({"rules": _rules_as_json(ctx, route_keys)})

    def chaos_rule_delete(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        rule_id = args.params["id"]
        if not ctx.chaos.remove(rule_id):
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"chaos rule {rule_id!r} not found",
                field="id",
                info={"id": rule_id},
            )
        return json_({"rules": _rules_as_json(ctx, _chaos_route_keys(binding))})

    def chaos_reset(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(ChaosResetBody, args.body(), source="POST /__unit/chaos/reset")
        if body.keep_rules:
            ctx.chaos.reset_counters()
        else:
            ctx.chaos.reset()
        return json_({"rules": _rules_as_json(ctx, _chaos_route_keys(binding))})

    # -- state -------------------------------------------------------------

    def journal(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        since = _since(args.query("since"))
        entries = ctx.store.journal(since)
        return json_(
            {
                "seq": ctx.store.journal_seq,
                "since": since,
                "count": len(entries),
                "entries": [journal_entry_as_json(entry) for entry in entries],
            }
        )

    def state(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        return json_(
            {
                "entities": ctx.store.stats(),
                "journal_seq": ctx.store.journal_seq,
                "digest": ctx.store.entity_digest(),
            }
        )

    def state_snapshot(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        return json_({"digest": ctx.store.entity_digest(), "snapshot": snapshot_as_json(ctx.store.snapshot())})

    def state_restore(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(StateRestoreBody, args.body(), source="POST /__unit/state/restore")
        if body.snapshot is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="snapshot is required",
                field="snapshot",
            )
        ctx.store.restore(body.snapshot.to_snapshot())
        return json_(
            {
                "entities": ctx.store.stats(),
                "journal_seq": ctx.store.journal_seq,
                "digest": ctx.store.entity_digest(),
            }
        )

    def auth_get(args: HandlerArgs) -> ReplyInit:
        """How to authenticate here, and credentials that would work right now.

        The gap this closes is not small: without it, a route table says
        ``auth: "bearer"`` and a conformance suite can assert the whole
        ``unauthorized`` row of the error table while never once sending an
        authenticated request -- which is exactly the state this control plane
        was in before. A credential has to cross the wire for authentication to
        be drivable by a consumer in another language.

        Publishing them is safe *because this is a fake*: every credential here
        is scenario data with no counterpart anywhere real, and withholding
        them would only mean each consumer copying the seed document instead.
        """
        ctx = args.ctx
        offered = list(ctx.vendor.auth.credentials(ctx))
        return json_(
            {
                "describe": dict(ctx.vendor.auth.describe()),
                "modes": sorted({credential.mode for credential in offered}),
                "count": len(offered),
                "credentials": [credential.as_json() for credential in offered],
            }
        )

    def state_update(args: HandlerArgs) -> ReplyInit:
        """One committed mutation of one entity, under optimistic concurrency.

        The store's write path, reached directly. ``version`` is passed through
        as ``expect_version``, so a stale value raises ``version_conflict`` from
        ``core/state/store.py`` and nothing is written -- which is the half of
        the journal contract no seed insert can demonstrate.

        Journalled like any other mutation, which means it is also delivered
        like any other mutation: this is a real write, not a simulation of one.
        """
        ctx = args.ctx
        body = parse_or_raise(StateUpdateBody, args.body(), source="POST /__unit/state/update")
        patch = dict(body.patch)

        def mutate(entity: dict[str, Any]) -> None:
            entity.update(patch)

        updated = ctx.store.collection(body.collection).update(body.id, mutate, expect_version=body.version)
        return json_(
            {
                "collection": body.collection,
                "id": body.id,
                "version": updated["version"],
                "journal_seq": ctx.store.journal_seq,
            }
        )

    def state_page(args: HandlerArgs) -> ReplyInit:
        """Page a collection through the store's own cursor implementation.

        Ids only, deliberately: the contract being made observable is the
        cursor's -- opaque, fingerprinted against the query it was issued for,
        expiring -- and a page of whole entities would invite a check to start
        asserting on a vendor's field names instead.
        """
        ctx = args.ctx
        body = parse_or_raise(StatePageBody, args.body(), source="POST /__unit/state/page")
        collection = ctx.store.collection(body.collection)
        page = collection.paginate(
            collection.all(),
            limit=body.limit,
            cursor=body.cursor,
            fingerprint=body.query,
        )
        return json_(
            {
                "collection": body.collection,
                "count": len(page.items),
                "ids": [str(item.get("id", "")) for item in page.items],
                "cursor": page.cursor,
            }
        )

    def state_reset(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        binding.hydrate()
        return json_(
            {
                "entities": ctx.store.stats(),
                "journal_seq": ctx.store.journal_seq,
                "digest": ctx.store.entity_digest(),
            }
        )

    # -- webhooks ----------------------------------------------------------

    def subscriptions_get(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        rows = ctx.webhooks.subscriptions()
        return json_({"count": len(rows), "subscriptions": [_subscription_as_json(sub) for sub in rows]})

    def subscriptions_post(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(SubscriptionCreateBody, args.body(), source="POST /__unit/webhooks/subscriptions")
        collection = ctx.store.collection(SUBSCRIPTION_COLLECTION)
        subscriber_id = body.id if body.id is not None else f"wbhk_ctl_{collection.size + 1:02d}"
        if collection.has(subscriber_id):
            raise UnitError(
                UnitErrorKind.CONFLICT,
                detail=f"subscription {subscriber_id!r} already exists",
                field="id",
                info={"id": subscriber_id},
            )
        # snake_case keys, and only the keys that were given a value: the
        # dispatcher's `Subscription.from_entity` is the one place these names
        # are known, and an entity carrying `"name": null` would make "absent"
        # and "explicitly nothing" indistinguishable to it.
        entity = collection.insert(
            compact(
                {
                    "id": subscriber_id,
                    "name": body.name if body.name is not None else "control-plane subscriber",
                    "notification_url": body.notification_url,
                    "event_types": list(body.event_types),
                    "signature_key": body.signature_key,
                    "enabled": body.enabled,
                    "api_version": body.api_version,
                }
            ),
            {"source": "control"},
        )
        return json_({"subscription": entity}, 201)

    def subscriptions_delete(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        subscriber_id = args.params["id"]
        if not ctx.store.collection(SUBSCRIPTION_COLLECTION).delete(subscriber_id):
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"subscription {subscriber_id!r} not found",
                field="id",
                info={"id": subscriber_id},
            )
        return json_({"deleted": subscriber_id})

    def deliveries(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        rows = ctx.webhooks.deliveries()
        wanted = args.query("event_type")
        if wanted is not None:
            rows = tuple(record for record in rows if record.event_type == wanted)
        return json_({"count": len(rows), "deliveries": [record.as_json() for record in rows]})

    def drain(args: HandlerArgs) -> ReplyInit:
        """Wait for in-flight deliveries and scheduled retries to settle.

        ``serialized=False``: this blocks on the delivery worker, which nothing
        inside this request can advance.
        """
        ctx = args.ctx
        ctx.webhooks.drain()
        return json_({"deliveries": len(ctx.webhooks.deliveries())})

    def retry_policy(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(RetryPolicyPatchBody, args.body(), source="POST /__unit/webhooks/retry-policy")
        return json_({"retry": ctx.webhooks.set_retry_policy(body.patch()).as_json()})

    def emit(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(WebhookEmitBody, args.body(), source="POST /__unit/webhooks/emit")
        emitted[0] += 1
        event_id = _synthetic_event_id(body.type, body.entity_id, emitted[0])
        created_at = ctx.clock.iso_ms()
        envelope = (
            body.body
            if body.body is not None
            else {
                "type": body.type,
                "event_id": event_id,
                "created_at": created_at,
                "data": {"id": body.entity_id},
            }
        )
        event = PreparedEvent(
            type=body.type,
            event_id=event_id,
            entity_id=body.entity_id,
            created_at=created_at,
            body=envelope,
        )
        ctx.webhooks.enqueue(event)
        return json_({"event_id": event_id, "type": body.type, "entity_id": body.entity_id}, 202)

    def sink_program(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(SinkProgramBody, args.body(), source="POST /__unit/webhooks/sink")
        sink = ctx.webhooks.sink
        if not isinstance(sink, MemorySink):
            # `conflict` and not the brief's `invalid_state`, which is not one
            # of the twenty kinds -- and the twenty are fixed by a conformance
            # check asserting the literal count. "The unit is not in a state
            # where this is possible" is what `conflict` already means.
            raise UnitError(
                UnitErrorKind.CONFLICT,
                detail=(
                    f"the delivery sink is {ctx.webhooks.sink_kind!r}; "
                    f"programming responses requires the {_MEMORY_SINK_KIND!r} sink"
                ),
                field="statuses",
                info={"sink": ctx.webhooks.sink_kind, "required": _MEMORY_SINK_KIND},
            )
        statuses = tuple(body.statuses)
        then = body.then
        # Offset from where the sink already is: `call_index` counts calls to
        # this sink for its whole life, and a programme that ignored the calls
        # already made would replay itself for whoever went first.
        base = len(sink.received)

        def responder(request: object, call_index: int) -> int:
            offset = call_index - base
            if 0 <= offset < len(statuses):
                return statuses[offset]
            return then

        sink.respond_with = responder
        return json_({"sink": ctx.webhooks.sink_kind, "statuses": list(statuses), "then": then, "from_call": base})

    # -- clock -------------------------------------------------------------

    def clock_advance(args: HandlerArgs) -> ReplyInit:
        """Move a virtual clock forward and settle whatever that set off.

        ``serialized=False``, and it passes ``settle=`` -- see the module
        docstring for both.
        """
        ctx = args.ctx
        body = parse_or_raise(ClockAdvanceBody, args.body(), source="POST /__unit/clock/advance")
        ms = require_finite(body.ms, field="ms")
        if ms < 0:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="ms must be a non-negative number",
                field="ms",
            )
        if ctx.clock.mode != "virtual":
            raise UnitError(
                UnitErrorKind.BAD_REQUEST,
                detail=(
                    'The clock is in real mode. Start the unit with clock.mode="virtual" '
                    "(VENDORFAKE_CLOCK=virtual) to control time."
                ),
                info={"mode": ctx.clock.mode},
            )
        fired = ctx.clock.advance(ms, settle=ctx.webhooks.settle)
        if body.drain:
            ctx.webhooks.drain()
        return json_(
            {
                "now": ctx.clock.iso_ms(),
                "fired_timers": fired,
                "pending": [_timer_as_json(timer) for timer in ctx.clock.pending()],
            }
        )

    # -- machines ----------------------------------------------------------

    def machines_get(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        return json_(
            {
                "count": len(ctx.vendor.machines),
                # `describe()` lives on the machine so that `terminal` is
                # derived in exactly one place; a check asserting
                # `terminal == (to == [])` over the wire then says something
                # about the enforcement rather than about this report.
                "machines": {name: StateMachine(definition).describe() for name, definition in _machines(ctx).items()},
            }
        )

    def machines_probe(args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        body = parse_or_raise(MachineProbeBody, args.body(), source="POST /__unit/machines/probe")
        declared = _machines(ctx)
        definition = declared.get(body.machine)
        if definition is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"no state machine named {body.machine!r}",
                field="machine",
                info={"machine": body.machine, "declared": sorted(declared)},
            )
        machine = StateMachine(definition)
        subject = f"machine {body.machine!r}"
        if body.from_ not in definition.states:
            # Checked here rather than left to `assert_transition`, which only
            # validates the *target*: with no `to`, an undeclared `from` would
            # sail through `assert_mutable` (an unknown state is not terminal)
            # and the probe would answer `ok` about a state that does not exist.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"'{body.from_}' is not a declared state of machine {body.machine!r}.",
                field="from",
                info={"machine": body.machine, "allowed": machine.states()},
            )
        # Nothing below touches the store: a probe answers the predicate and
        # mutates nothing, which is what makes it safe to call from a check
        # that is also asserting on the state digest.
        #
        # ONE question per call, and never both. A handler runs `assert_mutable`
        # and then `assert_transition`, in that order and for good reason -- but
        # a probe that did the same could never report the transition predicate
        # for a terminal state, because mutability would always answer first. A
        # machine that treated `from == to` as always legal would then be
        # undetectable through this route on any vendor whose non-terminal
        # states all allow themselves, which is the exact defect the route was
        # added to expose. With `to`, this asks whether the move is legal;
        # without it, whether the entity may be mutated at all.
        if body.to is None:
            machine.assert_mutable(body.from_, subject)
        else:
            machine.assert_transition(body.from_, body.to, subject)
        return json_(
            compact(
                {
                    "ok": True,
                    "machine": body.machine,
                    "from": body.from_,
                    "to": body.to,
                    "terminal": machine.is_terminal(body.from_),
                }
            )
        )

    # -- transport ---------------------------------------------------------

    def echo(args: HandlerArgs) -> ReplyInit:
        """Reflect what the body reader made of this request, and both query views.

        No capability, any content type, no vendor knowledge. It exists so that
        "a form-encoded body reaches the handler as fields" is assertable on
        every profile, including one whose vendor has no form-accepting route
        at all -- which is the difference between a guarantee vendor #2
        inherits and one that happens to hold for vendor #1's OAuth endpoint.
        """
        media_type = args.media_type()
        payload: dict[str, Any] = {
            "content_type": media_type,
            "raw_len": len(args.req.raw_body),
            "fields": {},
            "fields_multi": {},
            "query": dict(args.req.query),
            "query_all": {name: list(values) for name, values in args.req.query_all.items()},
        }
        if media_type == "application/x-www-form-urlencoded":
            form = args.form()
            payload["fields"] = dict(form)
            payload["fields_multi"] = form.multi()
        elif args.req.raw_body.strip():
            # `json` is present only when there was a JSON document to report.
            # `null` is a legitimate JSON body, so an always-present key could
            # not distinguish "the body was null" from "there was no body".
            payload["json"] = args.json()
        return json_(payload)

    return (
        c("GET", "/__unit/health", "Liveness probe.", health, operation_id="UnitHealth"),
        c("GET", "/__unit/info", "Everything needed to reproduce this unit run.", info, operation_id="UnitInfo"),
        c("GET", "/__unit/routes", "The unit surface, for docs and drift checks.", routes, operation_id="UnitRoutes"),
        c(
            "GET",
            "/__unit/errors",
            "Every core error kind as this vendor shapes it.",
            errors,
            operation_id="UnitErrors",
        ),
        c("GET", "/__unit/capabilities", "Capability state.", capabilities_get, operation_id="UnitCapabilities"),
        c(
            "POST",
            "/__unit/capabilities",
            "Toggle capabilities at runtime.",
            capabilities_post,
            operation_id="UnitSetCapabilities",
        ),
        c(
            "GET",
            "/__unit/chaos",
            "Active chaos rules with their counters and fire history.",
            chaos_get,
            operation_id="UnitChaos",
        ),
        c(
            "POST",
            "/__unit/chaos/rules",
            "Add one rule, or replace the whole set.",
            chaos_rules_post,
            operation_id="UnitAddChaosRule",
        ),
        c(
            "DELETE",
            "/__unit/chaos/rules/{id}",
            "Remove one rule.",
            chaos_rule_delete,
            operation_id="UnitRemoveChaosRule",
        ),
        c(
            "POST",
            "/__unit/chaos/reset",
            "Drop all rules and counters.",
            chaos_reset,
            operation_id="UnitResetChaos",
        ),
        c(
            "GET",
            "/__unit/journal",
            "Append-only log of committed state mutations.",
            journal,
            operation_id="UnitJournal",
        ),
        c("GET", "/__unit/state", "Entity counts and the state digest.", state, operation_id="UnitState"),
        c(
            "GET",
            "/__unit/state/snapshot",
            "Full state, restorable into another unit.",
            state_snapshot,
            operation_id="UnitStateSnapshot",
        ),
        c(
            "POST",
            "/__unit/state/restore",
            "Replace state with a previous snapshot.",
            state_restore,
            operation_id="UnitStateRestore",
        ),
        c(
            "POST",
            "/__unit/state/reset",
            "Wipe state and re-apply the seed scenario.",
            state_reset,
            operation_id="UnitStateReset",
        ),
        c(
            "POST",
            "/__unit/state/update",
            "Commit one mutation under optimistic concurrency.",
            state_update,
            operation_id="UnitStateUpdate",
        ),
        c(
            "POST",
            "/__unit/state/page",
            "Page a collection through the store's cursor.",
            state_page,
            operation_id="UnitStatePage",
        ),
        c(
            "GET",
            "/__unit/auth",
            "How to authenticate, and credentials that currently work.",
            auth_get,
            operation_id="UnitAuth",
        ),
        c(
            "GET",
            "/__unit/webhooks/subscriptions",
            "Subscribers the dispatcher knows about.",
            subscriptions_get,
            operation_id="UnitSubscriptions",
        ),
        c(
            "POST",
            "/__unit/webhooks/subscriptions",
            "Register a subscriber without using the vendor API.",
            subscriptions_post,
            operation_id="UnitAddSubscription",
        ),
        c(
            "DELETE",
            "/__unit/webhooks/subscriptions/{id}",
            "Remove a subscriber.",
            subscriptions_delete,
            operation_id="UnitRemoveSubscription",
        ),
        c(
            "GET",
            "/__unit/webhooks/deliveries",
            "Every delivery attempt, with headers and signature.",
            deliveries,
            operation_id="UnitDeliveries",
        ),
        c(
            "POST",
            "/__unit/webhooks/drain",
            "Wait for in-flight deliveries to settle.",
            drain,
            operation_id="UnitDrain",
            serialized=False,
        ),
        c(
            "POST",
            "/__unit/webhooks/retry-policy",
            "Adjust the retry schedule scaling at runtime.",
            retry_policy,
            operation_id="UnitSetRetryPolicy",
        ),
        c(
            "POST",
            "/__unit/webhooks/emit",
            "Enqueue a synthetic event through the real delivery path.",
            emit,
            operation_id="UnitEmitEvent",
        ),
        c(
            "POST",
            "/__unit/webhooks/sink",
            "Program the in-memory sink's next responses.",
            sink_program,
            operation_id="UnitProgramSink",
        ),
        c(
            "POST",
            "/__unit/clock/advance",
            "Virtual clock only: jump forward and fire due timers.",
            clock_advance,
            operation_id="UnitAdvanceClock",
            serialized=False,
        ),
        c(
            "GET",
            "/__unit/machines",
            "Declared state machines, with terminal states derived.",
            machines_get,
            operation_id="UnitMachines",
        ),
        c(
            "POST",
            "/__unit/machines/probe",
            "Evaluate a transition without mutating state.",
            machines_probe,
            operation_id="UnitProbeMachine",
        ),
        c(
            "POST",
            "/__unit/echo",
            "Reflect the parsed request body, whatever content type carried it.",
            echo,
            operation_id="UnitEcho",
        ),
    )


# ---------------------------------------------------------------------------
# Helpers. Module level so a test can reach them without building a unit.
# ---------------------------------------------------------------------------


def _machines(ctx: UnitContext) -> Mapping[str, MachineDef]:
    return ctx.vendor.machines


def _chaos_route_keys(binding: ControlBinding) -> tuple[str, ...]:
    """Route keys a chaos rule could actually select.

    **Internal routes are excluded.** The pipeline short-circuits them before
    fault selection ever runs, so counting them would report a rule as matching
    routes it can never fire on -- which is precisely the mistake
    ``matched_routes`` exists to surface.
    """
    return tuple(f"{row.method} {row.path}" for row in binding.list_routes() if not row.internal)


def _rules_as_json(ctx: UnitContext, route_keys: Sequence[str]) -> list[dict[str, Any]]:
    """Each rule with its counters and the routes it resolves to.

    ``matched_routes`` is the answer to "why did my rule never fire". The
    reference validates a rule's ``id``, ``fault`` and ``scope`` and never
    checks that ``match.route`` names a registered route, so a typo is a rule
    that matches nothing, forever, silently.
    """
    out: list[dict[str, Any]] = []
    for status in ctx.chaos.status():
        body = status.as_json()
        resolved = matched_routes(status.rule, route_keys)
        body["matched_routes"] = list(resolved)
        out.append(body)
    return out


def _validated_rule(document: Mapping[str, Any], ctx: UnitContext, route_keys: Sequence[str]) -> ChaosRule:
    """Parse one submitted rule and refuse the two things it can be wrong about.

    The grammar check is ``chaos/rules.py``'s. Two more happen here because
    both need something a pure parser does not have: the capability registry,
    and the route table.

    The webhook-scope capability assertion is the reference's, verbatim in
    intent -- a behaviour capability has no surface of its own, so this is
    where a consumer meets its "disabled" answer.
    """
    rule = parse_rule(document, source="POST /__unit/chaos/rules")
    if rule.scope == "webhook":
        ctx.capabilities.assert_enabled("webhooks.chaos", "POST /__unit/chaos/rules")
    resolved = matched_routes(rule, route_keys)
    if not resolved:
        _report_dead_rule(ctx, rule.id, rule.match.route if rule.match is not None else None)
    return rule


def _report_dead_rule(ctx: UnitContext, rule_id: str, pattern: str | None) -> None:
    """A rule that selects no route: a NOTE, or a 400 under ``strict_rules``.

    Both halves matter. Silence is how the reference ships a profile whose
    ``match.route`` no longer names anything and produces a transcript with a
    dead rule in it and nobody the wiser; a hard error by default would refuse
    a rule aimed at a route a capability has temporarily switched off, which is
    a legitimate thing to write.
    """
    detail = (
        f"chaos rule {rule_id!r} matches no registered route (match.route={pattern!r}); it can never fire. "
        "Route templates are braces -- 'GET /v2/orders/{order_id}' -- not colons."
    )
    if ctx.config.chaos.strict_rules:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=detail,
            field="match.route",
            info={"id": rule_id, "route": pattern},
        )
    ctx.log.warn("chaos rule matches no route", {"id": rule_id, "route": pattern})


def _since(raw: str | None) -> int:
    """``?since=`` as an integer, or an ``invalid_value`` naming it.

    The reference does ``Number(query('since') ?? 0)`` and falls back to ``0``
    for anything unparseable, so ``?since=abc`` silently returns the *whole*
    journal -- an ignored knob answering with the opposite of what was asked.
    Recorded as ``provenance: judgment`` and pinned by test.
    """
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="since must be a non-negative integer.",
            field="since",
        ) from None
    if value < 0:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="since must be a non-negative integer.",
            field="since",
        )
    return value


def _timer_as_json(timer: PendingTimer) -> dict[str, Any]:
    return {"id": timer.id, "label": timer.label, "due_in_ms": timer.due_in_ms}


def _subscription_as_json(subscription: Subscription) -> dict[str, Any]:
    """One subscriber, optional keys omitted rather than nulled.

    Projected from the typed :class:`~vendorfake.core.webhooks.models.Subscription`
    rather than from the raw entity, so the control plane and the dispatcher
    agree about what a subscription *is* by construction. The signature key is
    included: this is a fake, the key is chosen by whoever configured it, and a
    consumer verifying a signature needs to know what to verify with.
    """
    return compact(
        {
            "id": subscription.id,
            "name": subscription.name,
            "notification_url": subscription.notification_url,
            "event_types": list(subscription.event_types),
            "signature_key": subscription.signature_key,
            "enabled": subscription.enabled,
            "api_version": subscription.api_version,
        }
    )


def _signer_as_json(signer: Signer) -> dict[str, Any]:
    """The signing scheme's own description, plus what it declares it depends on.

    ``describe()`` is free prose a vendor writes for an operator. ``bindings``
    is the machine-readable half, and it is published because a conformance
    check has to assert each declared direction *in the direction declared* --
    a static scheme is conformant, not merely tolerated -- and it cannot do
    that from prose. ``signature_headers`` is in the same block for the same
    reason: without it a check comparing two deliveries can see that something
    moved but not that the *signature* moved.
    """
    properties = signer.properties
    return {
        **dict(signer.describe()),
        "bindings": {
            "url_bound": properties.url_bound,
            "body_bound": properties.body_bound,
            "secret_bound": properties.secret_bound,
            "signature_headers": [name.lower() for name in properties.signature_headers],
        },
    }


def _magic_as_json(spec: MagicTriggerSpec | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {
        "prefix": spec.prefix,
        "body_paths": list(spec.body_paths),
        "query_params": list(spec.query_params),
        "headers": list(spec.headers),
    }


def _synthetic_event_id(event_type: str, entity_id: str, seq: int) -> str:
    """A stable id for a control-plane emission.

    Derived, not drawn: taking it from the unit's RNG would consume a draw and
    move every subsequent seeded id, so an emitted probe event would change the
    ids of the entities a check is asserting on. The shape matches the
    dispatcher's own minted ids so nothing downstream can tell them apart.
    """
    digest = sha256_hex(f"control|{event_type}|{entity_id}|{seq}")
    return "-".join((digest[0:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]))
