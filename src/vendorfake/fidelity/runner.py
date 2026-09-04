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
from typing import Any, Protocol

import httpx

from vendorfake.core.kernel.router import Match, Router
from vendorfake.core.kernel.types import Route
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
    "REMOTE_CAVEAT",
    "TARGET_ENV_VAR",
    "ClientFactory",
    "CorpusClient",
    "CorpusResponse",
    "FidelityTarget",
    "HttpCorpusClient",
    "Opener",
    "modeled_routes",
    "remote_opener",
    "resolve_target",
    "run_case",
    "run_corpus",
    "run_corpus_remote",
]

TARGET_ENV_VAR = "VENDORFAKE_FIDELITY_TARGET"
"""Where the CLI and the pytest plugin look for a target when no flag names one."""

CONTROL_PREFIX = "/__unit/"

REMOTE_CAVEAT = (
    "a unit reached over --base-url is SHARED, not rebuilt per case, and its responses are NOT validated "
    "against the schema: validation needs the unit object, and a base URL is a socket. State is reset "
    "before every case, so cases still start from the seed. Point this at a throwaway container."
)


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


def remote_opener(base_url: str) -> tuple[Opener, str]:
    """An opener over a unit somebody else is running, and the profile it reports.

    The profile is DISCOVERED from ``GET /__unit/info``; state is reset before
    every case so each starts from the seed, which is the nearest a shared
    unit gets to a fresh one. See :data:`REMOTE_CAVEAT`.
    """
    probe = HttpCorpusClient(base_url)
    try:
        try:
            answered = probe.call(method="GET", path=f"{CONTROL_PREFIX}info")
        except httpx.HTTPError as exc:
            raise LookupError(f"cannot reach a unit at {base_url}: {type(exc).__name__}: {exc}") from exc
        if answered.status != 200:
            raise LookupError(
                f"GET {base_url.rstrip('/')}{CONTROL_PREFIX}info answered {answered.status}, expected 200. "
                f"--base-url must address a running unit, whose control plane answers on every profile."
            )
        profile = str(json.loads(answered.raw.body)["profile"])
    finally:
        probe.close()

    @contextmanager
    def opener(_profile: str) -> Iterator[CorpusClient]:
        client = HttpCorpusClient(base_url)
        try:
            reset = client.call(method="POST", path=f"{CONTROL_PREFIX}state/reset", body={})
            if reset.status // 100 != 2:
                raise RuntimeError(
                    f"POST {CONTROL_PREFIX}state/reset answered {reset.status} while resetting the shared unit; "
                    f"the next case would read state an earlier one left behind"
                )
            yield client
        finally:
            client.close()

    return opener, profile


def run_corpus_remote(
    base_url: str,
    anchor: str,
    cases: Sequence[Case],
) -> CorpusReport:
    """``--base-url``: the corpus over HTTP, unvalidated, and the report says so."""
    declaration = load_declaration(anchor)
    opener, profile = remote_opener(base_url)
    results = tuple(run_case(case, opener, profile=profile, variables=declaration.variables) for case in cases)
    return CorpusReport(target=base_url, results=results, validated=False, remote=True, caveats=(REMOTE_CAVEAT,))


def run_case(
    case: Case,
    opener: Opener,
    *,
    profile: str,
    variables: Mapping[str, str],
) -> CaseResult:
    """One case: open a client, run every step in order, stop at the first failure.

    ``${uuid}`` values are drawn from a generator seeded with the case id, so
    two runs of the same case send the same ids -- a corpus is a reproducible
    statement, and a diff between two runs should be a diff in the unit.
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
                    failure = StepFailure(step.name, "request", "a resolvable reference", str(exc))
                    break

                headers: dict[str, str] = request["headers"]
                if AUTH_HEADER_KEY in headers:
                    mode = headers.pop(AUTH_HEADER_KEY)
                    if auth_rows is None:
                        auth_rows = _auth_rows(client)
                    credential = next((row for row in auth_rows if str(row.get("mode")) == mode), None)
                    if credential is None:
                        offered = sorted({str(row.get("mode")) for row in auth_rows})
                        failure = StepFailure(
                            step.name,
                            f"request/headers/{AUTH_HEADER_KEY}",
                            f"a credential of mode {mode!r}",
                            f"modes published by GET {CONTROL_PREFIX}auth: {offered}",
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
                    failure = StepFailure(
                        step.name, "response", "an answer", f"{type(exc).__name__}", detail=str(exc)[:1200]
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
                    )

    except RuntimeError as exc:
        # The unit itself could not be opened, reset or asked for credentials
        # -- a control-plane failure, not a vendor fact. One failed case with
        # the reason, and the run goes on to the next.
        failure = StepFailure("open", "unit", "a unit to run the case against", f"{type(exc).__name__}: {exc}"[:600])
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


def _auth_rows(client: CorpusClient) -> list[Mapping[str, Any]]:
    answered = client.call(method="GET", path=f"{CONTROL_PREFIX}auth")
    if answered.status != 200:
        raise RuntimeError(f"GET {CONTROL_PREFIX}auth answered {answered.status}; a $auth header needs it")
    document = json.loads(answered.raw.body)
    rows = document.get("credentials", [])
    return [row for row in rows if isinstance(row, Mapping)]


def _check_step(
    step: Step, expected: Mapping[str, Any], response: CorpusResponse, captures: dict[str, Any]
) -> StepFailure | None:
    raw = response.raw.body
    if response.status != step.expect.status:
        return StepFailure(step.name, "status", step.expect.status, response.status, detail=f"body: {_excerpt(raw)}")
    mismatch = match_headers(expected["headers"], response.headers)
    if mismatch is not None:
        return StepFailure(step.name, mismatch.pointer.lstrip("/"), mismatch.expected, mismatch.actual)

    needs_body = step.expect.has_body or bool(step.expect.absent) or bool(step.capture)
    if not needs_body:
        return None
    if not raw:
        body: Any = MISSING
    else:
        try:
            body = json.loads(raw)
        except ValueError:
            return StepFailure(step.name, "body", "a JSON body", f"not JSON: {_excerpt(raw)}")

    if step.expect.has_body:
        mismatch = match(expected["body"], body)
        if mismatch is not None:
            return StepFailure(step.name, mismatch.pointer or "/", mismatch.expected, mismatch.actual)
    mismatch = absent_violations(body, step.expect.absent)
    if mismatch is not None:
        return StepFailure(step.name, mismatch.pointer, "absent", mismatch.actual)
    for name, pointer in step.capture.items():
        value = resolve_pointer(body, pointer)
        if value is MISSING:
            return StepFailure(step.name, f"capture/{name}", f"a value at {pointer}", MISSING)
        captures[name] = value
    return None


def _excerpt(raw: bytes, limit: int = 400) -> str:
    text = raw.decode("utf-8", errors="replace")
    return text if len(text) <= limit else text[:limit] + "..."
