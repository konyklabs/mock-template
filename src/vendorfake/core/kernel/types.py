"""The unit contract: everything a vendor module implements against.

INVARIANT: **this is deliberately not an HTTP contract.** A unit consumes a ``UnitRequest`` and produces a
``UnitResponse``; ``transport`` names whichever binding produced it. ``raw_body`` is the exact received bytes and
``body`` is already serialised, because a webhook signature covers raw bytes. Path templates are ``{order_id}``,
never ``:order_id``, in all four places one is consumed: the router, the chaos ``match.route`` key, the
capability-to-routes index and the generated OpenAPI document.

INVARIANT: **the core is synchronous** -- ``Handler`` returns a value, not an awaitable, because await points would
interleave concurrent requests and make id minting non-deterministic.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from urllib.parse import parse_qsl

from vendorfake.core.rand.rng import Rng
from vendorfake.core.time.clock import Clock

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Each imports this module at run time, so an unguarded import back would
    # be a cycle; that guard keeps the kernel free of its own subsystems.
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
    "NearMiss",
    "PaginationSpec",
    "PreparedEvent",
    "ReplyInit",
    "RequestRecord",
    "ResponseObserver",
    "Route",
    "SeedingVendor",
    "ShapedError",
    "SignInput",
    "Signer",
    "SignerProperties",
    "TransportDirective",
    "TransportKind",
    "UnitContext",
    "UnitError",
    "UnitErrorKind",
    "UnitRequest",
    "UnitResponse",
    "VendorDefinition",
]

# Open string vocabularies: the alias documents the intent, the constants are the suggestions.

TransportKind = str
"""Names the binding that produced a request: ``http`` or ``inprocess``."""

AuthMode = str
"""Passed verbatim to the vendor auth adapter; the core never interprets it."""


# Errors.


class UnitErrorKind(StrEnum):
    """The twenty core-generic failure kinds; no more without a conformance check moving with them. The kernel raises
    only these, and the vendor's ``ErrorShaper`` turns them into that vendor's wire format."""

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
    """A core-generic failure on its way to a vendor's wire format. ``kind`` is coerced, so a misspelled one raises at
    the raise site rather than falling out of the shaper's table as an unrelated status."""

    __slots__ = ("delay_ms", "detail", "fault", "field", "info", "kind", "rule_id")

    def __init__(
        self,
        kind: UnitErrorKind | str,
        *,
        detail: str | None = None,
        field: str | None = None,
        info: Mapping[str, Any] | None = None,
        delay_ms: int = 0,
        fault: str | None = None,
        rule_id: str | None = None,
    ) -> None:
        resolved = UnitErrorKind(kind)
        super().__init__(detail if detail is not None else resolved.value)
        self.kind: UnitErrorKind = resolved
        self.detail: str | None = detail
        #: Request field the error is about, in the vendor's dot notation.
        self.field: str | None = field
        #: Machine-readable context surfaced under ``x-unit-error`` / the sidecar.
        self.info: Mapping[str, Any] | None = info
        #: How long the refusal is withheld, carried to :attr:`UnitResponse.delay_ms`. A field and not an
        #: :attr:`info` key, which the sidecar publishes verbatim to the wire.
        self.delay_ms: int = delay_ms
        #: The chaos fault name and rule id that raised this, or ``None`` for an ordinary refusal. ``_shape`` stamps
        #: them as the ``vendorfake-fault`` / ``vendorfake-rule`` headers.
        self.fault: str | None = fault
        self.rule_id: str | None = rule_id


@dataclass(frozen=True, slots=True)
class ShapedError:
    """A ``UnitError`` after the vendor has said what it looks like on the wire."""

    status: int
    body: object
    headers: Mapping[str, str] = field(default_factory=dict)


# Requests and responses: the seam.


@dataclass(frozen=True, slots=True)
class UnitRequest:
    """A request as the kernel sees it, whatever transport delivered it."""

    id: str
    method: str
    path: str
    #: The scalar view: repeated keys collapse to the last value, matching
    #: every binding. Every value is kept in ``query_all``.
    query: Mapping[str, str]
    #: Header names are lowercased by every binding before the kernel sees them.
    headers: Mapping[str, str]
    #: Exact received bytes, never a re-serialisation: a webhook signature scheme signs the raw body.
    raw_body: bytes
    transport: TransportKind
    received_at: str
    #: Every value sent for each key, in arrival order. INVARIANT: both views have the same keys and ``query[k] ==
    #: query_all[k][-1]``. Defaults to the single-valued view of ``query``; supplied explicitly, it is checked.
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
class TransportDirective:
    """An instruction to a binding about the *socket*, not the vendor's bytes: the three faults no response schema can
    express. ``UnitResponse.body`` still carries what the handler produced. The kernel never touches sockets, so it
    builds this value and stops. provenance: transport; see ``docs/concepts/chaos-rules-and-faults.md`` ("Transport
    faults")."""

    kind: Literal["connection_reset", "empty_response", "slow_body"]
    chunk_bytes: int = 0
    chunk_delay_ms: int = 0


@dataclass(frozen=True, slots=True)
class UnitResponse:
    """Already-serialised. A transport adapter returns ``body`` untouched."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    #: How long this answer is withheld, in milliseconds. **The kernel decides whether to delay; the binding decides
    #: how**, since only the binding knows the caller's timeout. provenance: transport.
    delay_ms: int = 0
    #: A socket-level instruction alongside ``delay_ms``. See :class:`TransportDirective`.
    transport: TransportDirective | None = None


@dataclass(frozen=True, slots=True)
class ReplyInit:
    """What a handler may return. Precedence is contract: ``raw``, ``text``, then JSON, each chosen on ``is not None``
    because ``redirect()`` and ``no_content()`` return an empty text. ``json=None`` serialises to ``b"{}"``, never
    ``b"null"``."""

    status: int | None = None
    headers: Mapping[str, str] | None = None
    json: Any = None
    text: str | None = None
    raw: bytes | None = None


ResponseObserver = Callable[[UnitRequest, UnitResponse], None]
"""Called by a binding after the unit answered; may raise to turn the answer into a 500 naming the violation."""


# What the unit observed about a request. Distinct from the journal, which records committed *mutations* only.


@dataclass(frozen=True, slots=True)
class NearMiss:
    """One route the unit offers that a request nearly asked for. Declared here and not beside the scorer, which
    imports :class:`Route` from this module; the scoring stays there."""

    route: str
    operation_id: str | None
    #: 0.0 to 1.0, comparable only against other candidates for the same request.
    score: float

    def as_json(self) -> dict[str, Any]:
        """Two decimal places on the wire; full precision stays in memory, so
        no ordering is decided by the rounding."""
        body: dict[str, Any] = {"route": self.route, "score": round(self.score, 2)}
        if self.operation_id is not None:
            body["operation_id"] = self.operation_id
        return body


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """One request the unit handled, matched or not -- the half the journal cannot answer, since an entry there exists
    only where a mutation committed. INVARIANT: **no body and no headers**, so tokens, signatures and large
    documents never accumulate; the id is kept instead, echoed on the response as ``x-unit-request-id``."""

    id: str
    #: :attr:`UnitRequest.received_at` -- when a binding took delivery, not the unit's (possibly virtual) clock.
    received_at: str
    method: str
    path: str
    route: str | None
    operation_id: str | None
    status: int
    #: Whether a route answered. ``False`` covers both "no such path" (a 404, which carries :attr:`near_misses`) and
    #: "that path, wrong verb" (a 405, which does not -- the answer already names the methods that are allowed).
    matched: bool
    fault: str | None
    #: The id of the rule that armed it; ``magic`` for an in-band trigger.
    rule_id: str | None
    duration_ms: int
    #: The closest routes, best first. Empty for anything that matched.
    near_misses: tuple[NearMiss, ...] = ()
    #: The last journal ``seq`` this request committed, or ``None`` when it committed nothing -- a refusal, a
    #: replay, a read, a request-phase fault. Compare with ``GET /__unit/journal?since=`` to find the entries.
    committed_journal_seq: int | None = None
    #: ``True`` when the handler committed and the caller still did not get its clean answer. ``slow_body`` delivers
    #: intact, only late, so it does not count. The mutation stands; it is discarded only from the caller's view.
    discarded_mutation: bool = False

    def as_json(self) -> dict[str, Any]:
        """``matched``, ``near_misses`` and ``discarded_mutation`` are always present, being the three a caller filters
        on; every other null is an absent key."""
        body: dict[str, Any] = {
            "id": self.id,
            "received_at": self.received_at,
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "matched": self.matched,
            "duration_ms": self.duration_ms,
            "near_misses": [miss.as_json() for miss in self.near_misses],
            "discarded_mutation": self.discarded_mutation,
        }
        for key, value in (
            ("route", self.route),
            ("operation_id", self.operation_id),
            ("fault", self.fault),
            ("rule_id", self.rule_id),
            ("committed_journal_seq", self.committed_journal_seq),
        ):
            if value is not None:
                body[key] = value
        return body


# Capabilities, auth, routes.


@dataclass(frozen=True, slots=True)
class CapabilityDecl:
    """One capability a vendor declares. ``surface`` owns routes and answers a disabled call explicitly; ``behavior``
    gates conduct with no surface of its own, and conformance checks the two differently."""

    #: Dotted name. A child (``webhooks.chaos``) is usable only while its parent
    #: (``webhooks``) is enabled; the registry enforces that.
    name: str
    summary: str
    requires: Sequence[str] = ()
    kind: Literal["surface", "behavior"] = "surface"


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Who the presented credential resolves to, as the vendor sees it."""

    principal_id: str
    scopes: Sequence[str]
    token_id: str | None = None
    meta: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AuthCredential:
    """A credential a caller can present, and what it grants, so authentication is drivable from outside the process.
    Published at ``GET /__unit/auth``: this is a fake, and its credentials are scenario data, not secrets.
    ``headers`` is the whole instruction, whatever the vendor's scheme."""

    label: str
    mode: AuthMode
    headers: Mapping[str, str]
    scopes: Sequence[str] = ()
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

    key_path: str
    scope: str
    #: When true, a missing key is an error rather than "not idempotent".
    required: bool = False
    #: What a reused key with a DIFFERENT body does. ``conflict`` is the usual REST contract; ``replay`` returns the
    #: stored response and drops the new request, which is DOCUMENTED (Square's UpdateOrder: "you get a 200 response
    #: but the returned order doesn't reflect any of your updates").
    on_mismatch: Literal["conflict", "replay"] = "conflict"


@dataclass(frozen=True, slots=True)
class PaginationSpec:
    """How a list route pages, published at ``GET /__unit/routes``, so a language-independent check can walk it and
    assert no row repeats and none is lost. Two styles are named; a vendor paging some third way declares nothing
    and is not asked. ``where`` says where the page parameters travel."""

    #: ``cursor`` -- an opaque token in the response names the next page --
    #: or ``offset`` -- the caller counts rows itself.
    style: Literal["cursor", "offset"]
    items_path: str
    where: Literal["query", "body"] = "query"
    limit_param: str = "limit"
    #: Cursor style: the request parameter, and the response dot path to the next cursor.
    cursor_param: str = "cursor"
    next_cursor_path: str = "cursor"
    offset_param: str = "offset"
    id_path: str = "id"
    #: ``False`` declares that this route pages but the identity walk cannot be
    #: driven, with :attr:`unwalkable_reason` saying why. A paginating route is
    #: walked or excused on the record; an empty reason fails outright.
    walkable: bool = True
    unwalkable_reason: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "style": self.style,
            "items_path": self.items_path,
            "where": self.where,
            "limit_param": self.limit_param,
            "cursor_param": self.cursor_param,
            "next_cursor_path": self.next_cursor_path,
            "offset_param": self.offset_param,
            "id_path": self.id_path,
            "walkable": self.walkable,
            "unwalkable_reason": self.unwalkable_reason,
        }


@dataclass(frozen=True, slots=True)
class Route:
    """One entry in a vendor's surface. Routes are data, not decorators: a vendor builds a ``tuple[Route, ...]`` of
    bound methods, which is why a vendor module has nothing to import a web framework for."""

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
    #: How this route pages its rows, or ``None``. See :class:`PaginationSpec`.
    pagination: PaginationSpec | None = None
    #: A body this route ACCEPTS, published at ``GET /__unit/routes``. A contract about a committed mutation is
    #: unaskable until something has succeeded, so the vendor publishes one request that works, written against the
    #: scenario the profile loads.
    example_body: Mapping[str, Any] | None = None
    #: Path parameters that make the example applicable, naming seeded entities: the other half of
    #: :attr:`example_body` for a route whose path addresses one entity.
    example_params: Mapping[str, str] | None = None
    operation_id: str | None = None
    summary: str | None = None
    #: Control-plane route: no auth, no chaos, no idempotency, never in the
    #: vendor surface report and never decorated.
    internal: bool = False
    #: Whether the pipeline takes the unit-wide request lock. True except for routes whose handler blocks on
    #: machinery another request must feed -- draining the webhook queue, advancing a virtual clock -- which would
    #: otherwise hold the whole unit for a full delivery timeout. The request lock only makes id minting and journal
    #: ordering deterministic.
    serialized: bool = True

    @property
    def key(self) -> str:
        """``"POST /v2/orders/{order_id}/pay"`` -- how a chaos rule names a route."""
        return f"{self.method.upper()} {self.path}"


# The request body, content-type general.


class FormData(Mapping[str, str]):
    """A parsed ``application/x-www-form-urlencoded`` body. The scalar view is last-wins, so an ordinary
    ``client_id=x`` reads as a string; the repeats are kept and ``get_all()`` exposes them."""

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
    """Everything a handler is handed, and the only way it reads its request. ``body()`` accepts JSON and form encoding
    alike, so a consumer reaching an OAuth route with a form-encoded client fails on the thing under test rather
    than on a content type. JUDGMENT; keeping that decision out of the transport adapter is what the
    framework-free-core invariant requires."""

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

    def body_text(self) -> str:
        """The raw body decoded as UTF-8. Undecodable bytes become U+FFFD, so a
        malformed byte produces the vendor's own 400, not a 500."""
        return self.req.raw_body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """The body parsed as JSON, cached. An empty or whitespace-only body is ``{}``; anything unparseable raises
        ``invalid_json``. Untyped, because a body may legitimately be an object, an array or a scalar."""
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
        ``keep_blank_values=True``: ``a=`` is present and empty, not absent."""
        if self._form is None:
            self._form = FormData(parse_qsl(self.body_text(), keep_blank_values=True))
        return self._form

    def body(self) -> Mapping[str, Any]:
        """The body as fields, whatever content type carried it: branches on the
        media type before any ``;`` and falls through to JSON."""
        if self.media_type() == "application/x-www-form-urlencoded":
            return self.form()
        parsed = self.json()
        if not isinstance(parsed, dict):
            # ``body()`` promises fields, where ``json()`` is honest about arrays and scalars.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Request body must be a JSON object.",
                field="body",
            )
        # The cached object itself, not a copy: a handler reading the body twice must see one object.
        return parsed

    def media_type(self) -> str:
        """The request's content type with parameters and casing stripped."""
        raw = self.header("content-type") or ""
        return raw.split(";", 1)[0].strip().lower()

    def query(self, name: str) -> str | None:
        return self.req.query.get(name)

    def query_all(self, name: str) -> Sequence[str]:
        """Every value sent for ``name``, in arrival order; empty when absent."""
        return self.req.query_all.get(name, ())

    def header(self, name: str) -> str | None:
        return self.req.headers.get(name.lower())


# The journal.


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One committed state mutation. The journal is the event source: an entry exists only after the mutation commits
    and the dispatcher derives events from entries, so no handler can forget to emit one."""

    seq: int
    at: str
    collection: str
    id: str
    op: Literal["insert", "update", "delete"]
    from_version: int | None
    to_version: int | None
    changed: Sequence[str]
    meta: Mapping[str, Any] | None = None


# Webhook events and signing.


@dataclass(frozen=True, slots=True)
class EventMeta:
    """What the dispatcher assigns before the vendor builds its envelope."""

    event_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class MappedEvent:
    """A vendor event named but not yet built. Two phases: the id belongs to the dispatcher, being stable across
    retries, while its position in the envelope belongs to the vendor."""

    type: str
    entity_id: str
    build: Callable[[EventMeta], object]
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
    """What a signing scheme actually depends on: a property of the scheme and not a law, so the signer declares it and
    conformance checks what is true for that vendor."""

    url_bound: bool = True
    body_bound: bool = True
    secret_bound: bool = True
    #: Delivery headers the signature occupies, lower-cased. Declared rather than inferred from what differs between
    #: two deliveries, which cannot separate the signature from a per-event timestamp. Empty means no signature
    #: header, and the suite skips the signing contract.
    signature_headers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SignInput:
    """Everything a signer is allowed to sign over. ``raw_body`` is the exact
    bytes about to be sent, because vendors sign bytes, not a re-encoding."""

    notification_url: str
    raw_body: bytes
    secret: str
    attempt: int
    event: PreparedEvent


@dataclass(frozen=True, slots=True)
class MagicTriggerSpec:
    """In-band fault triggering. DOCUMENTED: Square's sandbox drives faults from
    magic values in ordinary request fields (``cnon:card-nonce-declined``).
    https://developer.squareup.com/docs/devtools/sandbox/testing"""

    prefix: str
    body_paths: Sequence[str] = ()
    query_params: Sequence[str] = ()
    headers: Sequence[str] = ()


# Logging.


class Logger(Protocol):
    """Structured logging, as the core uses it. Four levels, fields optional."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


# The fork contract: what a vendor supplies.


class Handler(Protocol):
    """One route's implementation. Synchronous; see the module docstring."""

    def __call__(self, args: HandlerArgs, /) -> ReplyInit | UnitResponse: ...


class ErrorShaper(Protocol):
    """The vendor's single lookup table from core error kind to wire format."""

    def shape(self, err: UnitError, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """Turn a core error into the vendor's wire representation.

        ``describing`` is set only by ``GET /__unit/errors``, which renders the table rather than a refusal.
        INVARIANT: **describing consumes nothing** -- no id from a vendor stream, no current time -- or a read-only
        route renumbers the caller's scenario and C10's byte-for-byte comparison of the two bindings becomes
        unsatisfiable. A shaper with a per-request envelope field substitutes a fixed synthetic value and says so at
        the site. An argument, not an ``err.info`` key, which reaches the wire."""
        ...

    def not_found(self, req: UnitRequest, ctx: UnitContext, *, describing: bool = False) -> ShapedError:
        """Body for a path that matched no route at all. ``describing`` means what it does on :meth:`shape`; this row
        of ``GET /__unit/errors`` does not come from the table, so it needs the signal separately."""
        ...

    def describe(self) -> Mapping[str, Mapping[str, Any]]:
        """The table as a report publishes it: one row per ``UnitErrorKind`` value, each carrying at least ``status``
        and ``provenance`` (``"documented"`` or ``"judgment"``). ``GET /__unit/errors`` reads every row's provenance
        from here."""
        ...


@runtime_checkable
class SeedingVendor(Protocol):
    """A vendor that publishes its own seed object, so ``unit("<name>").seed`` answers instead of refusing: the
    optional half of :class:`VendorDefinition`, discovered structurally by ``isinstance`` because for a seed "there
    is none" is a legitimate, permanent answer. The return type is ``object`` deliberately: the hook must satisfy
    ``vendorfake.testing.Seed``, the core may not import that module, and a second copy of the protocol here would
    put "a seed" in two places. ``seed_for`` is where a hook returning the wrong shape is caught."""

    def seed(self, vendor_config: Mapping[str, object]) -> object:
        """This vendor's seed object for a unit built on ``vendor_config``, the resolved profile's ``vendor`` block.
        Taking that rather than a built ``Unit`` keeps the hook usable before a unit exists."""
        ...


class AuthAdapter(Protocol):
    """Resolves a presented credential into a principal and its scopes. The
    kernel checks ``Route.scopes`` against the result itself."""

    def describe(self) -> Mapping[str, str]:
        """Human description used by ``/__unit/info``."""
        ...

    def resolve(self, args: HandlerArgs, mode: AuthMode) -> AuthResult:
        """Resolve the principal, or raise a ``UnitError``; ``mode`` is
        ``route.auth``. ``args.auth`` is still ``None`` here."""
        ...

    def credentials(self, ctx: UnitContext) -> Sequence[AuthCredential]:
        """Credentials that would resolve right now, published at ``/__unit/auth``. A method and not a static table,
        because a credential is state. Returning ``()`` is legal, and the conformance suite then skips the contracts
        it cannot ask."""
        ...


class Signer(Protocol):
    """The vendor's webhook signature scheme, and its delivery headers. ``properties`` is declared rather than assumed.
    The core computes neutral delivery metadata and :meth:`headers` names the vendor's headers; signing and naming
    live on one protocol because the signature is a header too."""

    @property
    def properties(self) -> SignerProperties: ...

    def sign(self, payload: SignInput) -> Mapping[str, str]:
        """Headers carrying the signature for one outbound delivery attempt."""
        ...

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Every non-signature header for one attempt, including the content type. The core adds nothing, so an empty
        mapping ships deliveries with no content type."""
        ...

    def describe(self) -> Mapping[str, str]:
        """Human description used by ``/__unit/info``."""
        ...


class EventMapper(Protocol):
    """Turns one committed state mutation into the vendor's events."""

    def map(self, entry: JournalEntry, ctx: UnitContext) -> Sequence[MappedEvent]: ...


class VendorDefinition(Protocol):
    """Everything one vendor supplies; the core supplies everything else. A protocol rather than a base class, so a
    vendor builds whatever object it likes. Every member is required: a vendor with nothing to do writes an empty
    method, which cannot be confused with "this vendor forgot". The one optional thing, a seed object, is
    :class:`SeedingVendor` instead."""

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
    def roles(self) -> Mapping[str, str]:
        """The neutral role vocabulary -- ``auth``, ``orders``, ``webhooks``, ``chaos`` -- mapped to this vendor's own
        capability names, so ``capabilities=["auth"]`` travels across vendors that name their login surface
        differently. A conformance clause checks that all four are present and every value names a declared
        capability. ``webhooks`` and ``chaos`` map to themselves, being the core's gated vocabulary."""
        ...

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
        """Named state machines this vendor's entities move through, registered so the control plane can see them and
        "every declared terminal state really is terminal" is assertable from outside the vendor package. Empty for
        a vendor whose entities have no lifecycle."""
        ...

    @property
    def retry_defaults(self) -> ProfileDocument:
        """This vendor's own defaults, merged **under** the profile document -- the delivery retry schedule above all,
        a documented property of one vendor rather than a core default. Unit construction refuses to start when
        ``webhooks`` is declared and the merged schedule is empty."""
        ...

    @property
    def profile_dir(self) -> Path:
        """Directory holding this vendor's ``<name>.json`` profiles."""
        ...

    @property
    def base_dir(self) -> Path:
        """Directory a profile's relative ``seed`` path resolves against. Separate from :attr:`profile_dir`, which is
        one level down; collapsing the two would silently move every relative seed path."""
        ...

    @property
    def not_supported(self) -> Mapping[str, str]:
        """Core-gated capabilities this vendor deliberately does not implement, each with a reason, echoed into
        ``/__unit/info``; otherwise an undeclared capability is silently off. Conformance asserts each is declared
        or listed here, never both."""
        ...

    @property
    def volatile_fields(self) -> Sequence[str]:
        """Entity field names whose *values* the state digest ignores, the unit writing them from its clock. Matched at
        any depth outside opaque subtrees; a set field still hashes as "set"."""
        ...

    @property
    def opaque_fields(self) -> Sequence[str]:
        """Names of caller free-form subtrees the state digest takes verbatim. Matched at any depth and winning over a
        volatile name: the scrub never descends below an opaque key, so a caller's own ``created_at`` inside a
        ``metadata`` block is digested as state."""
        ...

    def hydrate(self, ctx: UnitContext, seed: object) -> None:
        """Load a seed document into an empty store."""
        ...

    def decorate(self, headers: dict[str, str], ctx: UnitContext, req: UnitRequest) -> None:
        """Add vendor-wide response headers (API version, ...) in place, on
        every matched non-internal route and never on a 404."""
        ...


class UnitContext(Protocol):
    """Everything a handler is allowed to touch. Re-seeding the store and enumerating the router are deliberately
    absent, reachable only by the control plane. Every member's concrete type is imported under ``if
    TYPE_CHECKING:``, which is how the import cycle stays broken."""

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
