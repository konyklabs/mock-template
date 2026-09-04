"""The unit: composition root, and the nine-step request pipeline.

INVARIANT: **the step order is the specification**, each boundary observable and pinned by a test that would pass
under a plausible wrong order.

1. **internal short-circuit** -- a control-plane route runs with no capability
   gate, fault selection, auth or idempotency, or a disabled capability could
   not be re-enabled through the unit's own control plane.
2. **capability gate**, *before* auth: a disabled capability is a property of
   the deployment, so it answers the same with or without a token.
3. **fault selection**, once per request, through ``chaos/selector.py``, which
   applies the ``chaos`` gate first. One selection, two application phases.
4. **pre-auth faults** -- all but ``token_expiry``, so a consumer can test
   backoff without a valid token.
5. **auth and scopes** -- the vendor resolves the credential, the *kernel*
   checks ``Route.scopes``.
6. **post-auth fault** -- ``token_expiry`` only, whether or not the route
   declared ``auth``: ``chaos/faults.py`` owns which phase a fault fires in.
7. **idempotency** -- lookup and replay, after auth so a stored response never
   reaches a caller who guessed a key, and after the fault phases so an
   injected 500 consumes none. A hit *binds* the replay, so step 9 still runs.
8. **handler and idempotency store**, on a miss only: the handler runs and its
   clean 2xx is stored against the key.
9. **response-phase fault**, on whichever answer step 8 left, so a decision
   drawn at step 3 is paid out on every answer the *vendor* produced, errors
   included. A framework crash is not a vendor answer and is never faulted.

The router match happens before step 1 and produces both the 404 and the 405. ``_finish`` stamps
``x-unit-request-id`` and ``decorate`` gives the vendor its last chance, on the success path and every error path
but never on the 404.

INVARIANT: **delays leave as data** -- a fault sets ``UnitError.delay_ms`` and nothing below the seam sleeps,
because only the binding knows the caller's timeout. INVARIANT: **the request lock** -- ``handle`` takes one
re-entrant lock for the whole pipeline unless the route declares ``serialized=False``, which is what makes id
minting and journal ordering deterministic; taken after the router match, because the match decides whether to take
it.
"""

from __future__ import annotations

import collections
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

from vendorfake.core.capability.gates import CoreCapability, assert_capability_declarations
from vendorfake.core.capability.registry import CapabilityRegistry
from vendorfake.core.chaos.engine import ChaosDecision, ChaosEngine, ChaosSubject
from vendorfake.core.chaos.faults import (
    INTACT_RESPONSE_FAULTS,
    RESPONSE_PHASE_FAULTS,
    apply_request_fault,
    apply_response_fault,
)
from vendorfake.core.chaos.rules import matched_routes
from vendorfake.core.chaos.selector import FaultSelector
from vendorfake.core.config.models import ResolvedConfig
from vendorfake.core.kernel.magic import MagicExtraction, extract_magic
from vendorfake.core.kernel.nearmiss import NEAR_MISS_HEADER, near_miss_header, near_misses
from vendorfake.core.kernel.reply import normalize
from vendorfake.core.kernel.router import Match, MethodNotAllowed, Router, is_control_path
from vendorfake.core.kernel.shaping import assert_error_table_total, header_text
from vendorfake.core.kernel.types import (
    AuthResult,
    HandlerArgs,
    Logger,
    NearMiss,
    ReplyInit,
    RequestRecord,
    Route,
    ShapedError,
    UnitContext,
    UnitError,
    UnitErrorKind,
    UnitRequest,
    UnitResponse,
    VendorDefinition,
)
from vendorfake.core.logging import JsonLogger
from vendorfake.core.rand.rng import Rng
from vendorfake.core.state.store import IdempotencyRecord, Store
from vendorfake.core.time.clock import Clock
from vendorfake.core.util.b64 import b64url_decode, b64url_encode
from vendorfake.core.util.json import digest_of, dump_json
from vendorfake.core.util.paths import dot_get
from vendorfake.core.webhooks.dispatcher import WebhookDispatcher
from vendorfake.core.webhooks.sink import DeliverySink, HttpSink

__all__ = [
    "DELAY_ASKED_HEADER",
    "REQUEST_ID_HEADER",
    "ControlBinding",
    "RequestLog",
    "RouteInfo",
    "Unit",
    "make_request",
]

#: Echoed on every response and honoured on the way *in*: a binding minting a
#: fresh id for a caller-supplied one would break cross-transport correlation.
REQUEST_ID_HEADER = "x-unit-request-id"

#: On a ``timeout``-faulted answer: the ``delay_ms`` the rule asked for,
#: whichever clock the unit runs on. See ``Unit._shape``.
DELAY_ASKED_HEADER = "vendorfake-delay-ms"


def _now_iso() -> str:
    """Wall clock, RFC 3339 with milliseconds. Deliberately not the unit's :class:`Clock`: ``received_at`` is when a
    binding took delivery, a fact about the world rather than about the scenario."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_request(
    *,
    method: str,
    path: str,
    query: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    headers: Mapping[str, str] | None = None,
    body: object = None,
    raw_body: bytes | str | None = None,
    transport: str = "inprocess",
    request_id: str | None = None,
    received_at: str | None = None,
) -> UnitRequest:
    """Build a :class:`UnitRequest` the way every binding must build one: header names lower-cased; a ``?query=string``
    on ``path`` split off and parsed with blank values kept, ``query_all`` keeping every value and ``query`` the
    last; ``raw_body`` winning over ``body``, which is otherwise serialised with a JSON content type; and the id
    taken from an inbound ``x-unit-request-id``."""
    normalised: dict[str, str] = {}
    for name, value in (headers or {}).items():
        normalised[name.lower()] = value

    path, _, query_string = path.partition("?")
    pairs = list(parse_qsl(query_string, keep_blank_values=True))
    pairs.extend(query.items() if isinstance(query, Mapping) else (query or ()))
    query_all: dict[str, list[str]] = {}
    for name, value in pairs:
        query_all.setdefault(name, []).append(value)

    payload: bytes
    if raw_body is not None:
        payload = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
    elif body is not None:
        payload = body.encode("utf-8") if isinstance(body, str) else dump_json(body)
        normalised.setdefault("content-type", "application/json")
    else:
        payload = b""

    return UnitRequest(
        id=request_id or normalised.get(REQUEST_ID_HEADER) or str(uuid.uuid4()),
        method=method.upper(),
        path=path if path.startswith("/") else f"/{path}",
        query={name: values[-1] for name, values in query_all.items()},
        headers=normalised,
        raw_body=payload,
        transport=transport,
        received_at=received_at or _now_iso(),
        query_all={name: tuple(values) for name, values in query_all.items()},
    )


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """One row of the route table, as the control plane publishes it."""

    method: str
    path: str
    capability: str
    auth: str | None = None
    #: Scopes the kernel requires of the resolved credential, published so a
    #: caller can tell an insufficient credential from a missing one.
    scopes: tuple[str, ...] = ()
    #: How this route deduplicates a retried request, or ``None``.
    idempotency: dict[str, object] | None = None
    #: How this route pages, or ``None``. See :class:`PaginationSpec`.
    pagination: dict[str, object] | None = None
    #: A body this route accepts. See :attr:`Route.example_body`.
    example_body: Mapping[str, Any] | None = None
    #: Path parameters naming seeded entities the example applies to.
    example_params: Mapping[str, str] | None = None
    operation_id: str | None = None
    summary: str | None = None
    internal: bool = False
    serialized: bool = True

    @classmethod
    def of(cls, route: Route) -> RouteInfo:
        idempotency = (
            None
            if route.idempotency is None
            else {
                "key_path": route.idempotency.key_path,
                "scope": route.idempotency.scope,
                "required": route.idempotency.required,
                "on_mismatch": route.idempotency.on_mismatch,
            }
        )
        return cls(
            method=route.method.upper(),
            path=route.path,
            capability=route.capability,
            auth=route.auth,
            scopes=tuple(route.scopes),
            idempotency=idempotency,
            pagination=None if route.pagination is None else route.pagination.as_json(),
            example_body=route.example_body,
            example_params=None if route.example_params is None else dict(route.example_params),
            operation_id=route.operation_id,
            summary=route.summary,
            internal=route.internal,
            serialized=route.serialized,
        )

    def as_json(self) -> dict[str, object]:
        """snake_case, and optional keys omitted rather than sent as null."""
        body: dict[str, object] = {
            "method": self.method,
            "path": self.path,
            "capability": self.capability,
            "internal": self.internal,
            "serialized": self.serialized,
        }
        if self.scopes:
            body["scopes"] = list(self.scopes)
        for key, value in (
            ("auth", self.auth),
            ("idempotency", self.idempotency),
            ("pagination", self.pagination),
            ("example_body", None if self.example_body is None else dict(self.example_body)),
            ("example_params", None if self.example_params is None else dict(self.example_params)),
            ("operation_id", self.operation_id),
            ("summary", self.summary),
        ):
            if value is not None:
                body[key] = value
        return body


class RequestLog:
    """A bounded ring of :class:`RequestRecord`, newest last, answering what the journal cannot: a 4xx, a read and an
    unmatched request leave no trace there. Oldest evicted first. Control-plane requests are not recorded, which is
    the caller's rule to apply. Its own lock, because a ``serialized=False`` route runs outside the kernel's."""

    __slots__ = ("_capacity", "_lock", "_records")

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError(f"request log capacity must be zero or more, got {capacity}")
        self._capacity = capacity
        # ``maxlen`` is the eviction: one place for the bound.
        self._records: collections.deque[RequestRecord] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def record(self, entry: RequestRecord) -> None:
        """Append, evicting the oldest when full. A capacity of zero records
        nothing at all, which is how the log is switched off."""
        if self._capacity == 0:
            return
        with self._lock:
            self._records.append(entry)

    def clear(self) -> int:
        """Drop every record, returning how many there were."""
        with self._lock:
            count = len(self._records)
            self._records.clear()
            return count

    def records(
        self,
        *,
        operation_id: str | None = None,
        route: str | None = None,
        unmatched: bool | None = None,
        limit: int | None = None,
    ) -> tuple[RequestRecord, ...]:
        """Matching records, **newest first**. ``limit`` is applied after filtering, so ``requests(operation_id=X,
        limit=1)`` is the most recent call to X rather than the most recent call if it happened to be X. Every
        filter is a conjunction and ``None`` means "do not filter", so ``unmatched=False`` selects only the matched
        ones."""
        with self._lock:
            found = list(self._records)
        found.reverse()
        selected = [
            entry
            for entry in found
            if (operation_id is None or entry.operation_id == operation_id)
            and (route is None or entry.route == route)
            and (unmatched is None or entry.matched is not unmatched)
        ]
        return tuple(selected if limit is None else selected[:limit])


@dataclass(frozen=True, slots=True)
class ControlBinding:
    """Unit internals the control plane needs and a route handler must not have. Two callables and one object rather
    than a reference to the unit, so the surface is the complete enumerable list."""

    #: Wipe state and re-apply the seed document.
    hydrate: Callable[[], None]
    #: Every registered route, control routes included.
    list_routes: Callable[[], tuple[RouteInfo, ...]]
    #: The request log, read and cleared by ``/__unit/requests``. NOT on :class:`UnitContext`: a handler that could
    #: read it could branch on what the caller did earlier. Required rather than defaulted, so a mis-wired plane
    #: cannot answer ``{"count": 0}`` forever.
    requests: RequestLog


@dataclass(slots=True)
class _Trace:
    """What one request picked up on its way through, for the request log. Mutable and passed down, because what it
    carries is learned on paths that then raise, where a return value would be lost -- the armed
    :class:`ChaosDecision` included."""

    fault: str | None = None
    rule_id: str | None = None
    #: ``store.journal_seq`` immediately before and after the handler ran. On a ``serialized`` route both reads
    #: happen under the pipeline lock; a ``serialized=False`` route gets a best-effort window, and a request that
    #: never reached the handler leaves both at zero.
    journal_seq_before: int = 0
    journal_seq_after: int = 0
    #: The decision drawn at step 3, whole; ``fault``/``rule_id`` above are the two fields the request log wants.
    decision: ChaosDecision | None = None
    #: Whether the payout has already been *tried*. Set before the attempt, not after, because the attempt itself
    #: can raise, and the error path would otherwise call it again from inside the ``except`` clause.
    response_fault_attempted: bool = False
    near_misses: tuple[NearMiss, ...] = ()


class _Context:
    """The concrete :class:`UnitContext`. Attributes, checked against the
    protocol by the annotated assignment in :meth:`Unit.__init__`."""

    __slots__ = ("capabilities", "chaos", "clock", "config", "log", "rng", "store", "vendor", "webhooks")

    def __init__(
        self,
        *,
        vendor: VendorDefinition,
        config: ResolvedConfig,
        store: Store,
        capabilities: CapabilityRegistry,
        chaos: ChaosEngine,
        clock: Clock,
        rng: Rng,
        webhooks: WebhookDispatcher,
        log: Logger,
    ) -> None:
        self.vendor = vendor
        self.config = config
        self.store = store
        self.capabilities = capabilities
        self.chaos = chaos
        self.clock = clock
        self.rng = rng
        self.webhooks = webhooks
        self.log = log


def _assert_error_shaper_describes(vendor: VendorDefinition) -> None:
    """Make a shaper with no ``describe()``, or a short table, a startup failure naming the vendor and the kinds,
    rather than a leaked ``'NoneType' object is not callable`` or a ``provenance: null`` at 200."""
    describe = getattr(vendor.errors, "describe", None)
    if not callable(describe):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"vendor {vendor.name!r}: its ErrorShaper has no describe(); the control plane publishes "
            "every error row's provenance from it.",
            field="vendor.errors.describe",
        )
    rows = describe()
    try:
        assert_error_table_total(rows, name=f"vendor {vendor.name!r} ErrorShaper.describe()")
    except RuntimeError as exc:
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=str(exc), field="vendor.errors.describe") from exc
    bad = sorted(kind for kind, row in rows.items() if row.get("provenance") not in ("documented", "judgment"))
    if bad:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"vendor {vendor.name!r} ErrorShaper.describe(): every row needs provenance 'documented' or "
            f"'judgment'; missing or invalid on {bad}",
            field="vendor.errors.describe",
            info={"kinds": bad},
        )


class Unit:
    """One running fake: a vendor surface, a profile, and the pipeline. A vendor supplies a :class:`VendorDefinition`
    and nothing else; everything below the constructor is the same for every vendor."""

    __slots__ = (
        "_capabilities",
        "_chaos",
        "_clock",
        "_config",
        "_control",
        "_ctx",
        "_lock",
        "_log",
        "_requests",
        "_rng",
        "_router",
        "_routes",
        "_seed",
        "_selector",
        "_sink",
        "_store",
        "_vendor",
        "_webhooks",
    )

    def __init__(
        self,
        *,
        vendor: VendorDefinition,
        config: ResolvedConfig,
        seed: object = None,
        sink: DeliverySink | None = None,
        logger: Logger | None = None,
        control_routes: Callable[[ControlBinding], Sequence[Route]] | None = None,
    ) -> None:
        self._vendor = vendor
        self._config = config
        self._seed = seed
        self._log: Logger = JsonLogger(config.log_level) if logger is None else logger

        # A vendor gating on a core capability it never declared would have
        # that behaviour silently off: a startup failure, naming every problem.
        assert_capability_declarations(vendor.capabilities, vendor.not_supported)
        _assert_error_shaper_describes(vendor)

        self._clock = Clock(config.clock.mode, config.clock.start)
        # One stream for the unit, seeded from the profile. A vendor salts its
        # own id stream off it, so a new probability rule renumbers nothing.
        self._rng = Rng(config.chaos.seed)
        self._store = Store(self._clock)
        self._chaos = ChaosEngine(self._rng, self._clock.iso_ms, config.chaos.rules)

        self._requests = RequestLog(config.requests.capacity)

        self._control = ControlBinding(
            hydrate=self._hydrate,
            list_routes=self._list_routes,
            requests=self._requests,
        )
        control = tuple(control_routes(self._control)) if control_routes is not None else ()
        self._routes: tuple[Route, ...] = tuple(vendor.routes) + control
        # ``Router.add`` refuses a vendor route claiming the control-plane
        # namespace, so every construction path passes that gate.
        self._router = Router(self._routes)
        self._capabilities = CapabilityRegistry(vendor.capabilities, self._routes, config.capabilities, config.profile)
        self._selector = self._make_selector()

        self._assert_retry_schedule()
        self._report_dead_chaos_rules()

        # Built here but connecting nothing: the client is created on first
        # send, so a vendor with no webhooks opens no connection pool.
        self._sink: DeliverySink = HttpSink() if sink is None else sink
        self._webhooks = self._make_dispatcher(
            store=self._store,
            clock=self._clock,
            # The one choke point, applying the ``webhooks.chaos`` gate.
            selector=self._selector,
            sink=self._sink,
            retry=config.webhooks.retry,
            # Deferred: the context needs the dispatcher and vice versa.
            get_context=lambda: self._ctx,
            disabled=config.webhooks.disable_delivery,
        )

        self._ctx: UnitContext = _Context(
            vendor=vendor,
            config=config,
            store=self._store,
            capabilities=self._capabilities,
            chaos=self._chaos,
            clock=self._clock,
            rng=self._rng,
            webhooks=self._webhooks,
            log=self._log,
        )
        self._store.mark_volatile(*vendor.volatile_fields)
        self._store.mark_opaque(*vendor.opaque_fields)
        # After the context exists: the listener reaches through it on its
        # first journal entry, and holds the ``webhooks`` gate itself.
        self._webhooks.attach()
        self._lock = threading.RLock()

    # -- construction-time assertions ---------------------------------------

    def _assert_retry_schedule(self) -> None:
        """A declared ``webhooks`` capability needs a non-empty retry schedule. The core ships none, so the vendor's
        ``retry_defaults`` is merged under the profile; without it every delivery would exhaust on its first attempt
        and look like an unreachable subscriber."""
        declared = {decl.name for decl in self._vendor.capabilities}
        if CoreCapability.WEBHOOKS.value not in declared:
            return
        if self._config.webhooks.retry.schedule_ms:
            return
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"Vendor {self._vendor.name!r} declares the "
                f"{CoreCapability.WEBHOOKS.value!r} capability but the resolved retry schedule is empty. "
                "Supply it in the vendor's retry_defaults or in the profile's webhooks.retry.schedule_ms."
            ),
            field="webhooks.retry.schedule_ms",
            info={"profile": self._config.profile, "vendor": self._vendor.name},
        )

    def _report_dead_chaos_rules(self) -> None:
        """A profile rule whose ``match.route`` names no route can never fire, and a typo would otherwise be silent
        forever. Internal routes are excluded, the pipeline short-circuiting them before fault selection. A warning
        by default and a hard ``invalid_value`` under ``config.chaos.strict_rules``."""
        rules = self._chaos.list()
        if not rules:
            return
        route_keys = tuple(route.key for route in self._routes if not route.internal)
        dead = [rule for rule in rules if not matched_routes(rule, route_keys)]
        if not dead:
            return
        offenders = {rule.id: (rule.match.route if rule.match is not None else None) for rule in dead}
        detail = (
            f"chaos rule(s) {', '.join(sorted(offenders))} match no registered route and can never fire. "
            "Route templates are braces -- 'GET /v2/orders/{order_id}' -- not colons."
        )
        if self._config.chaos.strict_rules:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=detail,
                field="chaos.rules",
                info={"rules": offenders, "profile": self._config.profile},
            )
        self._log.warn("chaos rules match no route", {"rules": offenders, "profile": self._config.profile})

    # -- identity -----------------------------------------------------------

    @property
    def name(self) -> str:
        return self._vendor.name

    @property
    def context(self) -> UnitContext:
        return self._ctx

    @property
    def routes(self) -> tuple[Route, ...]:
        return self._routes

    @property
    def control(self) -> ControlBinding:
        """The typed binding the control plane is built against."""
        return self._control

    @property
    def requests(self) -> RequestLog:
        """Every request this unit has handled, control-plane calls excepted."""
        return self._requests

    # -- lifecycle ----------------------------------------------------------

    # Overridable factories for the two collaborators conformance mutants
    # replace, without a constructor seam production callers could misuse.
    def _make_selector(self) -> FaultSelector:
        return FaultSelector(self._chaos, self._capabilities)

    def _make_dispatcher(self, **kwargs: Any) -> WebhookDispatcher:
        return WebhookDispatcher(**kwargs)

    def start(self) -> None:
        """Hydrate the store from the seed document and report what was built."""
        self._hydrate()
        self._log.info(
            "unit started",
            {
                "vendor": self._vendor.name,
                "profile": self._config.profile,
                "capabilities": list(self._capabilities.enabled_names()),
                "entities": self._store.stats(),
                "state_digest": self._store.entity_digest()[:16],
                "chaos_seed": self._config.chaos.seed,
                "clock": self._config.clock.mode,
            },
        )

    @property
    def webhooks(self) -> WebhookDispatcher:
        """The dispatcher, for a control plane built against this unit."""
        return self._webhooks

    def stop(self) -> None:
        """Settle delivery, then release every timer. Drain first: clearing the timers first would discard the retries
        the drain is meant to settle. The worker stops after the drain, so no thread outlives the unit."""
        self._webhooks.drain()
        self._webhooks.stop()
        self._clock.clear_all()

    def _hydrate(self) -> None:
        """Reset, re-seed, then re-declare the profile's subscribers, in that order: ``reset`` empties the
        subscriptions too, and ``clear_log`` is last so the transcript starts at this scenario. The request log is
        cleared with them, or an assertion on call counts would depend on test order."""
        self._store.reset()
        self._vendor.hydrate(self._ctx, self._seed)
        self._webhooks.load_config_subscribers(self._config.webhooks.subscribers)
        self._webhooks.clear_log()
        self._requests.clear()

    def _list_routes(self) -> tuple[RouteInfo, ...]:
        return tuple(RouteInfo.of(route) for route in self._router.routes())

    # -- the seam -----------------------------------------------------------

    def handle(self, req: UnitRequest) -> UnitResponse:
        """The only thing that crosses the core/transport seam. Every failure leaves through ``_finish`` with a
        vendor-shaped body and an ``x-unit-error`` header. INVARIANT: **the kernel never raises for an unmatched
        request** -- it answers the vendor's 404 with the near-miss header, since there is nothing to raise into
        across a socket."""
        started = time.monotonic()
        route: Route | None = None
        trace = _Trace()
        try:
            outcome = self._router.match(req.method, req.path)
            if isinstance(outcome, MethodNotAllowed):
                raise UnitError(
                    UnitErrorKind.METHOD_NOT_ALLOWED,
                    detail=(f"{req.method} is not allowed on {req.path}. Allowed: {', '.join(outcome.allowed)}."),
                    info={"allowed": list(outcome.allowed)},
                )
            if not isinstance(outcome, Match):
                trace.near_misses = self._near_misses(req)
                shaped = self._vendor.errors.not_found(req, self._ctx)
                answer = self._shape(shaped, UnitErrorKind.NOT_FOUND)
                # The header rides on the vendor's shaped response; the body
                # is untouched, so a real 404 stays a real 404.
                answer = UnitResponse(
                    status=answer.status,
                    headers={**answer.headers, NEAR_MISS_HEADER: near_miss_header(trace.near_misses)},
                    body=answer.body,
                )
                return self._finish(req, answer, None, started, trace)

            route = outcome.route
            args = HandlerArgs(req=req, params=outcome.params, ctx=self._ctx, route=route)
            if route.serialized:
                with self._lock:
                    res = self._run_pipeline(req, route, args, trace)
            else:
                res = self._run_pipeline(req, route, args, trace)
            return self._finish(req, res, route, started, trace)
        except UnitError as err:
            shaped_error = self._shape(
                self._vendor.errors.shape(err, self._ctx),
                err.kind,
                delay_ms=err.delay_ms,
                fault=err.fault,
                rule_id=err.rule_id,
                delay_asked_ms=_delay_asked_ms(err),
            )
            # An error that left by raising is still the answer this caller gets, so a fault armed at step 3 applies
            # to it as step 9 would; otherwise the rule's ``when.times`` budget buys nothing. Nothing applies twice:
            # step 9 sets the flag before it tries. The attempt can itself raise, so it gets its own ``try`` and
            # answers the same diagnostic. provenance: judgment.
            if (
                trace.decision is not None
                and not trace.response_fault_attempted
                and trace.decision.fault in RESPONSE_PHASE_FAULTS
            ):
                trace.response_fault_attempted = True
                try:
                    shaped_error = apply_response_fault(trace.decision, shaped_error, log=self._log)
                except UnitError as fault_err:
                    shaped_error = self._shape(
                        self._vendor.errors.shape(fault_err, self._ctx),
                        fault_err.kind,
                        delay_ms=fault_err.delay_ms,
                        fault=fault_err.fault,
                        rule_id=fault_err.rule_id,
                    )
            return self._finish(req, shaped_error, route, started, trace)
        except Exception as exc:
            # Not a UnitError: a defect in a handler or in this file. Logged as
            # one, then answered as the vendor's own 500.
            self._log.error("unhandled error", {"path": req.path, "error": _describe(exc)})
            internal = UnitError(UnitErrorKind.INTERNAL, detail=_describe(exc))
            shaped = self._vendor.errors.shape(internal, self._ctx)
            return self._finish(req, self._shape(shaped, internal.kind), route, started, trace)

    def _near_misses(self, req: UnitRequest) -> tuple[NearMiss, ...]:
        """The closest routes among the ones this unit is *currently* serving. Internal routes and routes behind a
        disabled capability are excluded, being no part of the surface a consumer mistyped."""
        return near_misses(
            req.method,
            req.path,
            (route for route in self._routes if not route.internal and self._capabilities.is_enabled(route.capability)),
        )

    # -- the pipeline -------------------------------------------------------

    def _run_pipeline(self, req: UnitRequest, route: Route, args: HandlerArgs, trace: _Trace) -> UnitResponse:
        # 1. internal short-circuit -----------------------------------------
        if route.internal:
            return normalize(route.handler(args))

        # 2. capability gate -------------------------------------------------
        self._capabilities.assert_enabled(route.capability, route.key)

        # 3. fault selection -------------------------------------------------
        selection = self._selector.select_request(
            ChaosSubject(
                scope="request",
                route_key=route.key,
                method=req.method,
                path=req.path,
                capability=route.capability,
                headers=req.headers,
                body_text=args.body_text(),
            ),
            lambda: self._in_band(req, args),
        )
        decision = selection.decision
        if decision is not None:
            # Before the fault is applied, not after: applying it can raise,
            # and an unrecorded 429 is one a consumer cannot explain.
            trace.fault = decision.fault
            trace.rule_id = decision.rule_id
            trace.decision = decision

        # 4. pre-auth faults -------------------------------------------------
        if decision is not None:
            apply_request_fault(decision, "pre", clock=self._clock, log=self._log)

        # 5. auth and scopes -------------------------------------------------
        auth: AuthResult | None = None
        if route.auth is not None:
            auth = self._vendor.auth.resolve(args, route.auth)
            missing = [scope for scope in route.scopes if scope not in auth.scopes]
            if missing:
                raise UnitError(
                    UnitErrorKind.FORBIDDEN_SCOPE,
                    detail=f"The access token is missing the required permission(s): {', '.join(missing)}.",
                    info={"missing": missing, "granted": list(auth.scopes)},
                )

        # 6. post-auth fault (token_expiry only, unconditional) --------------
        if decision is not None:
            apply_request_fault(decision, "post_auth", clock=self._clock, log=self._log)
        args.auth = auth

        # 7. idempotency -----------------------------------------------------
        idem = route.idempotency
        idem_key: str | None = None
        request_digest = ""
        replayed: UnitResponse | None = None
        if idem is not None:
            body = args.body()
            raw = dot_get(body, idem.key_path)
            if isinstance(raw, str) and raw:
                idem_key = raw
                request_digest = digest_of(dict(body))
                stored = self._store.get_idempotent(idem.scope, idem_key)
                if stored is not None:
                    # Bound, not returned: step 9 faults a replay as it would a fresh answer.
                    replayed = self._replay(
                        idem.scope, idem.key_path, idem.on_mismatch, idem_key, request_digest, stored
                    )
            elif idem.required:
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail=f"{idem.key_path} is required.",
                    field=idem.key_path,
                )

        # 8. handler, then store the response against the idempotency key ----
        # Skipped whole on a replay: the vendor committed once already.
        if replayed is not None:
            res = replayed
        else:
            trace.journal_seq_before = self._store.journal_seq
            try:
                res = normalize(route.handler(args))
            finally:
                # In a ``finally``: a handler that committed and then raised has still committed.
                trace.journal_seq_after = self._store.journal_seq
            # INVARIANT: what is recorded is the handler's CLEAN answer, before any response-phase fault touches it.
            # The handler has committed and this store has no rollback, so a retry replays what the vendor really
            # committed; recording the faulted answer would stamp every later replay, and skipping the record would
            # let a retry charge the caller twice. provenance: judgment.
            if idem is not None and idem_key is not None and 200 <= res.status < 300:
                self._store.put_idempotent(
                    IdempotencyRecord(
                        scope=idem.scope,
                        key=idem_key,
                        request_digest=request_digest,
                        status=res.status,
                        headers=dict(res.headers),
                        body_b64=b64url_encode(res.body),
                        stored_at=self._clock.iso_ms(),
                    )
                )
        # 9. response-phase fault, on whichever answer step 8 produced -------
        # It corrupts a REAL answer, so it runs only after the handler produced
        # one, and it never touches ``ctx``. A replay is faulted as a fresh
        # answer is; paths that never reach here are faulted by ``handle``.
        # provenance: judgment -- no vendor documents whether its edge would
        # corrupt a replayed answer.
        if decision is not None and decision.fault in RESPONSE_PHASE_FAULTS:
            trace.response_fault_attempted = True
            res = apply_response_fault(decision, res, log=self._log)
        return res

    # -- pipeline helpers ---------------------------------------------------

    def _in_band(self, req: UnitRequest, args: HandlerArgs) -> MagicExtraction:
        """Scan the request for a magic value, tolerating an unreadable body: the handler is entitled to produce the
        real error a moment later. Reads the content-type-general ``body()``, so a declared body path is reachable
        on a form-encoded request. provenance: judgment."""
        try:
            parsed: object = args.body()
        except UnitError:
            parsed = None
        return extract_magic(self._vendor.magic, req, parsed)

    def _replay(
        self,
        scope: str,
        key_path: str,
        on_mismatch: str,
        key: str,
        request_digest: str,
        stored: IdempotencyRecord,
    ) -> UnitResponse:
        """Return the stored response, or refuse a reused key with a new body. ``x-unit-idempotent-replay`` is always
        stamped, and ``x-unit-idempotent-ignored-body`` too when the body differed under ``replay``: a 200 whose
        update was silently discarded is DOCUMENTED vendor behaviour a consumer cannot otherwise observe."""
        same_body = stored.request_digest == request_digest
        if not same_body and on_mismatch == "conflict":
            raise UnitError(
                UnitErrorKind.IDEMPOTENCY_CONFLICT,
                detail="The idempotency key can only be retried with the same request data.",
                field=key_path,
                info={"key": key, "scope": scope},
            )
        self._log.debug("idempotent replay", {"scope": scope, "key": key, "same_body": same_body})
        headers = dict(stored.headers)
        headers["x-unit-idempotent-replay"] = "true"
        if not same_body:
            headers["x-unit-idempotent-ignored-body"] = "true"
        return UnitResponse(status=stored.status, headers=headers, body=b64url_decode(stored.body_b64))

    # -- response shaping ---------------------------------------------------

    def _shape(
        self,
        shaped: ShapedError,
        kind: UnitErrorKind,
        *,
        delay_ms: int = 0,
        fault: str | None = None,
        rule_id: str | None = None,
        delay_asked_ms: str | None = None,
    ) -> UnitResponse:
        """The vendor's error body, plus the ``x-unit-error`` header a conformance check asserts on across vendors
        whose bodies share no field. ``delay_ms`` is the only way a refusal asks its binding to hold the answer
        back. ``fault``/``rule_id`` become the ``vendorfake-fault``/``vendorfake-rule`` headers, the same two
        stamped on a corrupted success; a ``rule_id`` with no ``fault`` is a rule-authoring refusal and becomes
        ``vendorfake-rule-error``."""
        headers = dict(shaped.headers)
        headers["x-unit-error"] = kind.value
        if fault is not None and rule_id is not None:
            headers["vendorfake-fault"] = fault
            headers["vendorfake-rule"] = header_text(rule_id)
        elif rule_id is not None:
            # A rule was involved but no fault fired: the payout refused its own params. Without this the caller
            # sees an unexplained 400 and reads it as the vendor failing.
            headers["vendorfake-rule-error"] = header_text(rule_id)
        if delay_asked_ms is not None:
            # What the rule ASKED for, on either clock -- distinct from ``delay_ms``, which is zero on a virtual
            # clock. The in-process transport compares this against the client's read timeout, so the same rule
            # raises ``ReadTimeout`` on both clocks.
            headers[DELAY_ASKED_HEADER] = delay_asked_ms
        answered = normalize(ReplyInit(status=shaped.status, json=shaped.body, headers=headers))
        if delay_ms <= 0:
            return answered
        return UnitResponse(status=answered.status, headers=answered.headers, body=answered.body, delay_ms=delay_ms)

    def _finish(
        self,
        req: UnitRequest,
        res: UnitResponse,
        route: Route | None,
        started: float,
        trace: _Trace,
    ) -> UnitResponse:
        headers = dict(res.headers)
        headers[REQUEST_ID_HEADER] = req.id
        if route is not None and not route.internal:
            self._vendor.decorate(headers, self._ctx, req)
        elapsed_ms = (time.monotonic() - started) * 1000
        self._log.debug(
            "request",
            {
                "method": req.method,
                "path": req.path,
                "status": res.status,
                "route": route.key if route is not None else None,
                "ms": round(elapsed_ms, 3),
            },
        )
        # Recorded on the one path every answer leaves through. Excluded by path and not only by matched route: an
        # unmatched control-plane request is still the observer's own traffic. ``is_control_path`` is the one
        # definition of that namespace.
        if (route is None or not route.internal) and not is_control_path(req.path):
            # "Committed" is a seq that moved; "discarded" is a commit the caller was not handed cleanly. A fault in
            # ``INTACT_RESPONSE_FAULTS`` did neither: only later.
            committed = trace.journal_seq_after > trace.journal_seq_before
            deprived = trace.response_fault_attempted and (
                trace.decision is None or trace.decision.fault not in INTACT_RESPONSE_FAULTS
            )
            self._requests.record(
                RequestRecord(
                    id=req.id,
                    received_at=req.received_at,
                    method=req.method,
                    path=req.path,
                    route=None if route is None else route.key,
                    operation_id=None if route is None else route.operation_id,
                    status=res.status,
                    matched=route is not None,
                    fault=trace.fault,
                    rule_id=trace.rule_id,
                    duration_ms=round(elapsed_ms),
                    near_misses=trace.near_misses,
                    committed_journal_seq=trace.journal_seq_after if committed else None,
                    discarded_mutation=committed and deprived,
                )
            )
        # ``decorate`` sees only the headers: the delay and the connection are not vendor opinions.
        return UnitResponse(
            status=res.status, headers=headers, body=res.body, delay_ms=res.delay_ms, transport=res.transport
        )


def _delay_asked_ms(err: UnitError) -> str | None:
    """The ``timeout`` fault's requested delay, for :data:`DELAY_ASKED_HEADER`; ``None`` otherwise. Read from ``info``,
    the consumer-visible copy filled on either clock, since ``delay_ms`` is already zero on a virtual one."""
    if err.fault != "timeout" or err.info is None:
        return None
    asked = err.info.get("delay_ms")
    return None if asked is None else str(asked)


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return text if text else exc.__class__.__name__
