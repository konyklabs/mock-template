"""The corpus runner against a synthetic vendor: captures, failures, ``$auth``, fresh units.

Also home to the synthetic vendor, the synthetic anchor package and the
target factory that ``test_cli.py`` and ``tests/fidelity/test_plugin.py``
reuse -- one description of the fake, not three.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import make_unit, route
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.reply import json_, text
from vendorfake.core.kernel.types import Route
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import in_process
from vendorfake.fidelity.corpus import MISSING
from vendorfake.fidelity.runner import (
    MANIFEST_CAVEAT,
    FidelityTarget,
    ManifestWorld,
    modeled_routes,
    resolve_target,
    run_case,
    run_corpus,
    run_corpus_remote,
    world_opener,
)
from vendorfake.fidelity.types import FidelityDeclaration

# ---------------------------------------------------------------------------
# The synthetic vendor and its fidelity data.
# ---------------------------------------------------------------------------

SOURCE_URL = "https://example.test/api.json"

DECLARATION: dict[str, Any] = {
    "schema": 1,
    "sources": [{"kind": "openapi3", "url": SOURCE_URL}],
    "error_envelope": "200",
    "error_member": "errors",
    "excused": [{"method": "GET", "path": "/v2/plain", "reason": "a text route the spec never had"}],
    "variables": {"location_id": "LOC_1"},
}

_ORDER_RESPONSE = {
    "description": "200",
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OrderResponse"}}},
}

EXTRACT: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {"title": "synthetic (scoped extract)", "version": "1.0"},
    "paths": {
        "/v2/orders": {"post": {"operationId": "CreateOrder", "responses": {"200": _ORDER_RESPONSE}}},
        "/v2/orders/{order_id}": {"get": {"operationId": "RetrieveOrder", "responses": {"200": _ORDER_RESPONSE}}},
        "/v2/whoami": {
            "get": {
                "operationId": "WhoAmI",
                "responses": {"200": {"description": "200", "content": {"application/json": {"schema": {}}}}},
            }
        },
    },
    "components": {
        "schemas": {
            "OrderResponse": {
                "type": "object",
                "properties": {
                    "order": {"$ref": "#/components/schemas/Order"},
                    "errors": {"type": "array", "items": {"type": "object"}},
                },
            },
            "Order": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "state": {"type": "string", "enum": ["OPEN", "COMPLETED"]},
                    "location_id": {"type": "string"},
                },
            },
        }
    },
    "x-vendorfake": {
        "schema": 1,
        "sources": [
            {
                "url": SOURCE_URL,
                "sha256": "abcdef0123456789abcdef",
                "bytes": 10,
                "version": "2.0",
                "fetched": "2026-09-02",
            }
        ],
        "modeled": [],
        "missing": [],
        "stubbed": [],
        "stripped": [],
    },
}


def synthetic_routes() -> tuple[Route, ...]:
    """A vendor with an order store per call, so every unit starts empty."""
    orders: dict[str, dict[str, Any]] = {}

    def create(args: Any) -> Any:
        body = args.json()
        order = body.get("order") or {}
        if "location_id" not in order:
            return json_(
                {
                    "errors": [
                        {
                            "category": "INVALID_REQUEST_ERROR",
                            "code": "MISSING_REQUIRED_PARAMETER",
                            "field": "order.location_id",
                        }
                    ]
                },
                status=400,
            )
        order_id = f"ord_{len(orders) + 1}"
        stored = {
            "id": order_id,
            "state": "OPEN",
            "location_id": order["location_id"],
            "idempotency_key": body.get("idempotency_key"),
            "total": {"amount": 100, "currency": "USD"},
            "line_items": [{"name": "coffee"}],
        }
        orders[order_id] = stored
        return json_({"order": stored})

    def retrieve(args: Any) -> Any:
        order_id = args.params["order_id"]
        if order_id not in orders:
            return json_({"errors": [{"code": "NOT_FOUND"}]}, status=404)
        return json_({"order": orders[order_id]})

    def whoami(args: Any) -> Any:
        return json_({"auth": args.req.headers.get("authorization"), "query": dict(args.req.query)})

    def plain(args: Any) -> Any:
        return text("plain body")

    def undeclared(args: Any) -> Any:
        return json_({"ok": True})

    return (
        route("POST", "/v2/orders", create),
        route("GET", "/v2/orders/{order_id}", retrieve),
        route("GET", "/v2/whoami", whoami),
        route("GET", "/v2/plain", plain),
        route("GET", "/v2/undeclared", undeclared),
    )


@contextmanager
def open_synthetic_unit(profile: str | None) -> Iterator[Unit]:
    unit = make_unit(synthetic_routes(), control_routes=control_plane_routes, profile=profile or "test")
    try:
        yield unit  # type: ignore[misc]
    finally:
        unit.stop()  # type: ignore[attr-defined]


def synthetic_target(anchor: str) -> FidelityTarget:
    return FidelityTarget(name="synthetic", anchor=anchor, open_unit=open_synthetic_unit, default_profile="test")


def make_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cases: Sequence[Mapping[str, Any]],
    *,
    declaration: Mapping[str, Any] = DECLARATION,
    extract: Mapping[str, Any] | None = EXTRACT,
) -> str:
    """A throwaway importable package holding the fidelity data; returns its name."""
    name = f"synthetic_anchor_{uuid.uuid4().hex[:8]}"
    package = tmp_path / name
    (package / "corpus").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "declaration.json").write_text(json.dumps(declaration))
    if extract is not None:
        (package / "extract.json").write_text(json.dumps(extract))
    for index, case in enumerate(cases):
        (package / "corpus" / f"{index:02d}-{case['id']}.json").write_text(json.dumps(case))
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def case(
    case_id: str,
    steps: Sequence[Mapping[str, Any]],
    *,
    provenance: str = "documented",
    routes: Sequence[str] = (),
    profile: str | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": 1,
        "id": case_id,
        "title": f"case {case_id}",
        "source": {"url": "https://example.test/docs", "fetched": "2026-09-02", "provenance": provenance},
        "routes": list(routes),
        "steps": list(steps),
    }
    if profile is not None:
        doc["profile"] = profile
    return doc


def step(
    name: str,
    method: str,
    path: str,
    *,
    body: Any = None,
    headers: Mapping[str, str] | None = None,
    query: Mapping[str, str] | None = None,
    status: int = 200,
    expect_body: Any = None,
    expect_headers: Mapping[str, str] | None = None,
    absent: Sequence[str] = (),
    capture: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"method": method, "path": path}
    if body is not None:
        request["body"] = body
    if headers:
        request["headers"] = dict(headers)
    if query:
        request["query"] = dict(query)
    expect: dict[str, Any] = {"status": status}
    if expect_body is not None:
        expect["body"] = expect_body
    if expect_headers:
        expect["headers"] = dict(expect_headers)
    if absent:
        expect["absent"] = list(absent)
    out: dict[str, Any] = {"name": name, "request": request, "expect": expect}
    if capture:
        out["capture"] = dict(capture)
    return out


CREATE = step(
    "create",
    "POST",
    "/v2/orders",
    body={"idempotency_key": "${uuid}", "order": {"location_id": "${vars.location_id}"}},
    expect_body={"order": {"state": "OPEN", "location_id": "LOC_1"}},
    capture={"order_id": "/order/id"},
)


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *cases: Mapping[str, Any], **kwargs: Any) -> Any:
    from vendorfake.fidelity.corpus import load_corpus

    anchor = make_anchor(tmp_path, monkeypatch, cases)
    target = synthetic_target(anchor)
    kwargs.setdefault("client_factory", in_process)
    kwargs.setdefault("validate", False)
    return run_corpus(target, load_corpus(anchor), **kwargs)


# ---------------------------------------------------------------------------
# Steps, captures and failures.
# ---------------------------------------------------------------------------


def test_captures_flow_between_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    follow = step(
        "retrieve",
        "GET",
        "/v2/orders/${cap.order_id}",
        expect_body={"order": {"id": "${cap.order_id}", "line_items": [{"name": "coffee"}]}},
        expect_headers={"Content-Type": "application/json"},
    )
    report = _run(tmp_path, monkeypatch, case("orders.flow", [CREATE, follow]))
    (result,) = report.results
    assert result.passed, result.failure
    assert result.steps_run == 2
    assert report.ok and report.validated is False


def test_a_wrong_expectation_fails_naming_the_step_pointer_and_both_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = dict(CREATE, expect={"status": 200, "body": {"order": {"state": "COMPLETED"}}})
    never = step("never", "GET", "/v2/whoami")
    report = _run(tmp_path, monkeypatch, case("orders.wrong", [wrong, never]))
    (result,) = report.results
    assert not result.passed
    assert result.failure is not None
    assert (result.failure.step, result.failure.pointer) == ("create", "/order/state")
    assert (result.failure.expected, result.failure.actual) == ("COMPLETED", "OPEN")
    # The first failing expectation stops the case.
    assert result.steps_run == 1
    assert report.failed == 1 and not report.ok


def test_a_status_mismatch_carries_the_body_as_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = step("create", "POST", "/v2/orders", body={"order": {}}, status=200)
    report = _run(tmp_path, monkeypatch, case("orders.missing-location", [bad]))
    failure = report.results[0].failure
    assert failure is not None
    assert (failure.pointer, failure.expected, failure.actual) == ("status", 200, 400)
    assert "MISSING_REQUIRED_PARAMETER" in failure.detail


def test_absent_any_and_regex_in_expectations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = step(
        "create",
        "POST",
        "/v2/orders",
        body={"order": {"location_id": "${vars.location_id}"}},
        expect_body={"order": {"id": "${re:ord_[0-9]+}", "total": {"amount": "${any}"}}},
        absent=["/order/closed_at"],
    )
    present = dict(good, expect={"status": 200, "absent": ["/order/id"]})
    report = _run(tmp_path, monkeypatch, case("ok", [good]), case("present", [present]))
    ok, bad = report.results
    assert ok.passed, ok.failure
    assert bad.failure is not None
    assert (bad.failure.pointer, bad.failure.expected, bad.failure.actual) == ("/order/id", "absent", "ord_1")


def test_a_capture_that_does_not_resolve_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = dict(CREATE, capture={"nothing": "/order/nope"})
    report = _run(tmp_path, monkeypatch, case("cap", [broken]))
    failure = report.results[0].failure
    assert failure is not None
    assert failure.pointer == "capture/nothing" and failure.actual is MISSING


def test_an_unresolvable_reference_fails_before_any_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _run(tmp_path, monkeypatch, case("ref", [step("get", "GET", "/v2/orders/${cap.never}")]))
    failure = report.results[0].failure
    assert failure is not None
    assert failure.pointer == "request" and "${cap.never}" in str(failure.actual)


# ---------------------------------------------------------------------------
# Divergence classes: not that a case failed, but how.
# ---------------------------------------------------------------------------

_GOOD_BODY = {"order": {"location_id": "${vars.location_id}"}}

DIVERGENCES: list[tuple[str, dict[str, Any]]] = [
    # A 400 where the case expected a 200: the envelope, before any field.
    ("status", step("s", "POST", "/v2/orders", body={"order": {}}, status=200)),
    # The field is there and says something else.
    ("value", step("s", "POST", "/v2/orders", body=_GOOD_BODY, expect_body={"order": {"state": "COMPLETED"}})),
    # The field the case expects is not in the answer at all.
    ("missing", step("s", "POST", "/v2/orders", body=_GOOD_BODY, expect_body={"order": {"closed_at": "${any}"}})),
    # A field the case said would be absent, and was not.
    ("unexpected", step("s", "POST", "/v2/orders", body=_GOOD_BODY, absent=["/order/id"])),
    ("header", step("s", "POST", "/v2/orders", body=_GOOD_BODY, expect_headers={"content-type": "text/plain"})),
    ("capture", step("s", "POST", "/v2/orders", body=_GOOD_BODY, capture={"nothing": "/order/nope"})),
    # Nothing was even asked: the reference does not resolve.
    ("request", step("s", "GET", "/v2/orders/${cap.never}")),
]


@pytest.mark.parametrize(("kind", "failing"), DIVERGENCES, ids=[kind for kind, _ in DIVERGENCES])
def test_each_way_a_case_can_diverge_is_classified(
    kind: str, failing: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _run(tmp_path, monkeypatch, case(f"d.{kind}", [failing]))
    failure = report.results[0].failure
    assert failure is not None and failure.kind == kind
    assert report.by_kind()[kind] == 1
    assert sum(report.by_kind().values()) == 1


def test_a_schema_refusal_is_its_own_class_and_carries_the_first_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract leg refusing a body is not the same finding as the unit answering the wrong value."""
    strict = json.loads(json.dumps(EXTRACT))
    strict["components"]["schemas"]["Order"]["properties"]["state"]["enum"] = ["NEVER"]
    anchor = make_anchor(tmp_path, monkeypatch, [case("orders.flow", [CREATE])], extract=strict)
    from vendorfake.fidelity.corpus import load_corpus

    report = run_corpus(synthetic_target(anchor), load_corpus(anchor))
    failure = report.results[0].failure
    assert failure is not None and failure.kind == "schema"
    assert failure.detail.startswith("/order/state"), failure.detail
    assert "\n" not in failure.detail, f"the first error only, got {failure.detail!r}"


def test_a_declared_route_no_step_reached_is_a_missing_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = case("claims", [step("who", "GET", "/v2/whoami")], routes=["POST /v2/orders"])
    report = _run(tmp_path, monkeypatch, claims)
    failure = report.results[0].failure
    assert failure is not None and failure.kind == "missing"
    assert "never reached: POST /v2/orders" in str(failure.actual)


def test_the_tally_counts_every_class_and_prints_only_when_something_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vendorfake.fidelity.report import format_cases

    mixed = [case(f"d.{kind}", [failing]) for kind, failing in DIVERGENCES[:2]]
    report = _run(tmp_path, monkeypatch, *mixed)
    assert report.by_kind() == {
        "status": 1,
        "value": 1,
        "missing": 0,
        "unexpected": 0,
        "schema": 0,
        "header": 0,
        "capture": 0,
        "request": 0,
    }
    text = format_cases(report)
    assert "divergence: status 1, value 1, missing 0, unexpected 0, schema 0, header 0, capture 0, request 0" in text
    assert "[FAIL status] d.status" in text and "[FAIL value] d.value" in text
    green = _run(tmp_path, monkeypatch, case("fine", [CREATE]))
    assert "divergence:" not in format_cases(green)


# ---------------------------------------------------------------------------
# $auth, fresh units, uuids, profiles.
# ---------------------------------------------------------------------------


def test_auth_header_expands_to_the_published_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    who = step(
        "who",
        "GET",
        "/v2/whoami",
        headers={"$auth": "test", "x-extra": "kept"},
        query={"q": "${vars.location_id}"},
        expect_body={"auth": "Test prn_1", "query": {"q": "LOC_1"}},
    )
    unknown = step("who", "GET", "/v2/whoami", headers={"$auth": "oauth"})
    report = _run(tmp_path, monkeypatch, case("auth.ok", [who]), case("auth.unknown", [unknown]))
    ok, bad = report.results
    assert ok.passed, ok.failure
    assert bad.failure is not None
    assert bad.failure.pointer == "request/headers/$auth"
    assert "oauth" in str(bad.failure.expected) and "test" in str(bad.failure.actual)


def test_each_case_gets_a_fresh_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both cases create the FIRST order of their unit: ord_1 twice, never ord_2."""
    first = step(
        "create", "POST", "/v2/orders", body={"order": {"location_id": "L"}}, expect_body={"order": {"id": "ord_1"}}
    )
    report = _run(tmp_path, monkeypatch, case("a", [first]), case("b", [first]))
    assert [result.passed for result in report.results] == [True, True], report.results


def test_uuid_is_fresh_per_occurrence_and_deterministic_per_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Any] = []

    def recording(unit: Unit) -> Any:
        client = in_process(unit)

        class Recorder:
            def call(self, **kwargs: Any) -> Any:
                sent.append(kwargs.get("body"))
                return client.call(**kwargs)

        return Recorder()

    two = step("create", "POST", "/v2/orders", body={"idempotency_key": "${uuid}", "order": {"location_id": "${uuid}"}})
    for _ in range(2):
        _run(tmp_path, monkeypatch, case("ids", [two]), client_factory=recording)
    first, second = sent
    # Two references, two ids.
    assert first["idempotency_key"] != first["order"]["location_id"]
    # The same case sends the same ids on every run.
    assert first == second
    uuid.UUID(first["idempotency_key"])


def test_profile_comes_from_the_override_then_the_case_then_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    who = step("who", "GET", "/v2/whoami")
    report = _run(tmp_path, monkeypatch, case("own", [who], profile="own-profile"), case("default", [who]))
    assert [result.profile for result in report.results] == ["own-profile", "test"]
    report = _run(tmp_path, monkeypatch, case("own", [who], profile="own-profile"), profile_override="forced")
    assert report.results[0].profile == "forced"


def test_the_default_client_validates_and_fills_the_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``validate=True`` with no injected factory is the validating client from ``fidelity/validate.py``."""
    anchor = make_anchor(tmp_path, monkeypatch, [case("orders.flow", [CREATE])])
    from vendorfake.fidelity.corpus import load_corpus

    report = run_corpus(synthetic_target(anchor), load_corpus(anchor))
    assert report.validated and report.ledger is not None
    assert report.results[0].passed, report.results[0].failure
    rows = {row.key: row.validated for row in report.ledger.rows()}
    assert rows.get("POST /v2/orders") == 1, rows
    assert "validated" in report.ledger.summary()


def test_a_schema_violation_raised_by_the_client_fails_the_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validating client raises on a body the extract forbids; the case reports it, not the run."""
    strict = json.loads(json.dumps(EXTRACT))
    strict["components"]["schemas"]["Order"]["properties"]["state"]["enum"] = ["NEVER"]
    anchor = make_anchor(tmp_path, monkeypatch, [case("orders.flow", [CREATE])], extract=strict)
    from vendorfake.fidelity.corpus import load_corpus

    report = run_corpus(synthetic_target(anchor), load_corpus(anchor))
    failure = report.results[0].failure
    assert failure is not None
    assert failure.pointer == "response" and failure.actual == "FidelityViolation"
    assert "/order/state" in failure.detail


# ---------------------------------------------------------------------------
# The world a case runs in.
# ---------------------------------------------------------------------------


class FakeWorld:
    """A world that is not a control plane, which is the whole point of the seam."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = list(rows)
        self.resets = 0

    def profile(self) -> str:
        return "from-the-world"

    def reset(self) -> None:
        self.resets += 1

    def credentials(self) -> Sequence[Mapping[str, Any]]:
        return self.rows


@contextmanager
def _synthetic_opener(profile: str) -> Iterator[Any]:
    with open_synthetic_unit(profile) as unit:
        yield in_process(unit)


def _one(doc: Mapping[str, Any], **kwargs: Any) -> Any:
    from vendorfake.fidelity.corpus import parse_case

    return run_case(parse_case(doc), _synthetic_opener, profile="test", variables={}, **kwargs)


def test_auth_comes_from_the_world_and_not_from_the_control_plane() -> None:
    """The unit's own ``/__unit/auth`` publishes mode ``test``; the world publishes ``sandbox``, and wins."""
    world = FakeWorld([{"mode": "sandbox", "headers": {"authorization": "Bearer from-a-manifest"}}])
    who = step("who", "GET", "/v2/whoami", headers={"$auth": "sandbox"}, expect_body={"auth": "Bearer from-a-manifest"})
    result = _one(case("w", [who]), world=world)
    assert result.passed, result.failure


def test_a_mode_the_world_does_not_publish_names_what_it_does() -> None:
    world = FakeWorld([{"mode": "sandbox", "headers": {}}])
    result = _one(case("w", [step("who", "GET", "/v2/whoami", headers={"$auth": "oauth"})]), world=world)
    assert result.failure is not None and result.failure.kind == "request"
    assert "modes this world publishes: ['sandbox']" in str(result.failure.actual)


def test_the_opener_resets_the_world_once_per_case() -> None:
    world = FakeWorld([])
    opener = world_opener("http://127.0.0.1:9", world)
    for _ in range(2):
        with opener("ignored"):
            pass
    assert world.resets == 2


def manifest_file(tmp_path: Path, **changes: Any) -> Path:
    document: dict[str, Any] = {
        "schema": "vendorfake.manifest/1",
        "vendorfake": "0.5.0",
        "vendor": "synthetic",
        "profile": "full",
        "base_url": "http://localhost:8080",
        "credentials": [{"label": "seeded", "mode": "bearer", "headers": {"authorization": "Bearer abc"}}],
        "ids": {},
    }
    document.update({key: value for key, value in changes.items() if value is not ...})
    for key, value in changes.items():
        if value is ...:
            document.pop(key, None)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document))
    return path


def test_a_manifest_round_trips_into_a_profile_credentials_and_a_base_url(tmp_path: Path) -> None:
    world = ManifestWorld(manifest_file(tmp_path))
    assert world.profile() == "full"
    assert [row["mode"] for row in world.credentials()] == ["bearer"]
    assert world.base_url == "http://localhost:8080"
    world.reset()
    assert list(world.caveats()) == [MANIFEST_CAVEAT]


def test_a_manifest_without_a_base_url_is_a_world_with_no_address(tmp_path: Path) -> None:
    assert ManifestWorld(manifest_file(tmp_path, base_url=...)).base_url is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "vendorfake.manifest/2"}, "expected 'vendorfake.manifest/1'"),
        ({"profile": ...}, "no profile"),
    ],
)
def test_a_document_that_is_not_a_manifest_is_refused_by_name(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(LookupError) as raised:
        ManifestWorld(manifest_file(tmp_path, **changes))
    assert message in str(raised.value)


def test_a_missing_manifest_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(LookupError) as raised:
        ManifestWorld(tmp_path / "nope.json")
    assert "cannot read the manifest" in str(raised.value)


# ---------------------------------------------------------------------------
# Targets and modeled routes.
# ---------------------------------------------------------------------------


TARGET = synthetic_target("nowhere")


def target_factory() -> FidelityTarget:
    return TARGET


def test_resolve_target_accepts_a_target_or_a_factory_and_names_anything_else() -> None:
    assert resolve_target(f"{__name__}:TARGET") is TARGET
    assert resolve_target(f"{__name__}:target_factory") is TARGET
    with pytest.raises(LookupError) as raised:
        resolve_target(f"{__name__}:SOURCE_URL")
    assert "not a FidelityTarget" in str(raised.value)


def test_modeled_routes_apply_aliases_and_skip_the_control_plane() -> None:
    declaration = FidelityDeclaration.of(
        "x",
        {
            **DECLARATION,
            "aliases": [
                {"method": "GET", "path": "/v2/whoami", "spec_path": "/v2/who/{id}", "reason": "literal for {id}"}
            ],
        },
    )
    with open_synthetic_unit(None) as unit:
        # The control plane is mounted.
        assert any(r.path.startswith("/__unit/") for r in unit.routes)
        modeled = modeled_routes(unit.routes, declaration)
    assert modeled == (
        ("GET", "/v2/orders/{order_id}"),
        ("GET", "/v2/plain"),
        ("GET", "/v2/undeclared"),
        ("GET", "/v2/who/{id}"),
        ("POST", "/v2/orders"),
    )


@pytest.mark.integration
def test_the_remote_runner_reaches_a_served_unit_and_says_it_did_not_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conformance.harness import serving
    from vendorfake.fidelity.corpus import load_corpus

    anchor = make_anchor(tmp_path, monkeypatch, [case("a", [CREATE]), case("b", [CREATE])])
    with open_synthetic_unit(None) as unit, serving(unit) as base_url:
        report = run_corpus_remote(base_url, anchor, load_corpus(anchor))
    assert [result.passed for result in report.results] == [True, True], report.results
    assert report.remote and not report.validated and report.caveats
    assert report.results[0].profile == "test"
