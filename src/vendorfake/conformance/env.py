"""Runtime discovery: how a check learns what this unit is without being told.

FOR: letting a contract say "probe the first enabled mutating route" instead of
"POST /v2/orders". Everything a check needs to aim itself -- routes,
capabilities, the seed digest, declared lifecycles, the in-band trigger's
spelling -- is read from the control plane at run time, so a second vendor
inherits the contracts rather than editing them.

INVARIANT: **no per-profile skip list, anywhere.** Preconditions are declared
as :class:`~vendorfake.conformance.types.Requires` and resolved here by asking
the unit. A list of "checks to skip on the oauth-only profile" would be a
second source of truth about a profile, and the moment the profile changed it
would be a lie that reported as a pass.

WHY THE PROBE VALUES ARE WHAT THEY ARE. A path template's parameters are
filled with a value that cannot exist in any seed (``conformance-probe``), so
a probe reaches the handler and is refused for a reason the check is asserting
about -- a disabled capability, an injected fault, a missing token -- rather
than accidentally succeeding and mutating state a later assertion reads. The
one exception is a parameter the target declares as its *tenant*
(``ConformanceTarget.path_params``): a path scoped to the merchant a
credential belongs to has to name that merchant, or no authenticated probe can
ever be honoured and no example body can ever commit. Everything below the
tenant stays the probe.

WHAT A CHECK MAY IMPORT FROM THE CORE. The core's own vocabulary: the error
kinds it raises and the capabilities it gates on. Those are the contract; a
check asserting on them is asserting on the specification. What a check may
never do is reach a unit object -- which is why this module holds a client and
a profile name and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from vendorfake.conformance.client import ConformanceClient
from vendorfake.conformance.types import ConformanceFailure, ConformanceSkip, ConformanceTarget, Requires
from vendorfake.core.capability.gates import CoreCapability

__all__ = [
    "PROBE_SEGMENT",
    "CapabilityRow",
    "CheckEnv",
    "Credential",
    "InBandTrigger",
    "RouteRow",
    "ancestors",
    "check_env",
    "concrete_path",
    "unmet_precondition",
]

PROBE_SEGMENT = "conformance-probe"
"""What a path parameter is filled with. Not a plausible id, deliberately."""

CONTROL_PREFIX = "/__unit/"
"""Where the control plane lives. Restated as a constant so the checks read as
prose; the kernel owns the enforcement that no vendor route may start here."""

_MUTATING_METHODS = frozenset({"POST", "PUT"})


def concrete_path(template: str, params: Mapping[str, str] | None = None) -> str:
    """``/v2/orders/{order_id}`` -> ``/v2/orders/conformance-probe``; a
    parameter named in ``params`` (the target's tenant) takes its value."""
    filled = params or {}

    def segment_of(segment: str) -> str:
        if segment.startswith("{") and segment.endswith("}"):
            return filled.get(segment[1:-1], PROBE_SEGMENT)
        return segment

    return "/".join(segment_of(segment) for segment in template.split("/"))


def ancestors(name: str) -> tuple[str, ...]:
    """``a.b.c`` -> ``('a', 'a.b')``. Dotted capabilities need their parents on."""
    parts = name.split(".")
    return tuple(".".join(parts[: index + 1]) for index in range(len(parts) - 1))


def _nested(path: str, value: str) -> dict[str, Any]:
    """``order.reference_id`` -> ``{'order': {'reference_id': value}}``."""
    body: dict[str, Any] = {}
    cursor = body
    keys = path.split(".")
    for key in keys[:-1]:
        nxt: dict[str, Any] = {}
        cursor[key] = nxt
        cursor = nxt
    cursor[keys[-1]] = value
    return body


@dataclass(frozen=True, slots=True)
class RouteRow:
    """One row of ``GET /__unit/routes``, as a check reads it."""

    method: str
    path: str
    capability: str
    internal: bool
    auth: str | None = None
    scopes: tuple[str, ...] = ()
    idempotency: Mapping[str, Any] | None = None
    pagination: Mapping[str, Any] | None = None
    example_body: Mapping[str, Any] | None = None
    example_params: Mapping[str, str] | None = None
    operation_id: str | None = None
    #: The target's tenant parameters; see ``ConformanceTarget.path_params``.
    path_params: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, row: Mapping[str, Any], path_params: Mapping[str, str] | None = None) -> RouteRow:
        return cls(
            path_params=dict(path_params or {}),
            method=str(row["method"]).upper(),
            path=str(row["path"]),
            capability=str(row["capability"]),
            internal=bool(row.get("internal", False)),
            auth=None if row.get("auth") is None else str(row["auth"]),
            scopes=tuple(str(name) for name in row.get("scopes", ())),
            idempotency=None if row.get("idempotency") is None else dict(row["idempotency"]),
            pagination=None if row.get("pagination") is None else dict(row["pagination"]),
            example_body=None if row.get("example_body") is None else dict(row["example_body"]),
            example_params=None
            if row.get("example_params") is None
            else {str(name): str(value) for name, value in dict(row["example_params"]).items()},
            operation_id=None if row.get("operation_id") is None else str(row["operation_id"]),
        )

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def probe_path(self) -> str:
        return concrete_path(self.path, self.path_params)

    @property
    def example_path(self) -> str:
        """The path the published example applies to: the route's declared
        example_params first, the target's tenant parameters over them (the
        tenant is authoritative about who the credential belongs to), and the
        probe segment for anything neither names. Equal to :attr:`probe_path`
        for a route that declares no example_params."""
        return concrete_path(self.path, {**(self.example_params or {}), **self.path_params})


@dataclass(frozen=True, slots=True)
class CapabilityRow:
    """One row of ``GET /__unit/capabilities``."""

    name: str
    summary: str
    enabled: bool
    kind: str
    requires: tuple[str, ...]
    routes: tuple[str, ...]
    blocked_by: str | None = None

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> CapabilityRow:
        return cls(
            name=str(row["name"]),
            summary=str(row.get("summary", "")),
            enabled=bool(row["enabled"]),
            kind=str(row.get("kind", "surface")),
            requires=tuple(str(item) for item in row.get("requires", ())),
            routes=tuple(str(item) for item in row.get("routes", ())),
            blocked_by=None if row.get("blocked_by") is None else str(row["blocked_by"]),
        )


@dataclass(frozen=True, slots=True)
class Credential:
    """One row of ``GET /__unit/auth``: something a caller can actually present.

    ``headers`` is the whole instruction, so a check never has to know that a
    bearer scheme spells itself ``Authorization: Bearer``; it copies what the
    unit published onto the request and sees what happens.
    """

    label: str
    mode: str
    headers: Mapping[str, str]
    scopes: frozenset[str]
    summary: str = ""

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Credential:
        return cls(
            label=str(row["label"]),
            mode=str(row["mode"]),
            headers={str(name): str(value) for name, value in dict(row["headers"]).items()},
            scopes=frozenset(str(name) for name in row.get("scopes", ())),
            summary=str(row.get("summary", "")),
        )

    def covers(self, scopes: Sequence[str]) -> bool:
        """Whether presenting this would satisfy a route asking for ``scopes``."""
        return all(scope in self.scopes for scope in scopes)


@dataclass(frozen=True, slots=True)
class InBandTrigger:
    """How this vendor lets a consumer ask for a fault inside a normal request.

    Discovered from ``/__unit/info``'s ``magic`` block, so a vendor that spells
    its trigger as a query parameter and one that spells it as a header both
    get the same contracts asked of them. The query form is preferred where a
    vendor offers it because it disturbs nothing else about the request; a body
    path is the last resort, since writing into the body changes what the
    handler would otherwise have parsed.
    """

    prefix: str
    where: str
    field: str

    @property
    def describe(self) -> str:
        return f"{self.where} {self.field!r} carrying {self.prefix!r}"

    def request(self, fault: str) -> dict[str, Any]:
        """Keyword arguments for :meth:`ConformanceClient.call` that arm ``fault``."""
        value = f"{self.prefix}{fault}"
        if self.where == "query":
            return {"query": {self.field: value}, "json_body": {}}
        if self.where == "header":
            return {"headers": {self.field: value}, "json_body": {}}
        return {"json_body": _nested(self.field, value)}


class CheckEnv:
    """Everything one check may reach: a client, a profile name, and discovery.

    Discovery results that cannot change under a check -- the route table, the
    declared machines, the vendor's own description -- are memoised. Anything a
    check deliberately mutates (capabilities, chaos, state) is fetched every
    time, because a cached copy of a thing the check just changed is a fault
    injection of its own.
    """

    __slots__ = ("_cache", "client", "profile", "target", "transport")

    def __init__(
        self,
        *,
        target: ConformanceTarget,
        profile: str,
        transport: str,
        client: ConformanceClient,
    ) -> None:
        self.target = target
        self.profile = profile
        self.transport = transport
        self.client = client
        self._cache: dict[str, Any] = {}

    # -- raw access ---------------------------------------------------------

    def get_json(self, path: str) -> Any:
        """GET a control route and parse it, or fail naming the route.

        A control route that is missing is a failure of the unit and not of the
        check, and the message says which file publishes the route table.
        """
        res = self.client.call("GET", path)
        if res.status != 200:
            raise ConformanceFailure(
                f"GET {path} answered {res.status}, expected 200. Every control route in "
                f"core/control/plane.py must answer on every profile: they are declared "
                f"internal=True, so no capability can switch one off."
            )
        return res.json()

    def _memo(self, key: str, path: str) -> Any:
        if key not in self._cache:
            self._cache[key] = self.get_json(path)
        return self._cache[key]

    # -- discovery ----------------------------------------------------------

    def info(self) -> Mapping[str, Any]:
        document: Mapping[str, Any] = self._memo("info", f"{CONTROL_PREFIX}info")
        return document

    def routes(self) -> tuple[RouteRow, ...]:
        document = self._memo("routes", f"{CONTROL_PREFIX}routes")
        rows: Sequence[Mapping[str, Any]] = document["routes"]
        return tuple(RouteRow.of(row, self.target.path_params) for row in rows)

    def machines(self) -> Mapping[str, Any]:
        document = self._memo("machines", f"{CONTROL_PREFIX}machines")
        declared: Mapping[str, Any] = document["machines"]
        return declared

    def capabilities_document(self) -> Mapping[str, Any]:
        document: Mapping[str, Any] = self.get_json(f"{CONTROL_PREFIX}capabilities")
        return document

    def capabilities(self) -> tuple[CapabilityRow, ...]:
        rows: Sequence[Mapping[str, Any]] = self.capabilities_document()["capabilities"]
        return tuple(CapabilityRow.of(row) for row in rows)

    def auth_document(self) -> Mapping[str, Any]:
        document: Mapping[str, Any] = self.get_json(f"{CONTROL_PREFIX}auth")
        return document

    def credentials(self) -> tuple[Credential, ...]:
        """Every credential the unit says would authenticate right now.

        Fetched rather than memoised: a check that revoked a token and asked
        again must not be handed the answer from before it did so.
        """
        rows: Sequence[Mapping[str, Any]] = self.auth_document()["credentials"]
        return tuple(Credential.of(row) for row in rows)

    def state(self) -> Mapping[str, Any]:
        document: Mapping[str, Any] = self.get_json(f"{CONTROL_PREFIX}state")
        return document

    def chaos(self) -> Mapping[str, Any]:
        document: Mapping[str, Any] = self.get_json(f"{CONTROL_PREFIX}chaos")
        return document

    def deliveries(self) -> Sequence[Mapping[str, Any]]:
        document = self.get_json(f"{CONTROL_PREFIX}webhooks/deliveries")
        rows: Sequence[Mapping[str, Any]] = document["deliveries"]
        return rows

    # -- derived views ------------------------------------------------------

    def enabled_capability_names(self) -> frozenset[str]:
        return frozenset(row.name for row in self.capabilities() if row.enabled)

    def capability_enabled(self, name: str) -> bool:
        return any(row.name == name and row.enabled for row in self.capabilities())

    def capability_declared(self, name: str) -> bool:
        return any(row.name == name for row in self.capabilities())

    def set_capabilities(self, names: Sequence[str]) -> None:
        """Replace the enabled set. Used only by checks that restore it in a
        ``finally``; each check has its own unit, so nothing else can see it."""
        self.client.call("POST", f"{CONTROL_PREFIX}capabilities", json_body={"set": list(names)})

    def vendor_routes(
        self, *, methods: frozenset[str] | None = None, enabled_only: bool = True
    ) -> tuple[RouteRow, ...]:
        """Non-internal routes, optionally filtered to the ones a profile enables."""
        live = self.enabled_capability_names()
        return tuple(
            row
            for row in self.routes()
            if not row.internal
            and (methods is None or row.method in methods)
            and (not enabled_only or row.capability in live)
        )

    def first_vendor_route(
        self,
        *,
        methods: frozenset[str] | None = None,
        exclude_capability: str | None = None,
    ) -> RouteRow:
        for row in self.vendor_routes(methods=methods):
            if exclude_capability is not None and row.capability == exclude_capability:
                continue
            return row
        raise ConformanceSkip(
            f"profile {self.profile!r} enables no vendor route matching "
            f"{'any method' if methods is None else '/'.join(sorted(methods))}"
        )

    def first_mutating_route(self, *, exclude_capability: str | None = None) -> RouteRow:
        return self.first_vendor_route(methods=_MUTATING_METHODS, exclude_capability=exclude_capability)

    def auth_routes(self) -> tuple[RouteRow, ...]:
        """Enabled vendor routes that require a credential."""
        return tuple(row for row in self.vendor_routes() if row.auth is not None)

    def example_routes(
        self,
        *,
        methods: frozenset[str] | None = None,
        idempotent: bool = False,
    ) -> tuple[RouteRow, ...]:
        """Enabled vendor routes publishing a body they accept.

        The only way a language-independent check can cause a *successful*
        vendor mutation: a body a check assembled itself can only ever be
        refused by the vendor's own validation, and every contract about what a
        committed mutation does is unaskable until one has succeeded.
        """
        return tuple(
            row
            for row in self.vendor_routes(methods=methods)
            if row.example_body is not None and (not idempotent or row.idempotency is not None)
        )

    def first_example_route(
        self,
        *,
        methods: frozenset[str] | None = None,
        idempotent: bool = False,
    ) -> RouteRow:
        rows = self.example_routes(methods=methods, idempotent=idempotent)
        if not rows:
            raise ConformanceSkip(
                f"profile {self.profile!r} enables no route publishing an example_body"
                f"{' with an idempotency spec' if idempotent else ''}"
            )
        return rows[0]

    def idempotent_routes(self) -> tuple[RouteRow, ...]:
        """Enabled vendor routes that declare an idempotency spec, example or not."""
        return tuple(row for row in self.vendor_routes() if row.idempotency is not None)

    def partner_idempotent_route(self, route: RouteRow) -> RouteRow | None:
        """Another enabled idempotent route to send ``route``'s key to.

        A DIFFERENT declared scope first -- that is the pair the isolation
        contract wants -- and within each tier the one with an example body,
        because a key replayed into a request that then *succeeds* is stronger
        evidence than one refused by validation. A same-scope partner is still
        returned when no other scope exists: the check treats fully collapsed
        declarations as its own finding, so the selection must not hide them.
        """
        scope = None if route.idempotency is None else route.idempotency.get("scope")
        others = [row for row in self.idempotent_routes() if row.key != route.key]
        tiers = (
            [row for row in others if (row.idempotency or {}).get("scope") != scope and row.example_body is not None],
            [row for row in others if (row.idempotency or {}).get("scope") != scope],
            [row for row in others if row.example_body is not None],
            others,
        )
        return next((tier[0] for tier in tiers if tier), None)

    def paginated_routes(self) -> tuple[RouteRow, ...]:
        """Enabled vendor routes that declare how they page."""
        return tuple(row for row in self.vendor_routes() if row.pagination is not None)

    def credential_for(self, route: RouteRow) -> Credential:
        """A published credential that satisfies ``route``'s mode and scopes."""
        for credential in self.credentials():
            if credential.mode == route.auth and credential.covers(route.scopes):
                return credential
        raise ConformanceSkip(
            f"no credential published at /__unit/auth satisfies {route.key} "
            f"(mode {route.auth!r}, scopes {sorted(route.scopes)})"
        )

    def authorized(self, route: RouteRow) -> dict[str, str]:
        """Headers that authenticate ``route``, or ``{}`` if it needs none."""
        if route.auth is None:
            return {}
        return dict(self.credential_for(route).headers)

    def signer(self) -> Mapping[str, Any] | None:
        declared = self.info().get("signer")
        if declared is None:
            return None
        block: Mapping[str, Any] = declared
        return block

    def in_band_trigger(self) -> InBandTrigger:
        """The vendor's in-band trigger, in the form that disturbs least."""
        spec = self.info().get("magic")
        if spec is None:
            raise ConformanceSkip("the vendor declares no in-band (magic-value) trigger")
        prefix = str(spec["prefix"])
        for where, key in (("query", "query_params"), ("header", "headers"), ("body", "body_paths")):
            fields: Sequence[str] = spec.get(key, ())
            if fields:
                return InBandTrigger(prefix=prefix, where=where, field=str(fields[0]))
        raise ConformanceSkip("the vendor declares an in-band trigger prefix but no field it may appear in")

    # -- a second unit ------------------------------------------------------

    @contextmanager
    def fresh(self, *, transport: str | None = None) -> Iterator[CheckEnv]:
        """A second, freshly constructed unit on the same profile.

        Determinism is a claim about two units, not about one unit asked twice,
        so C06 and C08 need this. It is also how C10 reaches the other binding.
        """
        wanted = self.transport if transport is None else transport
        with self.target.open_client(self.profile, wanted) as client:
            yield CheckEnv(target=self.target, profile=self.profile, transport=wanted, client=client)

    @contextmanager
    def seed_overlay_unit(self, overlay: Mapping[str, Any]) -> Iterator[CheckEnv]:
        """A unit on the same profile with ``overlay`` laid over its seed.

        Raises whatever the target's construction raises, unwrapped -- which
        is the point: the seed-overlay contract is about a refusal that must
        happen while the unit is *built*, and a helper that turned it into a
        skip or an error would be answering the question on the check's
        behalf. A check calling this catches the exception itself.

        :class:`ConformanceSkip` if the target publishes no such opener,
        which the ``seed_overlay`` precondition also reports; the guard is
        repeated here so a check that reaches for this without declaring the
        precondition skips rather than raising ``AttributeError`` on ``None``.
        """
        opener = self.target.open_with_seed_overlay
        if opener is None:
            raise ConformanceSkip(
                "the target publishes no open_with_seed_overlay, so a unit carrying a seed overlay "
                "cannot be built to ask this of"
            )
        with opener(self.profile, overlay) as client:
            yield CheckEnv(target=self.target, profile=self.profile, transport=self.transport, client=client)


@contextmanager
def check_env(target: ConformanceTarget, profile: str, transport: str) -> Iterator[CheckEnv]:
    """One check's environment: its own unit, torn down when it is done."""
    with target.open_client(profile, transport) as client:
        yield CheckEnv(target=target, profile=profile, transport=transport, client=client)


def unmet_precondition(requires: Requires, env: CheckEnv) -> str | None:
    """The first unmet precondition, as the reason to print, or ``None``.

    Every branch resolves by asking the unit. The reason is prose a reader can
    act on, because a skip nobody can explain is indistinguishable from a
    contract nobody wrote.
    """
    if requires.surface_route and not env.vendor_routes():
        return f"profile {env.profile!r} enables no vendor route to probe"
    if requires.mutating_route and not env.vendor_routes(methods=_MUTATING_METHODS):
        return f"profile {env.profile!r} enables no mutating (POST/PUT) vendor route"
    if requires.signer and env.signer() is None:
        return "the vendor declares no webhook signer"
    if requires.signature_headers:
        signer = env.signer()
        bindings = {} if signer is None else signer.get("bindings", {})
        if not bindings.get("signature_headers"):
            return "the signer declares no signature headers, so no delivery header can be attributed to it"
    if requires.machines and not env.machines():
        return "the vendor declares no state machines"
    if requires.seed and not any(int(count) for count in env.state()["entities"].values()):
        return f"profile {env.profile!r} loads no seed entities"
    if requires.seed_overlay and env.target.open_with_seed_overlay is None:
        return (
            "the target publishes no open_with_seed_overlay, so no unit carrying a seed overlay "
            "can be built to ask this of (ConformanceTarget.open_with_seed_overlay)"
        )
    if requires.chaos and not env.capability_enabled(CoreCapability.CHAOS.value):
        return f"the {CoreCapability.CHAOS.value!r} capability is off in profile {env.profile!r}"
    if requires.webhooks:
        if not env.capability_enabled(CoreCapability.WEBHOOKS.value):
            return f"the {CoreCapability.WEBHOOKS.value!r} capability is off in profile {env.profile!r}"
        if not env.info()["webhooks"]["enabled"]:
            return f"webhook delivery is switched off in profile {env.profile!r}"
    if requires.memory_sink and env.info()["webhooks"]["sink"] != "memory":
        return (
            f"the delivery sink is {env.info()['webhooks']['sink']!r}; programming its answers, "
            f"and therefore forcing a retry from outside the process, needs the in-memory sink"
        )
    if requires.in_band_trigger and env.info().get("magic") is None:
        return "the vendor declares no in-band (magic-value) trigger"
    if requires.auth_route and not env.auth_routes():
        return f"profile {env.profile!r} enables no vendor route that requires a credential"
    if requires.credentials and not env.credentials():
        return (
            "GET /__unit/auth publishes no credential, so no check can send an authenticated "
            "request; the vendor's AuthAdapter.credentials() returned nothing"
        )
    if requires.example_body and not env.example_routes():
        return f"profile {env.profile!r} enables no route publishing an example_body"
    if requires.mutating_example and not env.example_routes(methods=_MUTATING_METHODS):
        return f"profile {env.profile!r} enables no POST/PUT route publishing an example_body"
    if requires.idempotent_example and not env.example_routes(methods=_MUTATING_METHODS, idempotent=True):
        return (
            f"profile {env.profile!r} enables no idempotent POST/PUT route publishing an "
            f"example_body, so no request can be sent twice under one key"
        )
    if requires.two_idempotent_routes:
        examples = env.example_routes(methods=_MUTATING_METHODS, idempotent=True)
        if not any(env.partner_idempotent_route(row) is not None for row in examples):
            return (
                f"profile {env.profile!r} enables no second idempotent route alongside an "
                f"example-bearing one, so no key can be sent to two operations"
            )
    if requires.paginated_route and not env.paginated_routes():
        return f"profile {env.profile!r} enables no vendor route declaring a pagination spec"
    if requires.webhooks_chaos and not env.capability_enabled(CoreCapability.WEBHOOKS_CHAOS.value):
        return f"the {CoreCapability.WEBHOOKS_CHAOS.value!r} capability is off in profile {env.profile!r}"
    if requires.virtual_clock:
        # ``.get`` and not ``[...]``: a unit that publishes no clock block at
        # all has not met the precondition either, and an unmet precondition is
        # a skip with a reason -- never a KeyError that the runner would have
        # to report as this contract failing. The contract that fails for a
        # missing documented key is C01, which is where it belongs.
        clock = env.info().get("clock") or {}
        if clock.get("mode") != "virtual":
            return (
                f"profile {env.profile!r} reports clock mode {clock.get('mode')!r}; observing a "
                f"declared delay without waiting for it needs the virtual one"
            )
    if requires.out_of_process and not env.target.out_of_process:
        return (
            f"target {env.target.name!r} declares no out-of-process transport, so a second unit "
            f"would be built in this same interpreter and could not witness anything drawn from "
            f"the process itself (set ConformanceTarget.out_of_process)"
        )
    if requires.both_transports and len(set(env.target.transports)) < 2:
        return (
            f"target {env.target.name!r} offers only the {env.transport!r} transport; "
            f"comparing two bindings needs a second one (pass transports=('inprocess', 'http'))"
        )
    return None
