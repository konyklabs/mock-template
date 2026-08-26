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
than accidentally succeeding and mutating state a later assertion reads.

WHAT A CHECK MAY IMPORT FROM THE CORE. The core's own vocabulary: the error
kinds it raises and the capabilities it gates on. Those are the contract; a
check asserting on them is asserting on the specification. What a check may
never do is reach a unit object -- which is why this module holds a client and
a profile name and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from vendorfake.conformance.client import ConformanceClient
from vendorfake.conformance.types import ConformanceFailure, ConformanceSkip, ConformanceTarget, Requires
from vendorfake.core.capability.gates import CoreCapability

__all__ = [
    "PROBE_SEGMENT",
    "CapabilityRow",
    "CheckEnv",
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


def concrete_path(template: str) -> str:
    """``/v2/orders/{order_id}`` -> ``/v2/orders/conformance-probe``."""
    return "/".join(
        PROBE_SEGMENT if segment.startswith("{") and segment.endswith("}") else segment
        for segment in template.split("/")
    )


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
    operation_id: str | None = None

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> RouteRow:
        return cls(
            method=str(row["method"]).upper(),
            path=str(row["path"]),
            capability=str(row["capability"]),
            internal=bool(row.get("internal", False)),
            auth=None if row.get("auth") is None else str(row["auth"]),
            operation_id=None if row.get("operation_id") is None else str(row["operation_id"]),
        )

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def probe_path(self) -> str:
        return concrete_path(self.path)


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
        return tuple(RouteRow.of(row) for row in rows)

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
    if requires.both_transports and len(set(env.target.transports)) < 2:
        return (
            f"target {env.target.name!r} offers only the {env.transport!r} transport; "
            f"comparing two bindings needs a second one (pass transports=('inprocess', 'http'))"
        )
    return None
