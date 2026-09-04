"""Running the corpus: one fresh unit per case, every step in order, first failure wins.

FOR: being the framework-free façade over the corpus, exactly as
``conformance/runner.py`` is over the registry. The CLI and the pytest plugin
both come here; neither adds an assertion the other lacks.

INVARIANT: **each case gets its own freshly built unit.** A case's steps share
one unit -- that is what makes a two-step order-then-pay flow expressible --
but two cases never do, so case order is never load-bearing and a case that
mutates state cannot poison the next one. The target owns construction and
teardown through ``open_unit``; this module only asks.

SECOND INVARIANT: **the client is injectable, and the default one validates.**
``run_corpus`` builds a ``ValidatingClient`` over the target's declaration
and extract unless told otherwise, so the behaviour leg runs the contract
leg for free on every response it reads. ``validate=False`` -- and every
``--base-url`` run, where there is no unit object to validate through -- is
recorded in the report as such, never silently.

WHY THE TARGET IS NAMED AND NEVER GUESSED: the same layer rule as the
conformance package. This module may not import a vendor or the registry, so
``resolve_target("module:attr")`` is how a vendor is reached.
"""

from __future__ import annotations

import json
import os
import random
import uuid as _uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from vendorfake.core.kernel.router import Match, Router
from vendorfake.core.kernel.types import Route, SignInput
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import in_process
from vendorfake.fidelity.corpus import (
    AUTH_HEADER_KEY,
    MISSING,
    Case,
    InterpolationError,
    Step,
    absent_violations,
    interpolate,
    match,
    match_headers,
    resolve_pointer,
)
from vendorfake.fidelity.report import CaseResult, CorpusReport, StepFailure
from vendorfake.fidelity.types import FidelityDeclaration, load_declaration, load_extract, route_key

__all__ = [
    "CONTROL_PREFIX",
    "MANIFEST_CAVEAT",
    "MANIFEST_SCHEMA",
    "REMOTE_CAVEAT",
    "RESET_CAVEAT",
    "TARGET_ENV_VAR",
    "ClientFactory",
    "ControlPlaneWorld",
    "CorpusClient",
    "CorpusResponse",
    "FidelityTarget",
    "HttpCorpusClient",
    "ManifestWorld",
    "Opener",
    "World",
    "modeled_routes",
    "resolve_target",
    "run_case",
    "run_corpus",
    "run_corpus_remote",
    "world_opener",
]

TARGET_ENV_VAR = "VENDORFAKE_FIDELITY_TARGET"
"""Where the CLI and the pytest plugin look for a target when no flag names one."""

CONTROL_PREFIX = "/__unit/"

MANIFEST_SCHEMA = "vendorfake.manifest/1"
"""The document :class:`ManifestWorld` reads. See ``docs/reference/manifest.md``."""

REMOTE_CAVEAT = (
    "a unit reached over --base-url is SHARED, not rebuilt per case, and its responses are NOT validated "
    "against the schema: validation needs the unit object, and a base URL is a socket. Point this at a "
    "throwaway container."
)

RESET_CAVEAT = "state is reset before every case, so cases still start from the seed"

MANIFEST_CAVEAT = "no reset against this world; cases run against whatever state the account holds"


# ---------------------------------------------------------------------------
# What the runner needs of a client, and the two clients that provide it.
# ---------------------------------------------------------------------------


class _HasBody(Protocol):
    @property
    def body(self) -> bytes: ...


class CorpusResponse(Protocol):
    """The three things a step reads: status, headers, and the exact bytes."""

    @property
    def status(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def raw(self) -> _HasBody: ...


class CorpusClient(Protocol):
    """``InProcessClient.call``'s keyword signature, which is all a step uses."""

    def call(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: object = None,
    ) -> CorpusResponse: ...


@dataclass(frozen=True, slots=True)
class _RawBody:
    body: bytes


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status: int
    headers: Mapping[str, str]
    raw: _RawBody


class HttpCorpusClient:
    """The same ``call()`` over a base URL. Never a server: whoever has one passes its address."""

    __slots__ = ("_client",)

    def __init__(self, base_url: str, *, timeout_s: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)

    def call(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: object = None,
    ) -> _HttpResponse:
        sent = dict(headers or {})
        content: bytes | None = None
        if body is not None:
            content = json.dumps(body, separators=(",", ":")).encode("utf-8")
            sent.setdefault("content-type", "application/json")
        answered = self._client.request(
            method.upper(), path, params=dict(query) if query else None, headers=sent, content=content
        )
        return _HttpResponse(
            status=answered.status_code,
            headers={key.lower(): value for key, value in answered.headers.items()},
            raw=_RawBody(answered.content),
        )

    def close(self) -> None:
        self._client.close()


ClientFactory = Callable[[Unit], CorpusClient]
Opener = Callable[[str], AbstractContextManager[CorpusClient]]


# ---------------------------------------------------------------------------
# The target.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FidelityTarget:
    """What a vendor points the corpus at.

    ``open_unit(profile)`` MUST yield a *freshly constructed* unit on every
    call and stop it on exit; ``None`` asks for ``default_profile``.
    ``anchor`` names the package holding ``declaration.json``,
    ``extract.json`` and ``corpus/``.
    """

    name: str
    anchor: str
    open_unit: Callable[[str | None], AbstractContextManager[Unit]]
    default_profile: str = "full"
    #: The vendor's ``Signer.sign``, for ``webhooks`` to check a captured delivery against. ``None`` means this target
    #: makes no signing claim, and the subcommand refuses rather than guessing a scheme.
    signer: Callable[[SignInput], Mapping[str, str]] | None = None


def resolve_target(spec: str) -> FidelityTarget:
    """``my_package.testing:fidelity_target`` -> the target, or the result of calling it."""
    module_name, _, attribute = spec.partition(":")
    found = getattr(import_module(module_name), attribute or "target")
    if isinstance(found, FidelityTarget):
        return found
    if callable(found):
        built = found()
        if isinstance(built, FidelityTarget):
            return built
    raise LookupError(
        f"{spec} is {type(found).__name__}, not a FidelityTarget or a callable returning one. "
        f"Publish a FidelityTarget -- see vendorfake.fidelity.runner.FidelityTarget."
    )


def target_from_env() -> str | None:
    return os.environ.get(TARGET_ENV_VAR)


def modeled_routes(routes: Sequence[Route], declaration: FidelityDeclaration) -> tuple[tuple[str, str], ...]:
    """``(METHOD, spec_path)`` for every vendor route, aliases applied, sorted.

    Internal routes and the control plane are not modeled. Excused routes
    *are* included: the extract's ``missing`` list is where they belong, and
    it is the pin that says so, not this function."""
    out: set[tuple[str, str]] = set()
    for route in routes:
        if route.internal or route.path.startswith("/__"):
            continue
        alias = declaration.alias_for(route.method, route.path)
        out.add((route.method.upper(), alias.spec_path if alias is not None else route.path))
    return tuple(sorted(out))


# ---------------------------------------------------------------------------
# The world a case runs in.
# ---------------------------------------------------------------------------


class World(Protocol):
    """The three questions a case asks of its surroundings: which profile am I on, put the state back, which credentials
    work. The control plane answers all three, which is why the base-URL runner was written against it and could only
    ever address a vendorfake. Named as a seam, the same cases run against a *sandbox account*, where a manifest answers
    and ``reset`` is not on offer."""

    def profile(self) -> str: ...

    def reset(self) -> None: ...

    def credentials(self) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class _Caveating(Protocol):
    """A world with something the report must print about what it could not do."""

    def caveats(self) -> Sequence[str]: ...


def _caveats_of(world: World) -> tuple[str, ...]:
    return tuple(str(line) for line in world.caveats()) if isinstance(world, _Caveating) else ()


class ControlPlaneWorld:
    """A running unit: ``/__unit/info``, ``/__unit/state/reset``, ``/__unit/auth``. The profile is DISCOVERED, never
    asserted, and read once -- a unit's profile does not change under it."""

    __slots__ = ("_base_url", "_client", "_profile")

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = HttpCorpusClient(base_url)
        self._profile: str | None = None

    def profile(self) -> str:
        if self._profile is None:
            self._profile = str(json.loads(self._get("info").raw.body)["profile"])
        return self._profile

    def reset(self) -> None:
        answered = self._client.call(method="POST", path=f"{CONTROL_PREFIX}state/reset", body={})
        if answered.status // 100 != 2:
            raise RuntimeError(
                f"POST {CONTROL_PREFIX}state/reset answered {answered.status} while resetting the shared unit; "
                f"the next case would read state an earlier one left behind"
            )

    def credentials(self) -> Sequence[Mapping[str, Any]]:
        rows = json.loads(self._get("auth").raw.body).get("credentials", [])
        return [row for row in rows if isinstance(row, Mapping)]

    def caveats(self) -> Sequence[str]:
        return (RESET_CAVEAT,)

    def close(self) -> None:
        self._client.close()

    def _get(self, name: str) -> CorpusResponse:
        try:
            answered = self._client.call(method="GET", path=f"{CONTROL_PREFIX}{name}")
        except httpx.HTTPError as exc:
            raise LookupError(f"cannot reach a unit at {self._base_url}: {type(exc).__name__}: {exc}") from exc
        if answered.status != 200:
            raise LookupError(
                f"GET {self._base_url}{CONTROL_PREFIX}{name} answered {answered.status}, expected 200. "
                f"--base-url must address a running unit, whose control plane answers on every profile."
            )
        return answered


class ManifestWorld:
    """The world a ``vendorfake.manifest/1`` document describes: the profile, the credentials in ``/__unit/auth``'s own
    shape, and the base URL. That is exactly the subset of the control plane a case needs and the subset a real sandbox
    account can also produce (``docs/reference/manifest.md``). What it cannot carry is a reset, so :meth:`reset` does
    nothing and says so."""

    __slots__ = ("_credentials", "_profile", "base_url", "path")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise LookupError(f"cannot read the manifest {self.path}: {exc}") from exc
        except ValueError as exc:
            raise LookupError(f"{self.path}: not JSON: {exc}") from exc
        if not isinstance(document, Mapping):
            raise LookupError(f"{self.path}: a manifest is an object, not {type(document).__name__}")
        schema = document.get("schema")
        if schema != MANIFEST_SCHEMA:
            raise LookupError(f"{self.path}: schema is {schema!r}, expected {MANIFEST_SCHEMA!r}")
        profile = document.get("profile")
        if not profile:
            raise LookupError(f"{self.path}: no profile; a case runs on a named profile or none at all")
        self._profile = str(profile)
        rows = document.get("credentials", [])
        self._credentials: tuple[Mapping[str, Any], ...] = tuple(row for row in rows if isinstance(row, Mapping))
        url = document.get("base_url")
        #: The address the manifest was written for, or ``None``; ``--base-url`` wins, outliving that port.
        self.base_url: str | None = None if url is None else str(url)

    def profile(self) -> str:
        return self._profile

    def reset(self) -> None:
        """Nothing. See the class docstring and :data:`MANIFEST_CAVEAT`."""

    def credentials(self) -> Sequence[Mapping[str, Any]]:
        return self._credentials

    def caveats(self) -> Sequence[str]:
        return (MANIFEST_CAVEAT,)


class _ClientWorld:
    """The unit a client is already talking to. In process there is no second address: the client *is* the way in, so
    only ``credentials`` is ever called -- the caller chose the profile and ``open_unit`` promised the freshness."""

    __slots__ = ("_client", "_profile")

    def __init__(self, client: CorpusClient, profile: str) -> None:
        self._client = client
        self._profile = profile

    def profile(self) -> str:
        return self._profile

    def reset(self) -> None:
        """Nothing: ``open_unit`` already built a unit nobody else has touched."""

    def credentials(self) -> Sequence[Mapping[str, Any]]:
        answered = self._client.call(method="GET", path=f"{CONTROL_PREFIX}auth")
        if answered.status != 200:
            raise RuntimeError(f"GET {CONTROL_PREFIX}auth answered {answered.status}; a $auth header needs it")
        rows = json.loads(answered.raw.body).get("credentials", [])
        return [row for row in rows if isinstance(row, Mapping)]


# ---------------------------------------------------------------------------
# Running.
# ---------------------------------------------------------------------------


def run_corpus(
    target: FidelityTarget,
    cases: Sequence[Case],
    *,
    profile_override: str | None = None,
    validate: bool = True,
    client_factory: ClientFactory | None = None,
) -> CorpusReport:
    """Every case, each against its own fresh unit.

    ``client_factory`` is the seam: ``None`` means the validating client
    (imported here, not at module load, so the corpus is usable without the
    validator) when ``validate``, else the plain in-process client. A caller
    that injects a factory is making its own claim about validation, which
    ``validate`` records.
    """
    declaration = load_declaration(target.anchor)
    ledger: Any = None
    factory: ClientFactory
    if client_factory is not None:
        factory = client_factory
    elif validate:
        from vendorfake.fidelity.types import Surface
        from vendorfake.fidelity.validate import Ledger, ValidatingClient

        surface = Surface(declaration, load_extract(target.anchor))
        ledger = Ledger()

        def factory(unit: Unit) -> CorpusClient:
            # Lenient on an undeclared route: the case still runs, the ledger
            # counts it, and the matrix prints it in capitals and exits 1. A
            # raise mid-case would hide every case after it.
            return ValidatingClient(unit, surface, ledger, strict_undeclared=False)

    else:
        factory = in_process

    @contextmanager
    def opener(profile: str) -> Iterator[CorpusClient]:
        with target.open_unit(profile) as unit:
            yield ObservingClient(factory(unit), Router(unit.routes))

    results = tuple(
        run_case(case, opener, profile=_profile(case, target, profile_override), variables=declaration.variables)
        for case in cases
    )
    # A caller that injects its own client is making its own claim about
    # validation; this report does not repeat it as ours.
    return CorpusReport(
        target=target.name, results=results, validated=validate and client_factory is None, ledger=ledger
    )


class ObservingClient:
    """The client a case runs against, remembering which routes its steps reached.

    The matrix attributes a case's coverage to the routes its requests
    actually matched, not to the routes the case file says it covers; the
    declared list is checked against this and a case that names a route no
    step reached fails. Matching is the kernel's own router over the unit's
    own table, on the bare path, so it agrees with what the unit did.
    """

    __slots__ = ("_client", "_router", "observed")

    def __init__(self, client: CorpusClient, router: Router) -> None:
        self._client = client
        self._router = router
        self.observed: list[str] = []

    def call(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: object = None,
    ) -> CorpusResponse:
        outcome = self._router.match(method, path.partition("?")[0])
        if isinstance(outcome, Match) and not outcome.route.internal and not outcome.route.path.startswith("/__"):
            key = route_key(outcome.route.method, outcome.route.path)
            if key not in self.observed:
                self.observed.append(key)
        return self._client.call(method=method, path=path, query=query, headers=headers, body=body)


def _profile(case: Case, target: FidelityTarget, override: str | None) -> str:
    return override or case.profile or target.default_profile


def world_opener(base_url: str, world: World) -> Opener:
    """A fresh HTTP client per case over ``base_url``, the world reset first. The reset is the world's to define, and
    one that cannot says so in a caveat rather than pretending the state is fresh."""

    @contextmanager
    def opener(_profile: str) -> Iterator[CorpusClient]:
        client = HttpCorpusClient(base_url)
        try:
            world.reset()
            yield client
        finally:
            client.close()

    return opener


def run_corpus_remote(
    base_url: str,
    anchor: str,
    cases: Sequence[Case],
    *,
    world: World | None = None,
) -> CorpusReport:
    """``--base-url``: the corpus over HTTP, unvalidated, and the report says so. ``world`` supplies the profile, the
    reset and the credentials, defaulting to the control plane at ``base_url``. A world this call builds it also
    closes; one handed in belongs to its caller, who may run a second corpus through it."""
    if world is not None:
        return _run_remote(base_url, anchor, cases, world)
    control = ControlPlaneWorld(base_url)
    try:
        return _run_remote(base_url, anchor, cases, control)
    finally:
        control.close()


def _run_remote(base_url: str, anchor: str, cases: Sequence[Case], world: World) -> CorpusReport:
    declaration = load_declaration(anchor)
    opener = world_opener(base_url, world)
    profile = world.profile()
    results = tuple(
        run_case(case, opener, profile=profile, variables=declaration.variables, world=world) for case in cases
    )
    return CorpusReport(
        target=base_url,
        results=results,
        validated=False,
        remote=True,
        caveats=(REMOTE_CAVEAT, *_caveats_of(world)),
    )


def run_case(
    case: Case,
    opener: Opener,
    *,
    profile: str,
    variables: Mapping[str, str],
    world: World | None = None,
) -> CaseResult:
    """One case: open a client, run every step in order, stop at the first failure.

    ``${uuid}`` values are drawn from a generator seeded with the case id, so
    two runs of the same case send the same ids -- a corpus is a reproducible
    statement, and a diff between two runs should be a diff in the unit.

    ``$auth`` is resolved from ``world.credentials()``; ``None`` means the unit
    the opener just yielded a client for.
    """
    rng = random.Random(case.id)

    def fresh_uuid() -> str:
        return str(_uuid.UUID(int=rng.getrandbits(128), version=4))

    captures: dict[str, Any] = {}
    auth_rows: list[Mapping[str, Any]] | None = None
    steps_run = 0
    failure: StepFailure | None = None
    try:
        with opener(profile) as client:
            here: World = world if world is not None else _ClientWorld(client, profile)
            for step in case.steps:
                steps_run += 1
                try:
                    request = interpolate(
                        {
                            "path": step.request.path,
                            "headers": dict(step.request.headers),
                            "query": dict(step.request.query),
                            "body": step.request.body,
                        },
                        variables=variables,
                        captures=captures,
                        uuid=fresh_uuid,
                    )
                    expected = interpolate(
                        {"headers": dict(step.expect.headers), "body": step.expect.body},
                        variables=variables,
                        captures=captures,
                        uuid=fresh_uuid,
                    )
                except InterpolationError as exc:
                    failure = StepFailure(step.name, "request", "a resolvable reference", str(exc), kind="request")
                    break

                headers: dict[str, str] = request["headers"]
                if AUTH_HEADER_KEY in headers:
                    mode = headers.pop(AUTH_HEADER_KEY)
                    if auth_rows is None:
                        auth_rows = list(here.credentials())
                    credential = next((row for row in auth_rows if str(row.get("mode")) == mode), None)
                    if credential is None:
                        offered = sorted({str(row.get("mode")) for row in auth_rows})
                        failure = StepFailure(
                            step.name,
                            f"request/headers/{AUTH_HEADER_KEY}",
                            f"a credential of mode {mode!r}",
                            f"modes this world publishes: {offered}",
                            kind="request",
                        )
                        break
                    headers = {**{str(k): str(v) for k, v in dict(credential["headers"]).items()}, **headers}

                try:
                    response = client.call(
                        method=step.request.method,
                        path=request["path"],
                        query=request["query"] or None,
                        headers=headers or None,
                        body=request["body"] if step.request.has_body else None,
                    )
                except Exception as exc:
                    # A validating client refuses a body the extract forbids by raising: the contract leg speaking,
                    # a divergence of its own class rather than an unanswered request.
                    errors = _violation_errors(exc)
                    failure = StepFailure(
                        step.name,
                        "response",
                        "an answer",
                        f"{type(exc).__name__}",
                        detail=("\n".join(errors)[:1200] if errors else str(exc)[:1200]),
                        kind="request" if errors is None else "schema",
                    )
                    break

                failure = _check_step(step, expected, response, captures)
                if failure is not None:
                    break

            observed = tuple(getattr(client, "observed", ()))
            if failure is None and observed:
                unreached = [key for key in case.routes if key not in observed]
                if unreached:
                    failure = StepFailure(
                        "routes",
                        "routes",
                        "every declared route reached by a step",
                        f"never reached: {', '.join(unreached)}; reached: {', '.join(observed)}",
                        kind="missing",  # A route the case claims is missing from what the steps reached.
                    )

    except RuntimeError as exc:
        # The unit itself could not be opened, reset or asked for credentials
        # -- a control-plane failure, not a vendor fact. One failed case with
        # the reason, and the run goes on to the next.
        failure = StepFailure(
            "open",
            "unit",
            "a unit to run the case against",
            f"{type(exc).__name__}: {exc}"[:600],
            kind="request",
        )
        observed = ()

    return CaseResult(
        id=case.id,
        title=case.title,
        provenance=case.provenance,
        routes=case.routes,
        observed=observed,
        profile=profile,
        passed=failure is None,
        failure=failure,
        steps_run=steps_run,
    )


def _violation_errors(exc: BaseException) -> tuple[str, ...] | None:
    """The contract leg's own errors when ``exc`` is one of its refusals, else ``None``. Imported here, not at module
    scope: the corpus stays runnable without the validator."""
    try:
        from vendorfake.fidelity.validate import FidelityViolation
    except ImportError:  # pragma: no cover -- the validator ships with this package
        return None
    return exc.errors if isinstance(exc, FidelityViolation) else None


def _check_step(
    step: Step, expected: Mapping[str, Any], response: CorpusResponse, captures: dict[str, Any]
) -> StepFailure | None:
    """The first expectation of one step that does not hold, CLASSIFIED. The class separates "the unit is wrong" from
    "the unit is silent": ``OPEN`` for ``COMPLETED`` is a ``value``, the field absent is ``missing``, a field the case
    said would not be there is ``unexpected``. A tally of those reads as a diagnosis; a tally of "13 failed" does
    not."""
    raw = response.raw.body
    if response.status != step.expect.status:
        return StepFailure(
            step.name, "status", step.expect.status, response.status, detail=f"body: {_excerpt(raw)}", kind="status"
        )
    mismatch = match_headers(expected["headers"], response.headers)
    if mismatch is not None:
        return StepFailure(step.name, mismatch.pointer.lstrip("/"), mismatch.expected, mismatch.actual, kind="header")

    needs_body = step.expect.has_body or bool(step.expect.absent) or bool(step.capture)
    if not needs_body:
        return None
    if not raw:
        body: Any = MISSING
    else:
        try:
            body = json.loads(raw)
        except ValueError:
            return StepFailure(step.name, "body", "a JSON body", f"not JSON: {_excerpt(raw)}", kind="value")

    if step.expect.has_body:
        mismatch = match(expected["body"], body)
        if mismatch is not None:
            return StepFailure(
                step.name,
                mismatch.pointer or "/",
                mismatch.expected,
                mismatch.actual,
                kind="missing" if mismatch.actual is MISSING else "value",
            )
    mismatch = absent_violations(body, step.expect.absent)
    if mismatch is not None:
        return StepFailure(step.name, mismatch.pointer, "absent", mismatch.actual, kind="unexpected")
    for name, pointer in step.capture.items():
        value = resolve_pointer(body, pointer)
        if value is MISSING:
            return StepFailure(step.name, f"capture/{name}", f"a value at {pointer}", MISSING, kind="capture")
        captures[name] = value
    return None


def _excerpt(raw: bytes, limit: int = 400) -> str:
    text = raw.decode("utf-8", errors="replace")
    return text if len(text) <= limit else text[:limit] + "..."
