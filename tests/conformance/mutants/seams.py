"""The three seams a mutant may enter through, and nothing else.

FOR: breaking exactly one contract at a time, through wiring that a *real*
defect could travel through, so that a red check is evidence about the
production seam rather than about a patched module.

INVARIANT: **no mutant uses ``monkeypatch``.** Every mutation below is a
substitution the production composition root already accepts:

``VendorOverlay``
    A :class:`~vendorfake.core.kernel.types.VendorDefinition` that delegates
    every member to a real vendor and overrides the ones a mutant names. This
    is the seam a *vendor author* makes mistakes in, and it is the one the
    fork contract is written against.

``replace_control_route``
    The control plane is handed to :class:`~vendorfake.core.kernel.unit.Unit`
    as a factory (``control_routes=``), and a :class:`Route` is a frozen
    dataclass whose ``handler`` is data. Replacing one handler is therefore a
    construction-time substitution, not a patch. It is the only seam that can
    express a defect in something a check observes *solely* through the control
    plane -- the transition predicate, the journal, the echo route -- because
    the whole design point of this suite is that a check never reaches past it.

``ClientOverlay``
    A :class:`~vendorfake.conformance.client.ConformanceClient` wrapping
    another. This is the transport seam: it models a binding that re-encodes,
    truncates or answers for itself. A check cannot tell a wrapped client from
    an unwrapped one, which is exactly the property C10 and C15 exist to test.

Plus four collaborator substitutions that ride in through constructor
parameters the kernel already exposes: :class:`LeakyFaultSelector` (through
``Unit(fault_selector=...)``), :class:`UngatedWebhookDispatcher` and
:class:`ImpatientWebhookDispatcher` (through ``Unit(dispatcher=...)``),
:class:`AuthAdapterOverlay` (through ``VendorOverlay(auth=...)``) and
:class:`PermissiveStateMachine` (through a replaced probe route, which is where
the control plane constructs one).

The two dispatcher subclasses were added with the contracts that catch them.
Both defects -- a delivery gate that consults no capability, and a retry that
is submitted instead of scheduled -- lived in the core with no seam to reach
them, which is why they could be deleted outright and leave the suite green.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from vendorfake.conformance.client import MISSING, ConformanceClient, ConformanceResponse, FormPairs, QueryPairs
from vendorfake.core.capability.gates import CoreCapability
from vendorfake.core.capability.registry import CapabilityRegistry
from vendorfake.core.chaos.engine import ChaosEngine, ChaosSubject
from vendorfake.core.chaos.selector import FaultSelection, FaultSelector
from vendorfake.core.config.models import ProfileDocument
from vendorfake.core.kernel.magic import MagicExtraction
from vendorfake.core.kernel.types import (
    AuthAdapter,
    AuthCredential,
    AuthResult,
    CapabilityDecl,
    ErrorShaper,
    EventMapper,
    Handler,
    HandlerArgs,
    JournalEntry,
    MagicTriggerSpec,
    MutableResponse,
    ReplyInit,
    Route,
    ShapedError,
    Signer,
    SignerProperties,
    SignInput,
    UnitContext,
    UnitError,
    UnitErrorKind,
    UnitRequest,
    UnitResponse,
    VendorDefinition,
)
from vendorfake.core.state.machine import MachineDef, StateMachine
from vendorfake.core.webhooks.dispatcher import WebhookDispatcher
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION, DeliveryMetadata

__all__ = [
    "AuthAdapterOverlay",
    "ClientOverlay",
    "ErrorShaperOverlay",
    "ImpatientWebhookDispatcher",
    "LeakyFaultSelector",
    "PermissiveStateMachine",
    "SignerOverlay",
    "UngatedWebhookDispatcher",
    "VendorOverlay",
    "carries_magic",
    "replace_control_route",
    "rewrite_document",
    "wrap_vendor_handlers",
]


# ---------------------------------------------------------------------------
# Seam 1: the vendor definition.
# ---------------------------------------------------------------------------


class VendorOverlay:
    """A vendor that is another vendor, except where a mutant says otherwise.

    Every member is delegated explicitly rather than through ``__getattr__``.
    A delegating ``__getattr__`` would also forward a member the protocol
    grows later, which would make a mutant silently keep working against a
    contract it was never checked against -- the exact failure this whole
    exercise exists to prevent.
    """

    def __init__(
        self,
        inner: VendorDefinition,
        *,
        routes: Callable[[Sequence[Route]], Sequence[Route]] | None = None,
        capabilities: Callable[[Sequence[CapabilityDecl]], Sequence[CapabilityDecl]] | None = None,
        machines: Mapping[str, MachineDef] | None = None,
        signer: Signer | None = None,
        errors: ErrorShaper | None = None,
        auth: AuthAdapter | None = None,
        hydrate: Callable[[VendorDefinition, UnitContext, object], None] | None = None,
    ) -> None:
        self._inner = inner
        self._auth = auth
        self._routes = tuple(inner.routes) if routes is None else tuple(routes(inner.routes))
        self._capabilities = (
            tuple(inner.capabilities) if capabilities is None else tuple(capabilities(inner.capabilities))
        )
        self._machines = dict(inner.machines) if machines is None else dict(machines)
        self._signer = inner.signer if signer is None else signer
        self._errors = inner.errors if errors is None else errors
        self._hydrate = hydrate

    # -- identity ----------------------------------------------------------

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def display_name(self) -> str:
        return self._inner.display_name

    @property
    def api_version(self) -> str | None:
        return self._inner.api_version

    # -- surface -----------------------------------------------------------

    @property
    def capabilities(self) -> Sequence[CapabilityDecl]:
        return self._capabilities

    @property
    def routes(self) -> Sequence[Route]:
        return self._routes

    @property
    def errors(self) -> ErrorShaper:
        return self._errors

    @property
    def auth(self) -> AuthAdapter:
        return self._inner.auth if self._auth is None else self._auth

    @property
    def signer(self) -> Signer | None:
        return self._signer

    @property
    def events(self) -> EventMapper | None:
        return self._inner.events

    @property
    def magic(self) -> MagicTriggerSpec | None:
        return self._inner.magic

    @property
    def machines(self) -> Mapping[str, MachineDef]:
        return self._machines

    @property
    def retry_defaults(self) -> ProfileDocument:
        return self._inner.retry_defaults

    @property
    def profile_dir(self) -> Path:
        return self._inner.profile_dir

    @property
    def base_dir(self) -> Path:
        return self._inner.base_dir

    @property
    def not_supported(self) -> Mapping[str, str]:
        return self._inner.not_supported

    @property
    def volatile_fields(self) -> Sequence[str]:
        return self._inner.volatile_fields

    # -- lifecycle ---------------------------------------------------------

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        if self._hydrate is None:
            self._inner.hydrate(ctx, seed)
            return
        self._hydrate(self._inner, ctx, seed)

    def decorate(self, res: MutableResponse, ctx: UnitContext, req: UnitRequest) -> None:
        self._inner.decorate(res, ctx, req)


class ErrorShaperOverlay:
    """A shaper that is another shaper, except for the kinds a mutant names.

    Three of the mutants are error-table defects, which is the single most
    likely place a *new vendor* gets it wrong: the table is the one part of the
    fork contract that is pure data, so a hole in it looks like a typo rather
    than like a bug.
    """

    def __init__(
        self,
        inner: ErrorShaper,
        *,
        overrides: Mapping[UnitErrorKind, ShapedError] | None = None,
        not_found: ShapedError | None = None,
    ) -> None:
        self._inner = inner
        self._overrides = dict(overrides or {})
        self._not_found = not_found

    def shape(self, err: UnitError, ctx: UnitContext) -> ShapedError:
        override = self._overrides.get(err.kind)
        return self._inner.shape(err, ctx) if override is None else override

    def not_found(self, req: UnitRequest, ctx: UnitContext) -> ShapedError:
        return self._inner.not_found(req, ctx) if self._not_found is None else self._not_found


class AuthAdapterOverlay:
    """An auth adapter that is another adapter, except where a mutant says so.

    The seam a *vendor author* gets wrong. The kernel calls ``resolve`` at step
    5 of its pipeline and checks ``Route.scopes`` against what comes back; an
    adapter that returns a principal for anything it is handed, or one that
    returns every scope regardless of the credential, turns an authenticated
    surface into a public one without changing a single route declaration.

    ``credentials`` is delegated by default, so a mutant aimed at ``resolve``
    still publishes the credentials a check needs in order to *reach* it.
    """

    def __init__(
        self,
        inner: AuthAdapter,
        *,
        resolve: Callable[[AuthAdapter, HandlerArgs, str], AuthResult] | None = None,
    ) -> None:
        self._inner = inner
        self._resolve = resolve

    def describe(self) -> Mapping[str, str]:
        return self._inner.describe()

    def resolve(self, args: HandlerArgs, mode: str) -> AuthResult:
        if self._resolve is None:
            return self._inner.resolve(args, mode)
        return self._resolve(self._inner, args, mode)

    def credentials(self, ctx: UnitContext) -> Sequence[AuthCredential]:
        return self._inner.credentials(ctx)


class UngatedWebhookDispatcher(WebhookDispatcher):
    """A dispatcher whose journal listener consults no capability.

    The defect the ``webhooks`` gate exists to prevent, reproduced through the
    constructor seam the kernel already exposes. The gate is two lines inside
    :meth:`WebhookDispatcher.attach`; deleting them left the entire suite green
    because the only two contracts that touched delivery both declared
    ``requires=webhooks``, so they skipped on exactly the profiles where an
    ungated dispatcher is visible.

    Everything else ``attach`` refuses -- a vendor with no mapper or no signer,
    a mutation of the subscription collection, a seed entry, a mapping that
    raised -- is kept, so this mutant is precisely "the capability is not
    consulted" and not "the listener is broken".
    """

    def attach(self) -> None:
        def listener(entry: JournalEntry) -> None:
            if not self.enabled:
                return
            ctx = self._get_context()
            if ctx.vendor.events is None or ctx.vendor.signer is None:
                return
            if entry.collection == SUBSCRIPTION_COLLECTION:
                return
            if entry.meta is not None and entry.meta.get("seed") is True:
                return
            try:
                events = self._prepare(entry, ctx)
            except Exception:  # pragma: no cover - the real listener logs this
                return
            for event in events:
                self.enqueue(event)

        self._store.on_journal(listener)


class ImpatientWebhookDispatcher(WebhookDispatcher):
    """A dispatcher that retries at once instead of after the declared interval.

    The schedule is still read, still published and still exhausted after the
    declared number of retries -- only the *delay* is dropped, by submitting
    the attempt to the worker rather than putting it on the clock. That is the
    narrowest possible expression of "the documented retry schedule is
    decoration", and it is the shape a real defect takes: ``_schedule`` and
    ``_worker.submit`` are one line apart in
    ``core/webhooks/dispatcher.py::_run_attempt``.

    A contract that asserted only ``len(attempts) >= 2`` cannot see this at
    all, which is what made it worth writing one that can.
    """

    def _schedule(self, attempt: Any, delay_ms: float, label: str) -> None:
        self._worker.submit(partial(self._run_attempt, attempt))


class SignerOverlay:
    """A signer that is another signer, except where a mutant says otherwise.

    Split out from :class:`VendorOverlay` because three of the mutants are
    about the signing scheme alone, and because ``sign`` and ``headers`` are
    one protocol on purpose -- a mutant replacing one and inheriting the other
    is exactly the asymmetry that protocol exists to make visible.
    """

    def __init__(
        self,
        inner: Signer,
        *,
        sign: Callable[[Signer, SignInput], Mapping[str, str]] | None = None,
        headers: Callable[[Signer, DeliveryMetadata], Mapping[str, str]] | None = None,
        properties: SignerProperties | None = None,
    ) -> None:
        self._inner = inner
        self._sign = sign
        self._headers = headers
        self._properties = properties

    @property
    def properties(self) -> SignerProperties:
        return self._inner.properties if self._properties is None else self._properties

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        if self._sign is None:
            return self._inner.sign(payload)
        return self._sign(self._inner, payload)

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        if self._headers is None:
            return self._inner.headers(meta)
        return self._headers(self._inner, meta)

    def describe(self) -> Mapping[str, str]:
        return self._inner.describe()


def wrap_vendor_handlers(wrap: Callable[[Handler], Handler]) -> Callable[[Sequence[Route]], Sequence[Route]]:
    """Put ``wrap`` around every vendor route's handler.

    Every route and not one chosen route, because the two defects that use
    this -- an ungated in-band trigger, a handler drawing from outside the
    scenario -- are properties of the request pipeline rather than of one
    endpoint, and a mutant that only broke the endpoint a check happens to
    pick would be reverse-engineered from the check.
    """

    def apply(routes: Sequence[Route]) -> Sequence[Route]:
        return tuple(dataclasses.replace(route, handler=wrap(route.handler)) for route in routes)

    return apply


# ---------------------------------------------------------------------------
# Seam 2: the control plane.
# ---------------------------------------------------------------------------


def replace_control_route(
    method: str,
    path: str,
    wrap: Callable[[Handler], Handler],
) -> Callable[[Sequence[Route]], Sequence[Route]]:
    """Wrap the handler of exactly one control route, or fail saying so.

    The "or fail saying so" is the load-bearing half. A mutant aimed at a
    route that has been renamed would otherwise apply to nothing, the check it
    targets would pass, and the meta-test would report a mutant the suite
    cannot catch -- which reads identically to a check that does not work.
    """

    def apply(routes: Sequence[Route]) -> Sequence[Route]:
        out: list[Route] = []
        found = 0
        for route in routes:
            if route.method.upper() == method.upper() and route.path == path:
                out.append(dataclasses.replace(route, handler=wrap(route.handler)))
                found += 1
            else:
                out.append(route)
        if found != 1:
            raise AssertionError(
                f"mutant aimed at control route {method} {path}, which matched {found} routes. "
                f"The control plane in core/control/plane.py has changed; update the mutant in "
                f"tests/conformance/mutants/catalog.py rather than deleting it."
            )
        return tuple(out)

    return apply


def rewrite_document(
    transform: Callable[[dict[str, Any]], dict[str, Any]],
) -> Callable[[Handler], Handler]:
    """Let a mutant rewrite the JSON document one control route answers with.

    Only the document: status, headers and the raw/text branches pass through
    untouched, so a mutant cannot accidentally also change how the answer is
    framed.
    """

    def wrap(handler: Handler) -> Handler:
        def wrapped(args: HandlerArgs) -> ReplyInit | UnitResponse:
            reply = handler(args)
            if not isinstance(reply, ReplyInit) or reply.json is None:
                raise AssertionError(
                    f"rewrite_document expected a ReplyInit carrying a JSON document, got {type(reply).__name__}"
                )
            return dataclasses.replace(reply, json=transform(dict(reply.json)))

        return wrapped

    return wrap


# ---------------------------------------------------------------------------
# Seam 3: the transport.
# ---------------------------------------------------------------------------


class ClientOverlay:
    """A conformance client wrapping another, altering what crosses the wire.

    ``on_request`` sees the call before it is made and may rewrite it;
    ``on_response`` sees the answer and may rewrite that. A defect in a
    transport adapter is observationally exactly this, and a check has no way
    to tell the difference -- which is the point.
    """

    def __init__(
        self,
        inner: ConformanceClient,
        *,
        on_request: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
        on_response: Callable[[str, str, ConformanceResponse], ConformanceResponse] | None = None,
    ) -> None:
        self._inner = inner
        self._on_request = on_request
        self._on_response = on_response

    def call(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = MISSING,
        form: FormPairs | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        query: QueryPairs | None = None,
    ) -> ConformanceResponse:
        call: dict[str, Any] = {
            "json_body": json_body,
            "form": form,
            "body": body,
            "headers": headers,
            "query": query,
        }
        if self._on_request is not None:
            call = self._on_request(method, path, call)
        answered = self._inner.call(method, path, **call)
        if self._on_response is not None:
            answered = self._on_response(method, path, answered)
        return answered


# ---------------------------------------------------------------------------
# Two collaborators, substituted where the kernel already accepts one.
# ---------------------------------------------------------------------------


class LeakyFaultSelector(FaultSelector):
    """A selector that evaluates the standing rules *before* the in-band path.

    The defect it reproduces is the one ``core/chaos/selector.py`` says is
    unrepresentable there: an in-band (magic-value) fire that advances a
    standing rule's counters, so "the second create fails" silently becomes
    "the first" for every consumer who also uses the in-band trigger.

    PROVENANCE: hypothetical. The TypeScript reference bypasses ``evaluate``
    entirely on the magic path and has no such leak; the losing bake-off entry
    got the one-shot semantics right and the capability gate wrong. This is the
    mistake the *fix* invites -- reordering two lines -- which is why C12 is
    written to catch it and why the seam has to exist for that claim to be
    testable at all.
    """

    def select_request(
        self,
        subject: ChaosSubject,
        in_band: Callable[[], MagicExtraction] | None = None,
    ) -> FaultSelection:
        if (
            subject.scope == "request"
            and self._capabilities.is_enabled(CoreCapability.CHAOS.value)
            and in_band is not None
            and in_band().armed
        ):
            # The leak, and only on the armed path -- a leak on every request
            # would be a different (and far louder) bug. The decision is
            # discarded, so the *response* is still the overlay's, which is
            # what makes the leak invisible without C12's counter comparison.
            self._engine.evaluate(subject)
        return super().select_request(subject, in_band)


class PermissiveStateMachine(StateMachine):
    """``can_transition`` short-circuits on identity, as the reference does.

    PROVENANCE: verbatim from the TypeScript reference,
    ``packages/core/src/state/machine.ts``::

        canTransition(from, to) { if (from === to) return true; ... }

    Its observable consequence, confirmed by probe against that codebase:
    paying an order already in COMPLETED returns 200, replaces the tenders and
    bumps the version again -- a double payment the lifecycle existed to
    prevent.
    """

    def can_transition(self, from_state: str, to_state: str) -> bool:
        if from_state == to_state:
            return True
        return super().can_transition(from_state, to_state)


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------


def carries_magic(args: HandlerArgs) -> bool:
    """Whether this request carries the vendor's in-band trigger prefix.

    Query values and header values only, and deliberately without consulting
    any capability: two mutants need "the request looks like a chaos request"
    computed the wrong way round, which is the shape of the real defect.
    """
    spec = args.ctx.vendor.magic
    if spec is None:
        return False
    values = (*args.req.query.values(), *args.req.headers.values())
    return any(value.startswith(spec.prefix) for value in values)


def shaped(status: int, body: object) -> ShapedError:
    return ShapedError(status=status, body=body)


def unit_error(kind: UnitErrorKind, detail: str) -> UnitError:
    return UnitError(kind, detail=detail)


def subject_of(args: HandlerArgs) -> ChaosSubject:
    """A request-scope chaos subject for the route now running."""
    return ChaosSubject(
        scope="request",
        route_key=args.route.key if args.route is not None else None,
        method=args.req.method,
        path=args.req.path,
    )


def make_selector(engine: ChaosEngine, capabilities: CapabilityRegistry) -> FaultSelector:
    """The production selector, spelled as the factory the seam expects."""
    return FaultSelector(engine, capabilities)
