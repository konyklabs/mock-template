"""The ``/__unit/*`` control plane: the same thirty-four routes for every vendor, reachable over
the same channel as the vendor's own API, with no second port or client library. Anything a
conformance check needs to observe is observable here: a check drives a unit through a URL and
asserts on what comes back.

Namespaced under ``/__unit/`` since no real vendor serves a double-underscore path segment, and
``kernel/router.py`` refuses any vendor route that tries. Every route is ``internal=True``, so the
kernel skips auth, chaos and idempotency for it -- a control call must never trip the fault it is
configuring.

``webhooks/drain`` and ``clock/advance`` declare ``serialized=False``: both block on machinery
another request must feed. ``clock_advance`` also passes ``ctx.webhooks.settle`` to
``Clock.advance``, since a worker-thread delivery could otherwise under-report a retry cascade;
``{"drain": false}`` is the only way to observe that a retry did not happen before its interval.

Thirteen routes have no counterpart in a real vendor API -- ``errors``, the two ``machines``
routes, ``echo``, the two ``webhooks`` emit/sink routes, ``auth``, ``manifest``, the two ``state``
write routes, and the request log -- each documented at its own handler below.

Every response key is snake_case, including five this build could have spelled in camelCase.
JUDGMENT.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
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
    UnitRequest,
)
from vendorfake.core.kernel.unit import ControlBinding
from vendorfake.core.state.machine import MachineDef, StateMachine
from vendorfake.core.time.clock import PendingTimer
from vendorfake.core.util.json import compact, sha256_hex
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION, Subscription
from vendorfake.core.webhooks.sink import MemorySink

__all__ = ["CONTROL_PREFIX", "DEFAULT_REQUEST_LIMIT", "MANIFEST_SCHEMA", "control_plane_routes", "manifest_document"]

CONTROL_PREFIX = "/__unit/"
"""Every path below begins with this. Restated from ``kernel/router.py`` for a
reader; the router owns the enforcement."""

_MEMORY_SINK_KIND = "memory"
"""The one sink ``POST /__unit/webhooks/sink`` can program. Compared against
:attr:`MemorySink.kind` by a test, so a rename cannot silently orphan it."""


def control_plane_routes(binding: ControlBinding) -> tuple[Route, ...]:
    """Build the control plane against one unit's :class:`ControlBinding`.

    A factory rather than a module-level table because two of the routes need
    unit internals that a route handler must not have -- re-seeding the store
    and enumerating the router -- and the binding is the enumerable list of
    exactly those.
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
                "version": _distribution_version(),
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
                            # `getattr`: a vendor built before `VendorDefinition.roles`
                            # existed must not turn every GET /__unit/info into an
                            # AttributeError; `{}` lets conformance C34 report the real gap.
                            "roles": dict(getattr(ctx.vendor, "roles", {})),
                            # C35 is a promise about which profile *names* a vendor ships, so
                            # this needs the roster, glob'd rather than imported from
                            # vendorfake.registry (tools/boundary.toml forbids that import).
                            "profiles": sorted(path.stem for path in ctx.vendor.profile_dir.glob("*.json")),
                        }
                    ),
                    "profile": ctx.config.profile,
                    "requested_capabilities": (
                        None if ctx.config.requested_capabilities is None else list(ctx.config.requested_capabilities)
                    ),
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
                    # A fingerprint of any seed overlay, never its contents (which may
                    # carry the consumer's own credentials); a report pins this digest.
                    "seed_overlay": {
                        "active": ctx.config.seed_overlay_digest is not None,
                        "digest": ctx.config.seed_overlay_digest,
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

        C05's data source. Kinds are enumerated from the enum, not the
        vendor's table, so a vendor that forgot one answers with its
        unknown-kind shape -- or fails loudly here -- instead of just missing
        from the report. Each row's ``body``/``headers`` split reflects
        whichever ``errors.sidecar`` this unit started with (konyklabs/roadmap#71).
        """
        ctx = args.ctx
        # Provenance comes from `describe()`, not the shaped body, since the
        # sidecar that would carry it there is switchable.
        described = ctx.vendor.errors.describe()
        shaped: list[dict[str, Any]] = []
        for kind in UnitErrorKind:
            # `describing=True`: a read must not draw a request id or read the
            # clock, which would renumber the caller's own scenario.
            result = ctx.vendor.errors.shape(
                UnitError(kind, detail=f"conformance probe for {kind.value}"),
                ctx,
                describing=True,
            )
            provenance = described.get(kind.value, {}).get("provenance")
            if provenance is None:
                # Unreachable after the unit's startup check of describe().
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
        no_route = ctx.vendor.errors.not_found(args.req, ctx, describing=True)
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
        # Order is contract; see CapabilitiesBody.
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
            # Two instructions in one body is a caller who does not know which
            # one will win, and finding out from a transcript is worse than a 400.
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
        """How to authenticate here, and credentials that would work right now:
        a credential has to cross the wire for authentication to be drivable
        by a consumer in another language. Safe to publish since every
        credential here is scenario data with no real-world counterpart.
        """
        ctx = args.ctx
        offered = list(ctx.vendor.auth.credentials(ctx))
        return json_(
            {
                "describe": dict(ctx.vendor.auth.describe()),
                "modes": sorted({credential.mode for credential in offered}),
                "count": len(offered),
                "credentials": _credentials_as_json(ctx),
            }
        )

    def manifest(args: HandlerArgs) -> ReplyInit:
        """Everything an end-to-end script needs to address this unit, in one
        document: the credentials ``auth`` publishes, the webhook signing keys
        the seed carries, and every entity id, by collection.

        The point is a script that runs unchanged against a deployed sandbox.
        Such a script reads a manifest and the vendor's own API and never the
        control plane -- so in the deployed world a setup script writes this
        same shape from the sandbox account, and nothing else has to change.
        """
        return json_(manifest_document(args.ctx, base_url=_request_base_url(args.req)))

    def state_update(args: HandlerArgs) -> ReplyInit:
        """One committed mutation of one entity, under optimistic concurrency:
        the store's write path, reached directly. ``version`` passes through as
        ``expect_version``, so a stale value raises ``version_conflict`` and
        writes nothing. A real, journalled and delivered write, not a simulation.
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
        Ids only, deliberately: the contract observed is the cursor's, not a
        vendor's field names.
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
        # Only the keys given a value: an entity carrying `"name": null` would
        # make "absent" and "explicitly null" indistinguishable downstream.
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
            # `conflict`, since the twenty error kinds are fixed by a
            # conformance check and this already means "not possible now".
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
        # Offset from where the sink already is, so a programme does not replay
        # from its start over calls already made.
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
                # `terminal` is derived once, on the machine, not restated here.
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
            # `assert_transition` only validates the target: with no `to`, an
            # undeclared `from` would sail through `assert_mutable` unnoticed.
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
        # ONE question per call: mutability would always answer first if both
        # ran, hiding a machine that treats `from == to` as always legal.
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

    # -- the request log ---------------------------------------------------

    def requests_get(args: HandlerArgs) -> ReplyInit:
        """Every request the unit handled, newest first, filtered: the
        journal's counterpart, answering "what was called" including calls
        that changed nothing or matched no route. Control-plane requests are
        absent by construction, so polling this route does not show up in it.
        """
        capacity = binding.requests.capacity
        limit = _limit(args.query("limit"), maximum=capacity)
        records = binding.requests.records(
            operation_id=args.query("operation_id"),
            route=args.query("route"),
            unmatched=_flag(args.query("unmatched"), field="unmatched"),
            limit=limit,
        )
        return json_(
            {
                "count": len(records),
                "recorded": len(binding.requests),
                "capacity": capacity,
                "limit": limit,
                "requests": [record.as_json() for record in records],
            }
        )

    def requests_delete(args: HandlerArgs) -> ReplyInit:
        """Drop every record. State is untouched -- this forgets, it does not reset."""
        return json_({"cleared": binding.requests.clear()})

    def requests_near_misses(args: HandlerArgs) -> ReplyInit:
        """The unmatched requests, each with the routes it nearly asked for: a
        projection of the route above, narrowed to ``matched=false``, so a
        consumer need not know the filter spelling to ask for it.
        """
        limit = _limit(args.query("limit"), maximum=binding.requests.capacity)
        records = binding.requests.records(unmatched=True, limit=limit)
        return json_(
            {
                "count": len(records),
                "near_misses": [
                    {
                        "request": {
                            "id": record.id,
                            "method": record.method,
                            "path": record.path,
                            "received_at": record.received_at,
                            "status": record.status,
                        },
                        "near_misses": [miss.as_json() for miss in record.near_misses],
                    }
                    for record in records
                ],
            }
        )

    # -- transport ---------------------------------------------------------

    def echo(args: HandlerArgs) -> ReplyInit:
        """Reflect what the body reader made of this request, and both query
        views: no capability, any content type, no vendor knowledge, so a
        form-encoded body reaching the handler as fields is assertable on
        every profile, not just one whose vendor happens to have a form route.
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
            # Present only when there was a JSON document: `null` is a
            # legitimate body, so an always-present key could not tell the two apart.
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
            "/__unit/manifest",
            "Credentials, webhook keys and entity ids: what an end-to-end script needs to address this unit.",
            manifest,
            operation_id="UnitManifest",
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
        c(
            "GET",
            "/__unit/requests",
            "Every request the unit handled, newest first.",
            requests_get,
            operation_id="UnitRequests",
        ),
        c(
            "DELETE",
            "/__unit/requests",
            "Forget every recorded request. State is untouched.",
            requests_delete,
            operation_id="UnitClearRequests",
        ),
        c(
            "GET",
            "/__unit/requests/unmatched/near-misses",
            "Unmatched requests, each with the routes it nearly asked for.",
            requests_near_misses,
            operation_id="UnitNearMisses",
        ),
    )


MANIFEST_SCHEMA = "vendorfake.manifest/1"
"""The ``schema`` field of the manifest document. Versioned rather than dated: a
consumer's end-to-end script branches on it, and the deployed-world setup script
that writes the same shape by hand has to declare which shape it wrote."""

_WEBHOOK_COLLECTION_MARKERS = ("subscri", "webhook")
"""A collection whose name contains either holds subscribers, whatever the vendor
calls them -- ``subscriptions``, ``webhooks``, ``webhook_subscriptions``. Matched
rather than listed because a vendor names its own collections."""

_SECRET_FIELDS = ("signature_key", "secret", "webhook_secret")
"""The three spellings a seeded subscriber's signing key travels under here."""


def manifest_document(ctx: UnitContext, *, base_url: str | None) -> dict[str, Any]:
    """The world-neutral manifest: what a script needs to drive this unit
    *through the vendor's own API*, with nothing in it that only a fake could
    answer.

    Module level, and taking a :class:`UnitContext` rather than a
    ``ControlBinding``, so ``vendorfake manifest`` produces the same bytes
    without a server -- the two cannot drift, because there is one function.
    """
    collections = snapshot_as_json(ctx.store.snapshot())["collections"]
    return {
        "schema": MANIFEST_SCHEMA,
        "vendorfake": _distribution_version(),
        "vendor": ctx.vendor.name,
        "profile": ctx.config.profile,
        "base_url": base_url,
        "credentials": _credentials_as_json(ctx),
        "webhooks": {"signature_keys": _signature_keys(collections)},
        "ids": {name: list(entities) for name, entities in collections.items()},
    }


def _credentials_as_json(ctx: UnitContext) -> list[dict[str, Any]]:
    """The ``credentials`` array of ``GET /__unit/auth``, so the manifest cannot
    publish a credential the auth route would not."""
    return [credential.as_json() for credential in ctx.vendor.auth.credentials(ctx)]


def _signature_keys(collections: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Every distinct webhook signing key the seed carries, in seed order.

    Deduplicated because a vendor may seed one key across several subscribers,
    and a script verifying a delivery wants the set of keys that could have
    signed it, not one row per subscriber.
    """
    found: list[str] = []
    for name, entities in collections.items():
        lowered = name.lower()
        if not any(marker in lowered for marker in _WEBHOOK_COLLECTION_MARKERS):
            continue
        for entity in entities.values():
            for field in _SECRET_FIELDS:
                value = entity.get(field)
                if isinstance(value, str) and value and value not in found:
                    found.append(value)
    return found


def _distribution_version() -> str:
    """The installed distribution's version, or ``"unknown"``.

    ``importlib.metadata`` rather than ``vendorfake.__version__``: the manifest
    describes the *installation* a consumer would pin, and ``core`` may not
    import the package root anyway (``tools/boundary.toml``).
    """
    try:
        return distribution_version("vendorfake")
    except PackageNotFoundError:  # pragma: no cover - only in a tree with no metadata
        return "unknown"


def _request_base_url(req: UnitRequest) -> str | None:
    """``scheme://host`` from the request that asked, or ``None``.

    A unit does not know its own address -- it may be behind a container port
    mapping or a compose network alias -- so the only honest answer is the one
    the caller reached it at. ``x-forwarded-proto`` wins where a proxy set it,
    since the caller's scheme is the one a webhook URL has to carry.
    """
    host = req.headers.get("host")
    if not host:
        return None
    forwarded = req.headers.get("x-forwarded-proto", "")
    scheme = forwarded.split(",")[0].strip().lower() or "http"
    return f"{scheme}://{host}"


# Helpers. Module level so a test can reach them without building a unit.


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
    """Each rule with its counters and the routes it resolves to:
    ``matched_routes`` is the answer to "why did my rule never fire", since a
    rule grammar check alone never catches a ``match.route`` typo.
    """
    out: list[dict[str, Any]] = []
    for status in ctx.chaos.status():
        body = status.as_json()
        resolved = matched_routes(status.rule, route_keys)
        body["matched_routes"] = list(resolved)
        out.append(body)
    return out


def _validated_rule(document: Mapping[str, Any], ctx: UnitContext, route_keys: Sequence[str]) -> ChaosRule:
    """Parse one submitted rule and refuse the two things it can be wrong about
    that a pure parser cannot see: the capability registry and the route
    table. A webhook-scope rule asserts ``webhooks.chaos`` here, since a
    behaviour capability has no surface of its own to answer "disabled" from.
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
    Silence alone would let a stale ``match.route`` sit in a profile
    unnoticed; a hard error by default would refuse a rule aimed at a route a
    capability has only temporarily switched off, which is legitimate to write.
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
    """``?since=`` as an integer, or an ``invalid_value`` naming it: a silent
    fallback to ``0`` on junk would return the *whole* journal, the opposite
    of what an ignored knob should do. JUDGMENT, pinned by test.
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


DEFAULT_REQUEST_LIMIT = 100
"""How many request records ``GET /__unit/requests`` returns when not asked; the
capacity is a query parameter away."""


def _limit(raw: str | None, *, maximum: int) -> int:
    """``?limit=`` as an integer between 1 and the log's capacity. Clamped, not
    refused, at the top end -- a caller asking for more than the log holds
    wants "all of them" -- but a non-number, zero or negative limit is refused.
    """
    if raw is None or raw == "":
        return min(DEFAULT_REQUEST_LIMIT, maximum) if maximum else DEFAULT_REQUEST_LIMIT
    try:
        value = int(raw)
    except ValueError:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="limit must be a positive integer.",
            field="limit",
        ) from None
    if value < 1:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="limit must be a positive integer.",
            field="limit",
        )
    return min(value, maximum) if maximum else value


def _flag(raw: str | None, *, field: str) -> bool | None:
    """A tri-state query flag: ``None`` when absent, else a strict boolean, so
    ``?unmatched=false`` (not "no filter") and a bare ``?unmatched`` (true) are
    both accepted; anything else is refused rather than guessed, since a loose
    truthiness reading would make ``?unmatched=false`` select the wrong half.
    """
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in ("", "true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field} must be 'true' or 'false' (a bare '?{field}' means true).",
        field=field,
    )


def _timer_as_json(timer: PendingTimer) -> dict[str, Any]:
    return {"id": timer.id, "label": timer.label, "due_in_ms": timer.due_in_ms}


def _subscription_as_json(subscription: Subscription) -> dict[str, Any]:
    """One subscriber, optional keys omitted rather than nulled; projected from
    the typed :class:`~vendorfake.core.webhooks.models.Subscription`, so the
    control plane and the dispatcher agree on its shape by construction.
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
    """The signing scheme's own description, plus the machine-readable
    ``bindings`` a conformance check needs to assert each declared direction,
    since ``describe()`` alone is free prose it cannot assert against.
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
    """A stable id for a control-plane emission, derived rather than drawn from
    the unit's RNG, which would otherwise renumber every subsequent seeded id.
    """
    digest = sha256_hex(f"control|{event_type}|{entity_id}|{seq}")
    return "-".join((digest[0:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]))
