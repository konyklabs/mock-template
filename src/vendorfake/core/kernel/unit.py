"""The unit: composition root, and the nine-step request pipeline.

FOR: assembling a vendor definition and a resolved profile into the one object
that answers requests, and running every request through the same ordered
sequence of decisions so that "what happens before what" is a property of this
file rather than of each vendor's handlers.

INVARIANT: **the order below is the specification.** It is ported from
``packages/core/src/kernel/unit.ts`` and the steps carry numbered comments so a
reviewer can diff them against the reference rather than re-deriving them --
the reference's eight, with its step 8 split in two here so that the
response-phase fault is applied to a replayed answer as well. Each
boundary is observable, and each is pinned by a test that would pass under a
plausible wrong order:

1. **internal short-circuit** -- a control-plane route runs with no capability
   gate, no fault selection, no auth and no idempotency. If it did not, a unit
   with a capability disabled could not be re-enabled through its own control
   plane, and a chaos rule matching ``*`` would make the unit unrecoverable.
2. **capability gate** -- *before* authentication. A disabled capability is a
   property of the deployment, not of the caller, so it must answer the same
   way whether or not a token was presented. Answering ``unauthorized`` first
   would tell a consumer to go fix their credentials for a route that is
   switched off.
3. **fault selection** -- exactly once per request, through the single choke
   point in ``chaos/selector.py``, which applies the ``chaos`` capability gate
   before it parses anything. One selection, two application phases: a rule
   that matches must not be evaluated twice and count twice.
4. **pre-auth faults** -- everything except ``token_expiry``. A rate limit or
   an outage does not care who is calling, and injecting it before auth is what
   lets a consumer test their backoff without holding a valid token.
5. **auth and scopes** -- the vendor resolves the credential; the *kernel*
   checks ``Route.scopes`` against the result. Checked here rather than in the
   vendor because a second place to check is a second place to forget.
6. **post-auth fault** -- ``token_expiry`` only, and applied unconditionally,
   i.e. whether or not this route declared ``auth``. Unconditional because the
   phase belongs to the fault, not to the call site; ``chaos/faults.py`` owns
   which phase a fault fires in, and a pipeline that only ran this step for
   authenticated routes would be a second, divergent copy of that rule.
7. **idempotency** -- lookup and replay, *after* auth, so a stored response is
   never handed to an unauthenticated caller who guessed a key, and after the
   fault phases, so an injected 500 does not consume a key. A hit *binds* the
   replay rather than returning it, so step 9 still runs.
8. **handler and idempotency store** -- on a miss only: the handler runs and
   its clean 2xx response is stored against the key.
9. **response-phase fault** -- applied to whichever answer step 8 left,
   handler's or replay's, so a decision drawn at step 3 is paid out on every
   answer the *vendor* produced, errors included: ``handle``'s error-shaping
   block applies it to the answer a raise produced. A framework crash is not
   a vendor answer and is never faulted -- the ``except Exception`` block
   answers a 500 untouched. The caller's ``when.times`` budget therefore buys
   only faults that were applied to an answer they received -- with two
   named exceptions: a payout whose own params are invalid spends the budget
   and answers the 400 diagnostic naming the rule instead, and a framework
   crash spends it on the clean 500 above.

The router match happens **before** step 1 and produces both the ``no_route``
404 and the ``method_not_allowed`` 405; ``finish()`` stamps
``x-unit-request-id`` and ``decorate`` gives the vendor its last chance, both
after step 9 on the success path *and on every error path*. They live outside
the numbering, where the reference puts them.

``decorate`` runs on shaped errors too, for any matched non-internal route --
the reference's own transport test asserts the vendor's API-version header on a
400 -- and never on the 404 path, where no route matched and there is no vendor
opinion to apply.

WHAT DID NOT SURVIVE THE PORT

``kernel/bindings.ts``' ``WeakMap<UnitContext, ControlBinding>``
    A workaround for not wanting to widen an interface. The design point it
    protected is real and is kept: :class:`UnitContext` exposes the store, the
    capabilities, the chaos engine, the clock, the rng, the log, the config and
    the vendor, and **not** ``hydrate`` or ``list_routes`` -- a route handler
    has no business re-seeding the store or enumerating the router. Here the
    control plane is handed a typed :class:`ControlBinding` at construction
    instead, which is the same guarantee without a global side table.

The forward-declared ``let ctx`` closure
    The reference declares ``ctx`` before it is assigned so the dispatcher can
    capture a getter for it. Construction order here makes that unnecessary.

``process.env``
    Read nowhere in this file. The log level arrives on
    :attr:`ResolvedConfig.log_level`, which the profile loader resolved from a
    mapping its caller passed -- ``{}`` unless the CLI passed the real one.

DELAYS LEAVE AS DATA. A fault that wants the caller made to wait sets
``UnitError.delay_ms``; ``_shape`` copies it onto ``UnitResponse.delay_ms`` and
``_finish`` carries it through. Nothing in this file, or anywhere below the
seam, sleeps for it. The kernel decides *whether* to delay and the binding
decides *how*, because only the binding knows the caller's clock and the
caller's timeout -- and because a ``time.sleep`` here would block a request
thread, an event loop and a file-drop poller with the same line. See
``core/chaos/faults.py`` for the reversal that produced this.

THE REQUEST LOCK. ``handle`` is synchronous and takes one re-entrant lock for
the whole pipeline, unless the matched route declares ``serialized=False``.
The lock is what makes id minting and journal ordering deterministic, so that
two runs of the same scenario produce the same ids and a transcript is diffable
evidence rather than noise -- which Node gave the reference for free from its
single thread. It is taken *after* the router match, because the match decides
whether to take it at all.
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
    MutableResponse,
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
    "DispatcherFactory",
    "RequestLog",
    "RouteInfo",
    "Unit",
    "make_request",
]

DispatcherFactory = Callable[..., WebhookDispatcher]
"""How a caller supplies its own dispatcher, given the constructor's arguments.

The same shape of seam as ``fault_selector`` and for the same reason: the
``webhooks`` capability gate lives inside the listener
``WebhookDispatcher.attach`` registers, and the claim that the gate is real
must be falsifiable by something other than reading. A conformance mutant
installs a dispatcher whose gate is missing and asserts the contract goes red.
Production callers pass nothing and get the one correct dispatcher.
"""

REQUEST_ID_HEADER = "x-unit-request-id"
DELAY_ASKED_HEADER = "vendorfake-delay-ms"
"""On a ``timeout``-faulted answer: the ``delay_ms`` the rule asked for, as the
rule spelled it, whichever clock the unit runs on. See ``Unit._shape``."""
"""Echoed on every response, and honoured on the way *in*.

A binding that mints a fresh id for a request whose caller already supplied one
breaks cross-transport correlation: the same logical call gets two identities
depending on which binding carried it."""


def _now_iso() -> str:
    """Wall clock, RFC 3339 with milliseconds.

    Deliberately *not* the unit's :class:`Clock`: ``received_at`` records when
    a binding actually took delivery of the request, which is a fact about the
    world and not about the scenario. A virtual clock frozen in 2024 must not
    make every request look as though it arrived then.
    """
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
    """Build a :class:`UnitRequest` the way every binding must build one.

    Shared rather than duplicated per binding, because the four normalisations
    below are exactly the ones a second binding gets subtly wrong:

    * header names are lower-cased, so ``args.header()`` is case-insensitive
      without every handler saying so;
    * a ``?query=string`` on ``path`` is split off and parsed with blank values
      kept, and ``query`` may be a mapping or ``(name, value)`` pairs in
      arrival order; ``query_all`` keeps every value and ``query`` the last,
      so a repeated key is never lost on one binding and kept on another;
    * ``raw_body`` wins over ``body``; supplying ``body`` serialises it and
      defaults the content type to JSON, which is what makes
      ``post(path, {...})`` mean what a caller expects while leaving the exact
      received bytes reachable for signature checks;
    * the id is the inbound ``x-unit-request-id`` when the caller supplied one.
    """
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
    #: Scopes the kernel will require of the resolved credential. Published
    #: because "this route needs a token" and "this route needs a token
    #: carrying ORDERS_WRITE" are different facts, and only the second one
    #: lets a caller tell an insufficient credential from a missing one
    #: before sending anything.
    scopes: tuple[str, ...] = ()
    #: How this route deduplicates a retried request, or ``None``.
    idempotency: dict[str, object] | None = None
    #: A body this route accepts. See :attr:`Route.example_body`.
    example_body: Mapping[str, Any] | None = None
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
            example_body=route.example_body,
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
            ("example_body", None if self.example_body is None else dict(self.example_body)),
            ("operation_id", self.operation_id),
            ("summary", self.summary),
        ):
            if value is not None:
                body[key] = value
        return body


class RequestLog:
    """A bounded ring of :class:`RequestRecord`, newest last.

    FOR: answering "what did my code call, and did anything answer it?". The
    journal cannot: it records committed *mutations* by design, so a 4xx, a
    read, and a request that matched no route at all leave no trace in it --
    which is precisely the set of calls a consumer debugging an integration
    wants to see.

    BOUNDED, because a fake lives inside a test process and an unbounded log
    would be a slow leak proportional to suite length. Oldest evicted first: a
    consumer asking what just happened is nearly always asking about the end of
    the run, and the alternative -- refusing to record once full -- loses the
    part they wanted.

    **Control-plane requests are not recorded**, and that is the caller's rule
    to apply rather than this class's: ``/__unit/*`` is the observer, and a log
    that recorded the reads of itself would grow by one row for every question
    asked of it and bury the traffic under the instrumentation.

    Its own lock, like the store's and the clock's. The kernel's request lock
    is released for a route declaring ``serialized=False``, and two such
    requests finishing at once must not interleave a deque append.
    """

    __slots__ = ("_capacity", "_lock", "_records")

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError(f"request log capacity must be zero or more, got {capacity}")
        self._capacity = capacity
        # `maxlen` is the eviction, rather than a length check on append: one
        # place for the bound means it cannot be enforced on one path and
        # forgotten on another.
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
        """Matching records, **newest first**.

        Newest first because the question is nearly always "what did my code
        just do", and a consumer who wanted the other order has a list to
        reverse. ``limit`` is applied after filtering, so
        ``requests(operation_id=X, limit=1)`` is the most recent call to X and
        not "the most recent call, if it happened to be X" -- the second
        reading would answer ``None`` for a route that had definitely been
        called, which is worse than useless in an assertion.

        Every filter is a conjunction, and ``None`` means "do not filter":
        ``unmatched=False`` is therefore "only the matched ones" rather than
        "no filter", which is the distinction a boolean default of ``False``
        would have thrown away.
        """
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
    """Unit internals the control plane needs and a route handler must not have.

    Two callables and one object rather than a reference to the unit, so the
    surface is enumerable: this is the complete list of things ``/__unit/*``
    can do that a vendor handler cannot.
    """

    #: Wipe state and re-apply the seed document.
    hydrate: Callable[[], None]
    #: Every registered route, control routes included.
    list_routes: Callable[[], tuple[RouteInfo, ...]]
    #: The request log, read and cleared by ``/__unit/requests``.
    #:
    #: The object rather than two adapter callables: its own methods are
    #: already the narrow surface, and wrapping them would only add a second
    #: place for the filter semantics to be stated. It is emphatically NOT on
    #: :class:`UnitContext`, for the same reason ``hydrate`` is not -- a vendor
    #: handler that could read the log could branch on what the caller did
    #: earlier, and a fake whose answers depend on the shape of a test run is
    #: not reproducible.
    #:
    #: Required rather than defaulted to an empty log, which would let a
    #: mis-wired plane answer ``{"count": 0}`` forever and read as "your code
    #: called nothing".
    requests: RequestLog


@dataclass(slots=True)
class _Trace:
    """What one request picked up on its way through, for the request log.

    Mutable and passed down rather than returned back up, because the two
    facts it carries are learned on paths that then *raise*: an armed fault
    usually leaves through ``apply_request_fault``, and a near-miss list is
    computed where no route exists to return anything. A return value would be
    lost in exactly the cases worth recording.

    The armed :class:`ChaosDecision` itself rides along for the same reason:
    a request that leaves the pipeline by raising still owes the caller
    whatever the decision promised, and ``handle``'s error-shaping block is
    the only place left that can pay it out.
    """

    fault: str | None = None
    rule_id: str | None = None
    #: ``store.journal_seq`` immediately before and after the handler ran --
    #: read inside ``_run_pipeline`` step 8, so on a ``serialized`` route
    #: (every shipped route) both reads happen under the pipeline lock and no
    #: other request's commit can land between them. Read at the top of
    #: ``handle`` instead, before the lock, the ASGI threadpool could
    #: attribute a concurrent request's commit to this one (found by review
    #: of konyklabs/roadmap#101). A fork's ``serialized=False`` route gets a
    #: best-effort window instead; a request that never reached the handler
    #: leaves both at zero, which reads as "committed nothing".
    journal_seq_before: int = 0
    journal_seq_after: int = 0
    #: The decision drawn at step 3, whole -- ``fault``/``rule_id`` above are
    #: the two fields the request log wants, kept as they were because other
    #: code reads them.
    decision: ChaosDecision | None = None
    #: Whether the payout has already been *tried* on this request. Set before
    #: the attempt, not after, because the attempt itself can raise: two of the
    #: five response-phase faults reject bad params with a ``UnitError``, and
    #: without this the error path would call them again and raise from inside
    #: the ``except`` clause, escaping ``handle`` entirely.
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
    """``GET /__unit/errors`` reads every row's provenance from
    ``ErrorShaper.describe()``. A shaper without the method would fail at
    request time with a leaked ``'NoneType' object is not callable``; one
    whose table is short would publish ``provenance: null`` and 200. Both are
    startup failures here, naming the vendor and the kinds, so the plane can
    treat a missing provenance as the unreachable case it then is.
    """
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
    """One running fake: a vendor surface, a profile, and the pipeline.

    Everything below the constructor is shared machinery. A vendor supplies a
    :class:`VendorDefinition` and nothing else; the pipeline, the capability
    gate, the chaos hooks, idempotency and the control-plane wiring are the
    same for every vendor, which is the property that makes adding one a data
    exercise.
    """

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
        fault_selector: Callable[[ChaosEngine, CapabilityRegistry], FaultSelector] | None = None,
        dispatcher: DispatcherFactory | None = None,
    ) -> None:
        self._vendor = vendor
        self._config = config
        self._seed = seed
        self._log: Logger = JsonLogger(config.log_level) if logger is None else logger

        # Before anything else: a vendor that gates on a core capability it
        # never declared would have that behaviour silently off. This is a
        # startup failure naming every problem at once.
        assert_capability_declarations(vendor.capabilities, vendor.not_supported)
        _assert_error_shaper_describes(vendor)

        self._clock = Clock(config.clock.mode, config.clock.start)
        # One stream for the unit, seeded from the profile and reported by the
        # control plane. A vendor salts its own id stream off the same seed so
        # that adding a probability rule does not renumber every generated id.
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
        # Router.add is where a vendor route claiming the control-plane
        # namespace is refused, so every construction path passes that gate.
        self._router = Router(self._routes)
        self._capabilities = CapabilityRegistry(vendor.capabilities, self._routes, config.capabilities, config.profile)
        # The single arming point, injected rather than constructed inline.
        # `chaos/selector.py` states that the one-shot leak is *unrepresentable*
        # there rather than merely tested for -- a claim the conformance suite
        # has to be able to falsify. This seam is how: the mutant fixtures in
        # `tests/conformance/mutants/` install a deliberately leaky selector and
        # assert C12 goes red, without patching a module. Production callers
        # pass nothing and get the one correct selector.
        self._selector = (
            FaultSelector(self._chaos, self._capabilities)
            if fault_selector is None
            else fault_selector(self._chaos, self._capabilities)
        )

        self._assert_retry_schedule()
        self._report_dead_chaos_rules()

        # An HTTP sink by default, built here but connecting nothing: its
        # client is created on first send, so a unit whose vendor has no
        # webhooks opens no connection pool.
        self._sink: DeliverySink = HttpSink() if sink is None else sink
        # `dispatcher` is the second collaborator seam, alongside
        # `fault_selector`, and it exists for the same reason: the `webhooks`
        # capability gate is a line inside `WebhookDispatcher.attach`, and a
        # contract asserting that the gate is real needs a unit whose gate is
        # not. Production callers pass nothing.
        build_dispatcher: DispatcherFactory = WebhookDispatcher if dispatcher is None else dispatcher
        self._webhooks = build_dispatcher(
            store=self._store,
            clock=self._clock,
            # The dispatcher reaches chaos only through the one choke point,
            # which applies the `webhooks.chaos` gate before anything is armed.
            selector=self._selector,
            sink=self._sink,
            retry=config.webhooks.retry,
            # Deferred, because the context needs the dispatcher and the
            # dispatcher needs the context. The reference does the same with a
            # forward-declared `let ctx`; a callable says so out loud.
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
        # After the context exists, because the listener reaches through it on
        # its first journal entry. The `webhooks` capability gate lives inside
        # the listener rather than around this call; see `attach`.
        self._webhooks.attach()
        self._lock = threading.RLock()

    # -- construction-time assertions ---------------------------------------

    def _assert_retry_schedule(self) -> None:
        """A declared ``webhooks`` capability needs a non-empty retry schedule.

        The core ships no schedule -- one is a documented property of a
        particular vendor's webhook system, and the config layer may not import
        a vendor -- so the vendor's ``retry_defaults`` is merged under the
        profile document. If that merge did not happen, every delivery would
        exhaust on its first attempt and present as "the subscriber is
        unreachable" rather than as a configuration mistake. Checked here
        because this is the first place that knows both what the vendor
        declared and what the profile resolved to.
        """
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
        """A profile rule whose ``match.route`` names no route can never fire.

        The reference's ``validateRule`` checks ``id``, ``fault`` and ``scope``
        and never checks the route, so a typo -- or a path template that moved
        from ``:order_id`` to ``{order_id}`` -- is a rule that matches nothing,
        forever, silently. The first symptom is a chaos transcript in which two
        of four rules did nothing and nobody the wiser.

        Checked here because this is the first moment both facts exist: the
        parsed rules and the assembled route table. Internal routes are
        excluded from the table because the pipeline short-circuits them before
        fault selection runs, so a rule "matching" one could still never fire.

        A NOTE by default and a hard ``invalid_value`` under
        ``config.chaos.strict_rules``. Both, because refusing outright would
        reject a rule aimed at a route whose capability is temporarily switched
        off, which is a legitimate thing to write; and staying silent is the
        defect this exists to close.
        """
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
        """Settle delivery, then release every timer.

        Drain first and clear second, in that order and not the other: clearing
        the timers first would discard the retries a drain is meant to settle
        and make ``stop()`` silently lose deliveries a test had already caused.
        The worker is stopped after the drain so no thread outlives the unit --
        a fake must not outlive the test that built it, and a daemon thread
        that survives one test shows up as a mystery in the next.
        """
        self._webhooks.drain()
        self._webhooks.stop()
        self._clock.clear_all()

    def _hydrate(self) -> None:
        """Reset, re-seed, then re-declare the profile's subscribers.

        Order is the reference's (``unit.ts:hydrate``) and each step depends on
        the one before: ``reset`` empties every collection including the
        subscriptions, so config subscribers must be re-inserted after it or a
        ``POST /__unit/state/reset`` would silently deregister them.
        ``clear_log`` is last so the transcript starts at the scenario rather
        than spanning the one before it -- and note that the re-insertion above
        journals, which is exactly why the dispatcher ignores mutations to the
        subscription collection.

        The request log is cleared with them, and for the same reason: a reset
        says "this is the beginning", and a per-test ``reset()`` that left the
        previous test's calls in the log would make ``assert_called(..., times=1)``
        pass or fail on test order.
        """
        self._store.reset()
        self._vendor.hydrate(self._ctx, self._seed)
        self._webhooks.load_config_subscribers(self._config.webhooks.subscribers)
        self._webhooks.clear_log()
        self._requests.clear()

    def _list_routes(self) -> tuple[RouteInfo, ...]:
        return tuple(RouteInfo.of(route) for route in self._router.routes())

    # -- the seam -----------------------------------------------------------

    def handle(self, req: UnitRequest) -> UnitResponse:
        """The only thing that crosses the core/transport seam.

        Synchronous by design. Every failure leaves through ``finish()`` with a
        vendor-shaped body and an ``x-unit-error`` header, so no caller ever
        receives a framework's own error document.

        **The kernel never raises for an unmatched request.** It answers the
        vendor's own 404 with the near-miss diagnosis in a header, and the
        binding decides whether that is a failure -- see
        ``config/models.py::UnmatchedPolicy``. Raising here would make a served
        unit unable to honour the same profile as an in-process one, since
        there is nothing to raise *into* across a socket.
        """
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
                # The header rides on the response the vendor shaped; the BODY
                # is untouched, because a consumer rehearsing what their code
                # does with a real 404 must get the real one.
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
            # An error that left the pipeline by *raising* -- a 401, a missing
            # scope, a missing idempotency key, an idempotency conflict -- is
            # still the answer this caller gets, so a response-phase fault
            # armed at step 3 applies to it exactly as step 9 applies it to a
            # handler's answer or to a replay. Without this the fault is drawn
            # and counted and nothing is delivered: the rule's ``when.times``
            # budget is spent on a clean 401 and the request log claims a
            # fault the caller never saw.
            #
            # Nothing is applied twice. A request-phase fault raises its own
            # ``UnitError`` at step 4 or 6 and is not in
            # ``RESPONSE_PHASE_FAULTS``, so this branch skips it and its
            # ``fault``/``rule_id`` reach the response through ``_shape``
            # above. Response-phase faults need the flag instead, because two
            # of the five *do* raise: ``body_mutation`` with no ``params.ops``
            # and ``malformed_body`` with an unknown ``params.mode`` are
            # rejected here rather than at rule-add time, where no route table
            # exists to check them against. Step 9 sets the flag before it
            # tries, so a payout that raised is not attempted a second time --
            # which would raise again from inside this ``except`` clause and
            # take the exception straight out of ``handle``, costing the caller
            # the 400 that names the bad rule.
            #
            # The FIRST attempt can raise here too, and the flag cannot help
            # with that one: an error raised before step 9 arrives with the
            # payout still owed, and the shaped error is a different document
            # from the handler's -- a ``body_mutation`` pointer that exists in
            # a payment does not exist in a 403 envelope. So the attempt gets
            # its own ``try``, and a payout that cannot be applied answers the
            # same diagnostic it would have answered from step 9, whichever
            # path reached it. Nothing leaves ``handle`` by raising except the
            # framework path below.
            # provenance: judgment -- a vendor's edge does not know which of
            # its own answers is an error, and this project decided the
            # decision is honoured on whatever the caller is handed.
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
            # Not a UnitError, so nothing in the core meant this: it is a defect
            # in a handler or in this file. It is logged as one, then answered
            # as the vendor's own 500 so that no caller ever receives a Python
            # traceback or a framework's error document.
            self._log.error("unhandled error", {"path": req.path, "error": _describe(exc)})
            internal = UnitError(UnitErrorKind.INTERNAL, detail=_describe(exc))
            shaped = self._vendor.errors.shape(internal, self._ctx)
            return self._finish(req, self._shape(shaped, internal.kind), route, started, trace)

    def _near_misses(self, req: UnitRequest) -> tuple[NearMiss, ...]:
        """The closest routes among the ones this unit is *currently* serving.

        Internal routes are excluded because the control plane is the observer
        and is not what a consumer mistyped; a route behind a disabled
        capability is excluded because it is not part of the surface right now,
        and naming it would send a reader looking for a typo instead of for the
        profile that switched it off.
        """
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
            # Recorded before the fault is applied, not after: applying it can
            # raise, and a rate limit that left no trace in the request log
            # would be exactly the 429 a consumer cannot explain.
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
                    # Bound, not returned: a response-phase fault armed at step
                    # 3 still owes this caller its answer, and step 9 applies it
                    # to the replay exactly as it would to a fresh one.
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
        # Skipped whole on a replay: the vendor committed once, and step 7
        # already has the answer that commit produced.
        if replayed is not None:
            res = replayed
        else:
            trace.journal_seq_before = self._store.journal_seq
            try:
                res = normalize(route.handler(args))
            finally:
                # In a ``finally`` because a handler that committed and then
                # raised has still committed: the shaped error the caller gets
                # is exactly the "looked like it failed" case the request log
                # exists to name.
                trace.journal_seq_after = self._store.journal_seq
            # WHAT GETS RECORDED IS THE HANDLER'S CLEAN ANSWER, RECORDED BEFORE
            # ANY RESPONSE-PHASE FAULT TOUCHES IT.
            #
            # The handler has already run: it created the entity and journalled
            # it. That commit is exactly what an idempotency key exists to make
            # safe to retry, and this store has no transaction to undo it with
            # (``core/state/store.py`` has no rollback and no savepoint). So the
            # record is written here, between the handler and the fault, and the
            # retry replays the answer the vendor really committed.
            #
            # Two earlier shapes were both wrong, and the reasons are worth
            # keeping. First this stored whatever the handler-plus-fault pipeline
            # produced, on the theory that "a replay gets exactly what the caller
            # got the first time, faulted or not" -- but ``IdempotencyRecord``
            # has nowhere to put a ``UnitResponse.transport`` directive, so a
            # stored ``connection_reset`` or ``slow_body`` replayed as a clean
            # 200 that still carried the ``vendorfake-fault`` header the real
            # fault stamped, silently switching off any validator that trusts
            # :func:`is_transport_fault` (``core/chaos/faults.py``) on every
            # later replay of that key. Then it skipped the record entirely for a
            # faulted response, by analogy with a request-scope fault. That
            # analogy is false: a request-scope fault raises at step 4 or 6,
            # *before* ``route.handler(args)`` ever runs, so nothing was
            # committed and re-running is correct -- while a response-phase fault
            # runs at step 9, *after* the commit, so skipping the record
            # discards the only trace of it and a retry with the same key
            # charges the caller twice. That is the single guarantee an
            # idempotency key carries, modelled backwards, on precisely the
            # fault (``connection_reset``) whose whole purpose is to rehearse a
            # retry across a dropped connection.
            #
            # Recording the pre-fault ``res`` gets both: no ``TransportDirective``
            # and no ``vendorfake-fault``/``vendorfake-rule`` header can reach
            # ``IdempotencyRecord``, because the fault has not been applied yet,
            # and the retry answers with the payment the handler really made.
            # provenance: judgment -- no vendor documents what its idempotency
            # store does when the response never reaches the caller; committing
            # first and replaying the commit is what a real store's ordering
            # gives you.
            #
            # A third shape was wrong too, and it is why this block sits under
            # an ``else`` and the fault moved to step 9. Step 7 used to
            # *return* the replay, which skipped the fault the decision at
            # step 3 had already armed and already counted: the rule's
            # ``when.times`` budget was spent on a request the caller saw no
            # fault on, and the request log claimed a ``connection_reset`` for
            # a clean 200. A vendor's network does not know a request is a
            # retry, so a response-phase fault applies to a replayed answer
            # exactly as it does to a fresh one -- the "second dropped
            # connection" a robust client has to survive. ``state.fires`` then
            # counts only faults the caller observed, and the log's ``fault``
            # matches the response. The stored record is still untouched by
            # any fault, because a replay does not store.
            # provenance: judgment -- no vendor documents whether its edge
            # would corrupt a replayed answer; honouring the armed decision on
            # whatever the pipeline produces is the only rule that keeps the
            # budget and the log honest.
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
        # A response-scope fault ("the vendor returned garbage") corrupts a
        # REAL answer, so it can only run after the handler produced one --
        # unlike every fault above, which fires instead of the handler ever
        # running. It never touches ``ctx``, so it cannot journal anything on
        # its own; DELAYS LEAVE AS DATA applies here too, via
        # ``UnitResponse.transport``.
        #
        # "Whichever answer" is the point: a replay from step 7 is faulted the
        # same as a fresh one, so the decision drawn at step 3 is paid out on
        # every answer the vendor produced -- including on the paths that never
        # reach here, where ``handle``'s ``except UnitError`` block applies it
        # to the shaped error instead. A framework crash is not a vendor
        # answer: the ``except Exception`` block's 500 is never faulted. The
        # budget, drawn at step 3, is spent either way -- including when the
        # payout itself fails on bad params and the caller gets the 400
        # diagnostic rather than the fault. See the step 8 comment for why.
        if decision is not None and decision.fault in RESPONSE_PHASE_FAULTS:
            trace.response_fault_attempted = True
            res = apply_response_fault(decision, res, log=self._log)
        return res

    # -- pipeline helpers ---------------------------------------------------

    def _in_band(self, req: UnitRequest, args: HandlerArgs) -> MagicExtraction:
        """Scan the request for a magic value, tolerating an unreadable body.

        Reads the content-type-general ``body()`` rather than the reference's
        JSON-only ``safeJson``, which left every declared body path unreachable
        on a form-encoded request. Neither of the shipped magic paths is an
        OAuth field, so nothing observable changes today; it is recorded as
        ``provenance: judgment`` because keeping a second, JSON-only body
        reader would re-create exactly the drift this build exists to remove.

        A body that will not parse is not an error *here*: the request may not
        have a body at all, and the handler is entitled to produce the real
        error a moment later.
        """
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
        """Return the stored response, or refuse a reused key with a new body.

        ``x-unit-idempotent-replay`` is always stamped, and
        ``x-unit-idempotent-ignored-body`` additionally when the body differed
        and the route asked for ``replay`` rather than ``conflict``. The second
        header exists because "you got a 200 and your update was silently
        discarded" is real documented vendor behaviour that a consumer has no
        other way to observe.
        """
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
        """The vendor's error body, plus the machine-readable ``x-unit-error``.

        The header is what lets a conformance check assert "this failed, and it
        failed for *this* reason" across vendors whose bodies share no field.

        ``delay_ms`` comes from :attr:`UnitError.delay_ms` and is the only way a
        refusal can ask its binding to hold the answer back -- today, the
        ``timeout`` fault on a real clock. It is a keyword with a default so
        every other call site here reads exactly as it did.

        ``fault``/``rule_id`` come from the same error, when a chaos decision
        raised it, and become the ``vendorfake-fault``/``vendorfake-rule``
        headers -- the same two stamped on a successful response a
        response-scope fault corrupted (``core/chaos/faults.py``), so a test
        can tell any faulted answer from a real one without parsing the body,
        whichever of the two mechanisms produced it. A ``rule_id`` with no
        ``fault`` is a rule-authoring refusal and becomes
        ``vendorfake-rule-error`` instead: the rule is named, and the absence
        of ``vendorfake-fault`` stays truthful.
        """
        headers = dict(shaped.headers)
        headers["x-unit-error"] = kind.value
        if fault is not None and rule_id is not None:
            headers["vendorfake-fault"] = fault
            headers["vendorfake-rule"] = header_text(rule_id)
        elif rule_id is not None:
            # A rule was involved but no fault fired: the payout refused its
            # own params (a ``body_mutation`` pointer that is not in *this*
            # answer, say). Without this a consumer that reads status codes
            # and not bodies sees an unexplained 400 with none of the fault
            # headers, and reads it as the vendor failing
            # (konyklabs/roadmap#101, item 19).
            headers["vendorfake-rule-error"] = header_text(rule_id)
        if delay_asked_ms is not None:
            # The delay the ``timeout`` rule ASKED for, on either clock --
            # distinct from ``delay_ms`` below, which is what the binding
            # still owes and is zero on a virtual clock because scenario time
            # already moved. The in-process transport compares this one
            # against the client's read timeout, so the same rule raises
            # ``ReadTimeout`` on both clocks instead of a 504 on one of them
            # (konyklabs/roadmap#101, item 18).
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
        mutable = MutableResponse(status=res.status, headers=dict(res.headers), body=res.body)
        mutable.headers[REQUEST_ID_HEADER] = req.id
        if route is not None and not route.internal:
            self._vendor.decorate(mutable, self._ctx, req)
        elapsed_ms = (time.monotonic() - started) * 1000
        self._log.debug(
            "request",
            {
                "method": req.method,
                "path": req.path,
                "status": mutable.status,
                "route": route.key if route is not None else None,
                "ms": round(elapsed_ms, 3),
            },
        )
        # Recorded here, on the one path every answer leaves through -- the
        # success path, every shaped error and the catch-all 500 alike. A
        # recording step in `handle` would have to be repeated four times and
        # would be forgotten on the fifth.
        #
        # Excluded by path, not only by matched route: an unmatched control-
        # plane request (a mistyped control path, a wrong verb on a real
        # control route, or the bare `/__unit` with no trailing slash) is
        # still the observer's own traffic, and must stay absent from the log
        # by construction rather than merely when it happens to resolve to an
        # internal route. `is_control_path` is the one place that namespace is
        # defined, shared with `Router.add`'s reservation check.
        if (route is None or not route.internal) and not is_control_path(req.path):
            # Recorded on the one path every answer leaves through, from the
            # two reads step 8 took around the handler. "Committed" is a seq
            # that moved; "discarded" is a commit the caller was not handed
            # cleanly -- the payout was attempted, and it either corrupted the
            # answer or refused with the 400 naming the rule. A fault in
            # ``INTACT_RESPONSE_FAULTS`` did neither: the caller has the
            # handler's answer, only later. All of this is what a real
            # gateway mangling a response after the write does, which is what
            # a response-phase fault rehearses, and none of it was readable
            # anywhere but the journal.
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
                    status=mutable.status,
                    matched=route is not None,
                    fault=trace.fault,
                    rule_id=trace.rule_id,
                    duration_ms=round(elapsed_ms),
                    near_misses=trace.near_misses,
                    committed_journal_seq=trace.journal_seq_after if committed else None,
                    discarded_mutation=committed and deprived,
                )
            )
        # ``delay_ms`` and ``transport`` are carried across rather than offered
        # to ``decorate``. ``MutableResponse`` is the vendor's last chance to
        # shape what goes on the *wire*; how long a binding holds the answer
        # back, and what it does to the connection while doing it, are not
        # vendor opinions, and putting either in reach of one would make the
        # same fault behave differently per vendor for no stated reason.
        return UnitResponse(
            status=mutable.status,
            headers=mutable.headers,
            body=mutable.body,
            delay_ms=res.delay_ms,
            transport=res.transport,
        )


def _delay_asked_ms(err: UnitError) -> str | None:
    """The ``timeout`` fault's requested delay, for :data:`DELAY_ASKED_HEADER`;
    ``None`` for every other error. Read from ``info`` -- the consumer-visible
    copy ``core/chaos/faults.py`` fills on either clock -- rather than from
    ``delay_ms``, which is already zero by the time a virtual clock answers."""
    if err.fault != "timeout" or err.info is None:
        return None
    asked = err.info.get("delay_ms")
    return None if asked is None else str(asked)


def _describe(exc: BaseException) -> str:
    text = str(exc)
    return text if text else exc.__class__.__name__
