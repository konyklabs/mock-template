"""The unit contract: everything a vendor module implements against.

FOR: stating what a request, a response, a route, an error, an event and a
vendor are, once, so that the core can be written against the statement rather
than against any particular vendor or any particular transport.

INVARIANT: **this is deliberately not an HTTP contract.** A unit consumes a
``UnitRequest`` and produces a ``UnitResponse``, and ``transport`` names
whichever binding produced it. HTTP is one binding; in-process and file-drop
bindings feed the same ``Unit.handle``, which is what keeps "the core does not
assume HTTP" a mechanical fact rather than an aspiration.

Two fields carry most of that weight:

``UnitRequest.raw_body`` is the exact received bytes
    Never a re-serialisation. Webhook signature schemes sign raw bytes, and a
    binding that parsed and re-encoded the body would silently change what is
    under test while every assertion still passed.

``UnitResponse.body`` is already serialised
    A transport adapter returns those bytes untouched. It does not get a model
    to render, because rendering it again is the same defect from the other
    end.

Path templates are ``{order_id}``, never ``:order_id``, in every one of the
four places a template is consumed: the router, the chaos ``match.route`` key,
the capability-to-routes index, and the generated OpenAPI document. The
reference already paid a translation between the two forms
(``tools/spec-freshness.mjs``, ``route.path.replace(/:([A-Za-z0-9_]+)/g,
'{$1}')``) because OpenAPI uses braces; making braces canonical deletes it and
makes the four agree structurally instead of by convention.

The core is synchronous. ``Handler`` returns a value, not an awaitable. Three
reasons, in the order they bind: the webhook-timing guarantee needs a
synchronous prologue in either model, so async only makes forgetting it
silent; ``Clock.advance()`` must re-scan after every firing and is a plain loop
when synchronous; and an async pipeline with await points would interleave
concurrent requests and make "two runs of the same scenario produce the same
ids" false, which the reference got for free from Node's single thread.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import parse_qsl

from vendorfake.core.rand.rng import Rng
from vendorfake.core.time.clock import Clock

if TYPE_CHECKING:  # pragma: no cover - typing only
    # These are the subsystems ``UnitContext`` exposes. The import is
    # guarded because each of those modules imports this one at runtime;
    # a runtime import back would be a cycle. That guard IS the mechanism
    # that keeps the kernel free of its own subsystems, so nothing here
    # ever becomes an unguarded import.
    from vendorfake.core.capability.registry import CapabilityRegistry
    from vendorfake.core.chaos.engine import ChaosEngine
    from vendorfake.core.config.models import ProfileDocument, ResolvedConfig
    from vendorfake.core.state.machine import MachineDef
    from vendorfake.core.state.store import Store
    from vendorfake.core.webhooks.dispatcher import WebhookDispatcher
    from vendorfake.core.webhooks.models import DeliveryMetadata

__all__ = [
    "AuthAdapter",
    "AuthCredential",
    "AuthMode",
    "AuthResult",
    "CapabilityDecl",
    "ErrorShaper",
    "EventMapper",
    "EventMeta",
    "FormData",
    "Handler",
    "HandlerArgs",
    "IdempotencySpec",
    "JournalEntry",
    "Logger",
    "MagicTriggerSpec",
    "MappedEvent",
    "MutableResponse",
    "PreparedEvent",
    "ReplyInit",
    "Route",
    "ShapedError",
    "SignInput",
    "Signer",
    "SignerProperties",
    "TransportKind",
    "UnitContext",
    "UnitError",
    "UnitErrorKind",
    "UnitRequest",
    "UnitResponse",
    "VendorDefinition",
]

# ---------------------------------------------------------------------------
# Open string vocabularies.
#
# The reference writes these as `'http' | 'inprocess' | (string & {})`, a
# TypeScript trick for "any string, but suggest these". Python has no such
# thing and does not need one: the alias documents the intent and the named
# constants are the suggestions.
# ---------------------------------------------------------------------------

TransportKind = str
"""Names the binding that produced a request: ``http``, ``inprocess``, ``filedrop``, ..."""

AuthMode = str
"""Passed verbatim to the vendor auth adapter; the core never interprets it."""


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------


class UnitErrorKind(StrEnum):
    """The twenty core-generic failure kinds. Exactly twenty; no more are added
    without a conformance check moving with them.

    The kernel and every core subsystem raise only these, and the vendor's
    ``ErrorShaper`` turns them into that vendor's wire format. That split is
    why a new vendor's entire error story is one lookup table, and why
    conformance can assert that all twenty map to a 4xx or 5xx with a non-empty
    body without knowing anything about the vendor.
    """

    BAD_REQUEST = "bad_request"
    INVALID_JSON = "invalid_json"
    MISSING_FIELD = "missing_field"
    INVALID_VALUE = "invalid_value"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    UNAUTHORIZED = "unauthorized"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_REVOKED = "token_revoked"
    FORBIDDEN_SCOPE = "forbidden_scope"
    CAPABILITY_DISABLED = "capability_disabled"
    VERSION_CONFLICT = "version_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INVALID_CURSOR = "invalid_cursor"
    INVALID_TRANSITION = "invalid_transition"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


class UnitError(Exception):
    """A core-generic failure, on its way to a vendor's wire format.

    ``kind`` is coerced through :class:`UnitErrorKind`, so a misspelled kind
    raises at the raise site instead of travelling to the shaper and falling
    out of a lookup table as an unrelated status. That coercion is what
    replaces the TypeScript compiler's checking of a literal union.
    """

    __slots__ = ("detail", "field", "info", "kind")

    def __init__(
        self,
        kind: UnitErrorKind | str,
        *,
        detail: str | None = None,
        field: str | None = None,
        info: Mapping[str, Any] | None = None,
    ) -> None:
        resolved = UnitErrorKind(kind)
        super().__init__(detail if detail is not None else resolved.value)
        self.kind: UnitErrorKind = resolved
        self.detail: str | None = detail
        #: Request field the error is about, in the vendor's dot notation.
        self.field: str | None = field
        #: Machine-readable context surfaced under ``x-unit-error`` / the sidecar.
        self.info: Mapping[str, Any] | None = info


@dataclass(frozen=True, slots=True)
class ShapedError:
    """A ``UnitError`` after the vendor has said what it looks like on the wire."""

    status: int
    body: object
    headers: Mapping[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Requests and responses: the seam.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnitRequest:
    """A request as the kernel sees it, whatever transport delivered it."""

    #: Unique per received request; echoed on the response for correlation.
    id: str
    #: Verb. HTTP methods for the HTTP binding; other bindings supply their own
    #: (the file-drop binding reads the verb out of the request document).
    method: str
    #: Logical resource path, always starting with ``/``.
    path: str
    #: The scalar view: repeated keys collapse to the last value, matching
    #: every binding. Every value is kept in ``query_all``.
    query: Mapping[str, str]
    #: Header names are lowercased by every binding before the kernel sees them.
    headers: Mapping[str, str]
    #: Exact received bytes. Kept as bytes -- never a re-serialised object --
    #: because webhook signature schemes sign the raw body and a
    #: re-serialisation would silently change the bytes under test.
    raw_body: bytes
    transport: TransportKind
    #: RFC 3339 with milliseconds.
    received_at: str
    #: Every value sent for each key, in arrival order. Invariant: the two
    #: views have the same keys and ``query[k] == query_all[k][-1]`` for every
    #: ``k``. Defaults to the single-valued view of ``query`` so a hand-built
    #: request keeps it; supplied explicitly, it is checked.
    query_all: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.query_all:
            object.__setattr__(self, "query_all", {k: (v,) for k, v in self.query.items()})
            return
        for key in set(self.query) | set(self.query_all):
            values = self.query_all.get(key)
            if not values or self.query.get(key) != values[-1]:
                raise ValueError(
                    f"UnitRequest.query[{key!r}] is {self.query.get(key)!r} but query_all[{key!r}] is "
                    f"{values!r}; query must be the last value of query_all for every key"
                )


@dataclass(frozen=True, slots=True)
class UnitResponse:
    """Already-serialised. A transport adapter returns ``body`` untouched."""

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(slots=True)
class MutableResponse:
    """The response while ``finish()`` and the vendor's ``decorate`` still hold it."""

    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class ReplyInit:
    """What a handler may return; the kernel normalises it into a ``UnitResponse``.

    Precedence is contract, and it is asserted: ``raw`` wins, then ``text``,
    then JSON. ``text`` is chosen on ``is not None`` and not on truthiness,
    because ``redirect()`` and ``no_content()`` both return an *empty* text --
    a zero-byte body with no ``content-type`` -- and a truthiness test would
    send both of them down the JSON branch and answer a 302 with
    ``{}`` and ``content-type: application/json``.

    ``json=None`` serialises to ``b"{}"``, never ``b"null"``: the reference
    writes ``JSON.stringify(r.json ?? {})`` and both of TypeScript's empty
    values land on the same two bytes.
    """

    status: int | None = None
    headers: Mapping[str, str] | None = None
    json: Any = None
    text: str | None = None
    raw: bytes | None = None


# ---------------------------------------------------------------------------
# Capabilities, auth, routes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityDecl:
    """One capability a vendor declares.

    ``surface`` capabilities own routes and answer a disabled call explicitly.
    ``behavior`` capabilities gate conduct with no surface of their own -- fault
    injection, for instance -- and conformance checks the two differently.
    """

    #: Dotted name. A child (``webhooks.chaos``) is usable only while its parent
    #: (``webhooks``) is enabled; the registry enforces that.
    name: str
    summary: str
    #: Capabilities that must also be enabled for this one to function.
    requires: Sequence[str] = ()
    kind: Literal["surface", "behavior"] = "surface"


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Who the presented credential resolves to, as the vendor sees it."""

    #: Vendor-side identity the token resolves to (a merchant, a tenant, ...).
    principal_id: str
    scopes: Sequence[str]
    token_id: str | None = None
    meta: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AuthCredential:
    """A credential a caller can actually present to this unit, and what it grants.

    FOR: making authentication *drivable* from outside the process. A route
    table that says ``auth: "bearer"`` tells a consumer -- and a conformance
    check -- that a credential is required and nothing whatever about how to
    obtain one, which is why a suite can assert the whole error table for
    ``unauthorized`` while never once sending an authenticated request.

    Published at ``GET /__unit/auth``, so the answer crosses the wire and a
    consumer in another language gets it too. This is a *fake*: the credentials
    it holds are scenario data, not secrets, and refusing to publish them would
    only mean every consumer hardcoding the seed document instead.

    ``headers`` is the whole instruction -- header name to header value -- so
    that a vendor whose scheme is not ``Authorization: Bearer`` needs no new
    vocabulary here and no reader of this type has to know one.
    """

    #: Stable, human name for this credential within the unit. Not a secret id.
    label: str
    #: The ``Route.auth`` mode this credential satisfies.
    mode: AuthMode
    #: Exactly what to put on the request, header name -> header value.
    headers: Mapping[str, str]
    #: What presenting it grants, in the same vocabulary as ``Route.scopes``.
    scopes: Sequence[str] = ()
    #: One line: where it came from, and what it is for.
    summary: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "mode": self.mode,
            "headers": dict(self.headers),
            "scopes": list(self.scopes),
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class IdempotencySpec:
    """How a route deduplicates a retried request."""

    #: Dot path into the parsed body, e.g. ``idempotency_key``.
    key_path: str
    #: Namespace, so the same key on two operations does not collide.
    scope: str
    #: When true, a missing key is an error rather than "not idempotent".
    required: bool = False
    #: What a reused key with a DIFFERENT body does. ``conflict`` is the usual
    #: REST contract; ``replay`` returns the stored response and drops the new
    #: request on the floor, which some vendors really do -- Square's
    #: UpdateOrder is documented that way ("you get a 200 response but the
    #: returned order doesn't reflect any of your updates").
    on_mismatch: Literal["conflict", "replay"] = "conflict"


@dataclass(frozen=True, slots=True)
class Route:
    """One entry in a vendor's surface. Routes are data, not decorators.

    A vendor builds a ``tuple[Route, ...]`` whose handlers are bound methods of
    a surface object holding its dependencies. That is why a vendor module has
    nothing to import a web framework *for*.
    """

    method: str
    #: ``/v2/orders/{order_id}`` -- ``{name}`` segments become ``params``.
    path: str
    #: Every route belongs to exactly one capability. Asserted by conformance.
    capability: str
    handler: Handler
    #: Passed verbatim to the vendor auth adapter. ``None`` for unauthenticated.
    auth: AuthMode | None = None
    #: Scopes the token must carry; checked by the kernel, not by the vendor.
    scopes: Sequence[str] = ()
    idempotency: IdempotencySpec | None = None
    #: A body this route ACCEPTS, published at ``GET /__unit/routes``.
    #:
    #: The one thing a language-independent check cannot work out for itself. A
    #: contract about what a *committed mutation* does -- the journal, the
    #: version, an idempotent replay -- is unaskable until something has
    #: actually succeeded, and a probe body assembled by the check can only
    #: ever be refused by the vendor's own validation. So the vendor publishes
    #: one request that works, once, and every such contract aims itself at it.
    #:
    #: It is written against the scenario the profile loads, and that coupling
    #: is deliberate rather than hidden: an example that named no seeded entity
    #: could not be a body the route accepts.
    example_body: Mapping[str, Any] | None = None
    #: Stable identifier used by the spec-freshness inventory.
    operation_id: str | None = None
    summary: str | None = None
    #: Control-plane route: no auth, no chaos, no idempotency, never in the
    #: vendor surface report and never decorated.
    internal: bool = False
    #: Whether the pipeline takes the unit-wide request lock for this route.
    #:
    #: True for everything except the handful of routes whose handler blocks
    #: on machinery *another request must feed* -- draining the webhook queue,
    #: advancing a virtual clock, a vendor's "send a test event and tell me
    #: what happened". The reference gets away without the distinction because
    #: Node's event loop yields at every ``await``; a real lock does not, and
    #: such a route would hold the whole unit for the full delivery timeout
    #: against an unreachable subscriber. The store, the delivery log and the
    #: clock keep their own independent locks; the request lock exists only so
    #: that id minting and journal ordering are deterministic, which is exactly
    #: what those routes do not touch.
    serialized: bool = True

    @property
    def key(self) -> str:
        """``"POST /v2/orders/{order_id}/pay"`` -- how a chaos rule names a route."""
        return f"{self.method.upper()} {self.path}"


# ---------------------------------------------------------------------------
# The request body, content-type general.
# ---------------------------------------------------------------------------


class FormData(Mapping[str, str]):
    """A parsed ``application/x-www-form-urlencoded`` body.

    The scalar view is last-wins, which is the reference's behaviour:
    ``Object.fromEntries(new URLSearchParams(text))`` keeps only the final
    occurrence of a repeated key, and every vendor-side ``require_string``
    equivalent is written against a plain string. Returning a list for a
    singly-occurring key would make an ordinary ``client_id=x`` fail its own
    parser.

    The repeats are not thrown away, though -- ``get_all()`` exposes them. That
    is the difference between last-wins as a *contract* and last-wins as
    lossiness: a caller that genuinely wants every ``scopes=`` value asks for
    them explicitly, and a caller that does not is never surprised by a list.
    """

    __slots__ = ("_last", "_pairs")

    def __init__(self, pairs: Sequence[tuple[str, str]]) -> None:
        self._pairs: tuple[tuple[str, str], ...] = tuple(pairs)
        last: dict[str, str] = {}
        for key, value in self._pairs:
            last[key] = value
        self._last = last

    def __getitem__(self, key: str) -> str:
        return self._last[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._last)

    def __len__(self) -> int:
        return len(self._last)

    def __repr__(self) -> str:
        return f"FormData({self._pairs!r})"

    def get_all(self, key: str) -> list[str]:
        """Every value sent for ``key``, in the order it was sent."""
        return [value for name, value in self._pairs if name == key]

    def multi(self) -> dict[str, list[str]]:
        """The whole body as a multi-valued mapping, keys in first-seen order."""
        out: dict[str, list[str]] = {}
        for key, value in self._pairs:
            out.setdefault(key, []).append(value)
        return out


class HandlerArgs:
    """Everything a handler is handed, and the only way it reads its request.

    ``body()`` is where the trap that broke two of three bake-off entries is
    defeated. In the reference this logic lived vendor-side, in
    ``packages/square/src/surface/common.ts``: *"Square's REST API takes JSON,
    but the OAuth endpoints are the ones consumers most often reach with a
    form-encoded client out of habit. Accepting both and normalizing is a
    JUDGMENT call in the mock's favour: it fails on the thing under test rather
    than on a content-type mismatch."* It moves into the core here, so that
    every vendor inherits the guarantee instead of rediscovering the trap --
    and so that no transport adapter is ever asked to decide what a body is.
    That decision at the edge is precisely the leak the framework-free-core
    invariant forbids: it is what makes a form-encoded body require
    ``python-multipart``, a ``Form(...)`` declaration, and shared-code surgery.
    """

    __slots__ = ("_form", "_json_parsed", "_json_value", "auth", "ctx", "params", "req", "route")

    def __init__(
        self,
        *,
        req: UnitRequest,
        params: Mapping[str, str],
        ctx: UnitContext,
        route: Route,
        auth: AuthResult | None = None,
    ) -> None:
        self.req = req
        self.params = params
        self.ctx = ctx
        self.route = route
        #: Resolved by the vendor auth adapter when ``route.auth`` is set.
        self.auth = auth
        self._json_parsed = False
        self._json_value: Any = None
        self._form: FormData | None = None

    # -- raw ---------------------------------------------------------------

    def body_text(self) -> str:
        """The raw body decoded as UTF-8.

        Undecodable bytes become U+FFFD rather than raising, matching
        JavaScript's ``TextDecoder``: a malformed byte in a body must produce
        the vendor's own 400, not an internal 500 from the decoder.
        """
        return self.req.raw_body.decode("utf-8", errors="replace")

    # -- parsed ------------------------------------------------------------

    def json(self) -> Any:
        """The body parsed as JSON, cached.

        An empty or whitespace-only body is ``{}``; anything unparseable raises
        ``invalid_json``. The return is untyped because JSON is untyped -- a
        body may legitimately be an object, an array or a scalar, and pretending
        otherwise here would only move the cast somewhere less visible.
        """
        if not self._json_parsed:
            text = self.body_text()
            if text.strip() == "":
                self._json_value = {}
            else:
                try:
                    self._json_value = _json.loads(text)
                except ValueError as exc:
                    raise UnitError(
                        UnitErrorKind.INVALID_JSON,
                        detail=f"Request body is not valid JSON: {exc}",
                    ) from exc
            self._json_parsed = True
        return self._json_value

    def form(self) -> FormData:
        """The body parsed as ``application/x-www-form-urlencoded``, cached.

        ``keep_blank_values=True`` reproduces ``URLSearchParams``: ``a=`` is the
        empty string, present, not an absent key.
        """
        if self._form is None:
            self._form = FormData(parse_qsl(self.body_text(), keep_blank_values=True))
        return self._form

    def body(self) -> Mapping[str, Any]:
        """The body as fields, whatever content type carried it.

        Branches on the parsed media type -- the part before any ``;``, so
        ``application/x-www-form-urlencoded; charset=utf-8`` is recognised --
        and falls through to JSON, which is what every vendor documents.
        """
        if self.media_type() == "application/x-www-form-urlencoded":
            return self.form()
        parsed = self.json()
        if not isinstance(parsed, dict):
            # ``json()`` is honest about arrays and scalars because a body may
            # legitimately be either. ``body()`` promises fields, so it says so
            # here rather than handing back something a vendor cannot index.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Request body must be a JSON object.",
                field="body",
            )
        # The cached object itself, not a copy: a vendor handler that reads the
        # body twice must see one object, as it does in the reference.
        return parsed

    def media_type(self) -> str:
        """The request's content type with parameters and casing stripped."""
        raw = self.header("content-type") or ""
        return raw.split(";", 1)[0].strip().lower()

    # -- accessors ---------------------------------------------------------

    def query(self, name: str) -> str | None:
        return self.req.query.get(name)

    def query_all(self, name: str) -> Sequence[str]:
        """Every value sent for ``name``, in arrival order; empty when absent."""
        return self.req.query_all.get(name, ())

    def header(self, name: str) -> str | None:
        return self.req.headers.get(name.lower())


# ---------------------------------------------------------------------------
# The journal.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One committed state mutation.

    The journal is the event source, not decoration: an entry exists only after
    the mutation is committed, and the webhook dispatcher derives events from
    entries rather than from handlers. That is why an event cannot exist for a
    mutation that did not commit, and why a handler cannot forget to emit one.
    """

    seq: int
    at: str
    collection: str
    id: str
    op: Literal["insert", "update", "delete"]
    from_version: int | None
    to_version: int | None
    changed: Sequence[str]
    #: Free-form provenance, e.g. ``{"operation_id": "CreateOrder"}``.
    meta: Mapping[str, Any] | None = None


# ---------------------------------------------------------------------------
# Webhook events and signing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventMeta:
    """What the dispatcher assigns before the vendor builds its envelope."""

    event_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class MappedEvent:
    """A vendor event named but not yet built.

    Two phases, because the id belongs to the dispatcher -- it must be stable
    across retries so a consumer can deduplicate -- while its position in the
    envelope belongs to the vendor.
    """

    #: Vendor event type used for subscription matching, e.g. ``order.created``.
    type: str
    entity_id: str
    build: Callable[[EventMeta], object]
    #: Override the assigned id -- used by fixtures that pin one.
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedEvent:
    """A vendor event ready to serialise; ``body`` is the vendor's own envelope."""

    type: str
    event_id: str
    entity_id: str
    created_at: str
    body: object


@dataclass(frozen=True, slots=True)
class SignerProperties:
    """What a signing scheme actually depends on.

    Square's HMAC covers the notification URL and the body; a vendor that sends
    a static shared header depends on neither. This is a property of the
    scheme, not a law, so the signer declares it and conformance checks what is
    true for *that* vendor instead of what was true for the first one.
    """

    #: Signature changes when the subscriber's notification URL changes.
    url_bound: bool = True
    #: Signature changes when the body changes.
    body_bound: bool = True
    #: Signature changes when the subscription's secret changes.
    secret_bound: bool = True
    #: Delivery headers the signature itself occupies, lower-cased.
    #:
    #: Declared rather than discovered because a conformance check asserting
    #: "the signature moved when the secret moved" has to know *which* header
    #: is the signature. Inferring it -- as the header that differs between two
    #: deliveries -- works only for whichever binding is being varied and
    #: cannot separate the signature from a delivery header that varies for its
    #: own reasons, such as a per-event timestamp. A signer that leaves this
    #: empty is declaring that it contributes no signature header, and the
    #: suite skips the signing contract rather than guessing.
    signature_headers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SignInput:
    """Everything a signer is allowed to sign over.

    ``raw_body`` is the exact bytes about to be sent, because real vendors sign
    the bytes and not a re-encoding of the object they came from.
    """

    notification_url: str
    raw_body: bytes
    secret: str
    attempt: int
    event: PreparedEvent


@dataclass(frozen=True, slots=True)
class MagicTriggerSpec:
    """In-band fault triggering.

    Prior art: Square's sandbox uses magic values in ordinary request fields
    (``cnon:card-nonce-declined``) rather than a control channel, so a
    consumer's own client library can drive a fault.
    https://developer.squareup.com/docs/devtools/sandbox/testing
    """

    prefix: str
    #: Dot paths into the parsed body that are scanned for the prefix.
    body_paths: Sequence[str] = ()
    query_params: Sequence[str] = ()
    headers: Sequence[str] = ()


# ---------------------------------------------------------------------------
# Logging.
# ---------------------------------------------------------------------------


class Logger(Protocol):
    """Structured logging, as the core uses it. Four levels, fields optional."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


# ---------------------------------------------------------------------------
# The fork contract: what a vendor supplies.
# ---------------------------------------------------------------------------


class Handler(Protocol):
    """One route's implementation. Synchronous, by design -- see the module docstring."""

    def __call__(self, args: HandlerArgs, /) -> ReplyInit | UnitResponse: ...


class ErrorShaper(Protocol):
    """The vendor's single lookup table from core error kind to wire format."""

    def shape(self, err: UnitError, ctx: UnitContext) -> ShapedError:
        """Turn a core error into the vendor's wire representation."""
        ...

    def not_found(self, req: UnitRequest, ctx: UnitContext) -> ShapedError:
        """Body for a path that matched no route at all."""
        ...


class AuthAdapter(Protocol):
    """Resolves a presented credential into a principal and its scopes.

    The kernel checks ``Route.scopes`` itself against the returned result; a
    vendor that also checked them would be the second place to forget.
    """

    def describe(self) -> Mapping[str, str]:
        """Human description used by ``/__unit/info``."""
        ...

    def resolve(self, args: HandlerArgs, mode: AuthMode) -> AuthResult:
        """Resolve the principal, or raise a ``UnitError``. ``mode`` is ``route.auth``.

        ``args.auth`` is still ``None`` at this point; the pipeline sets it from
        the return value.
        """
        ...

    def credentials(self, ctx: UnitContext) -> Sequence[AuthCredential]:
        """Credentials that would resolve right now, published at ``/__unit/auth``.

        A method on the adapter and not a static table, because a credential is
        state: a token the scenario seeded, a token an OAuth flow just minted, a
        token that has since been revoked. Taking the context means the answer
        is computed from the store rather than copied out of it at construction.

        Returning ``()`` is legal and honest for a vendor whose credentials
        genuinely cannot be enumerated -- the conformance suite skips the
        contracts it cannot ask rather than pretending it asked them.
        """
        ...


class Signer(Protocol):
    """The vendor's webhook signature scheme, and its delivery headers.

    ``properties`` is declared rather than assumed so that conformance can
    check the directions this scheme actually claims.

    :meth:`headers` is the hook that de-vendors delivery. The reference writes
    a content type and three brand-prefixed retry headers straight into
    vendor-neutral core (``packages/core/src/webhooks/dispatcher.ts``, lines
    292-300); here the core computes neutral metadata and the vendor names its
    own headers. It is one hook and not two -- ``sign`` and ``headers`` on the
    same protocol -- because the signature is a header too, and two hooks would
    be two chances to register only one, whose failure mode is a delivery that
    is signed but uncounted, or counted but unsigned, and silent at the sink.
    """

    @property
    def properties(self) -> SignerProperties: ...

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        """Headers carrying the signature for one outbound delivery attempt."""
        ...

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Every non-signature header for one attempt, including the content type.

        The core adds nothing to what comes back, so a vendor that returns an
        empty mapping ships deliveries with no content type -- which is its
        decision to make and not the core's to second-guess.
        """
        ...

    def describe(self) -> Mapping[str, str]:
        """Human description used by ``/__unit/info``."""
        ...


class EventMapper(Protocol):
    """Turns one committed state mutation into the vendor's events."""

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]: ...


class VendorDefinition(Protocol):
    """Everything one vendor supplies. The core supplies everything else.

    A protocol rather than a base class: a vendor module builds whatever object
    it likes -- a frozen dataclass, typically -- and structural typing checks it
    against this without an import in the inheritance direction.

    ``hydrate`` and ``decorate`` are required rather than optional. The
    reference marks both ``?``; here a vendor with nothing to do writes an
    empty method, which is one line and cannot be confused with "this vendor
    forgot".
    """

    @property
    def name(self) -> str:
        """Slug used in ids, config and reports."""
        ...

    @property
    def display_name(self) -> str: ...

    @property
    def api_version(self) -> str | None:
        """The vendor's own API version string, surfaced by ``/__unit/info``."""
        ...

    @property
    def capabilities(self) -> Sequence[CapabilityDecl]: ...

    @property
    def routes(self) -> Sequence[Route]: ...

    @property
    def errors(self) -> ErrorShaper: ...

    @property
    def auth(self) -> AuthAdapter: ...

    @property
    def signer(self) -> Signer | None: ...

    @property
    def events(self) -> EventMapper | None: ...

    @property
    def magic(self) -> MagicTriggerSpec | None: ...

    @property
    def machines(self) -> Mapping[str, MachineDef]:
        """Named state machines this vendor's entities move through.

        Reaching the control plane is the point: without a registration
        mechanism a machine is a module-level object nothing can see, and
        "every declared terminal state really is terminal" is unassertable
        from outside the vendor package. The mapping is empty for a vendor
        whose entities have no lifecycle.
        """
        ...

    @property
    def retry_defaults(self) -> ProfileDocument:
        """This vendor's own defaults, merged **under** the profile document.

        The delivery retry schedule above all. It lives here rather than in the
        core because a schedule is a documented property of one vendor's
        webhook system, and the core's ``RetryPolicy`` therefore ships with an
        empty schedule and no vendor default. Unit construction refuses to
        start when the ``webhooks`` capability is declared and the merged
        schedule is still empty, so a vendor that forgets this is a startup
        error rather than a delivery that exhausts on its first attempt.
        """
        ...

    @property
    def profile_dir(self) -> Path:
        """Directory holding this vendor's ``<name>.json`` profiles."""
        ...

    @property
    def base_dir(self) -> Path:
        """Directory a profile's relative ``seed`` path resolves against.

        Separate from :attr:`profile_dir` because the reference resolves seeds
        against the *package* root while profiles live one level down, and
        collapsing the two would silently move every relative seed path.
        """
        ...

    @property
    def not_supported(self) -> Mapping[str, str]:
        """Core-gated capabilities this vendor deliberately does not implement,
        each with a prose reason, echoed into ``/__unit/info``.

        Without it, a capability the core gates on but the vendor never
        declares evaluates to "disabled" and the behaviour is silently off.
        Conformance asserts that every core-gated capability is either declared
        or listed here, and that nothing is both.
        """
        ...

    @property
    def volatile_fields(self) -> Sequence[str]:
        """Entity fields excluded from the state digest because they carry
        wall-clock time. ``created_at``/``updated_at`` are excluded already."""
        ...

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        """Load a seed document into an empty store."""
        ...

    def decorate(self, res: MutableResponse, ctx: UnitContext, req: UnitRequest) -> None:
        """Last chance to add vendor-wide response headers (API version, ...).

        Applied to error responses on a matched non-internal route as well as
        to successes, and never to a 404, where no route matched.
        """
        ...


class UnitContext(Protocol):
    """Everything a handler is allowed to touch.

    The design point is as much what is absent as what is present: re-seeding
    the store and enumerating the router are *not* here, so a route handler
    cannot reach them. Only the control plane can, through a separate typed
    binding it is given at construction.

    Every member is declared here with its concrete type imported under
    ``if TYPE_CHECKING:``, which is how the kernel/subsystem import cycle stays
    broken: the subsystems import this module at run time, and this module
    imports them only for the type checker. ``webhooks`` was the last to land
    and followed the same three lines as the rest -- the guarded import, the
    property, and the field on the context the unit builds.
    """

    @property
    def vendor(self) -> VendorDefinition: ...

    @property
    def config(self) -> ResolvedConfig: ...

    @property
    def store(self) -> Store: ...

    @property
    def capabilities(self) -> CapabilityRegistry: ...

    @property
    def chaos(self) -> ChaosEngine: ...

    @property
    def clock(self) -> Clock: ...

    @property
    def rng(self) -> Rng: ...

    @property
    def webhooks(self) -> WebhookDispatcher: ...

    @property
    def log(self) -> Logger: ...
