"""The request log: the ring itself, what the kernel puts in it, and the routes.

Three layers, tested separately because they fail differently. The ring is a
data structure with a bound and three filters. The kernel decides *what* is
recorded -- every vendor request, no control request, the near misses on an
unmatched one. The control plane is the only way a consumer reaches any of it,
and its query parameters are where a silent misreading (``?unmatched=false``
meaning "no filter") would be invisible.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fakes import make_unit, route
from vendorfake.core.control.plane import DEFAULT_REQUEST_LIMIT, control_plane_routes
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import RequestRecord, UnitError, UnitErrorKind
from vendorfake.core.kernel.unit import RequestLog
from vendorfake.core.transport.inprocess import InProcessClient, in_process


def _record(**kwargs: Any) -> RequestRecord:
    base: dict[str, Any] = {
        "id": "req_1",
        "received_at": "2024-01-01T00:00:00.000Z",
        "method": "GET",
        "path": "/v2/orders",
        "route": "GET /v2/orders",
        "operation_id": "ListOrders",
        "status": 200,
        "matched": True,
        "fault": None,
        "rule_id": None,
        "duration_ms": 1,
    }
    base.update(kwargs)
    return RequestRecord(**base)


# ---------------------------------------------------------------------------
# the ring
# ---------------------------------------------------------------------------


def test_the_oldest_record_is_evicted_first() -> None:
    """Oldest first, because a consumer asking what just happened is asking
    about the end of the run; refusing to record once full would lose exactly
    the part they wanted."""
    log = RequestLog(2)
    for index in range(3):
        log.record(_record(id=f"req_{index}"))
    assert [entry.id for entry in log.records()] == ["req_2", "req_1"]
    assert len(log) == 2


def test_a_capacity_of_zero_records_nothing() -> None:
    """How the log is switched off, for a consumer who wants neither the
    memory nor the record."""
    log = RequestLog(0)
    log.record(_record())
    assert log.records() == ()
    assert len(log) == 0


def test_a_negative_capacity_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="zero or more"):
        RequestLog(-1)


def test_records_come_back_newest_first() -> None:
    log = RequestLog(10)
    log.record(_record(id="first"))
    log.record(_record(id="second"))
    assert [entry.id for entry in log.records()] == ["second", "first"]


def test_the_limit_applies_after_the_filter() -> None:
    """`requests(operation_id=X, limit=1)` must be the most recent call to X,
    not 'the most recent call, if it happened to be X' -- the second reading
    answers nothing for a route that was definitely called."""
    log = RequestLog(10)
    log.record(_record(id="wanted", operation_id="CreateOrder"))
    log.record(_record(id="noise", operation_id="ListOrders"))
    assert [entry.id for entry in log.records(operation_id="CreateOrder", limit=1)] == ["wanted"]


def test_unmatched_false_selects_the_matched_ones_rather_than_everything() -> None:
    """The distinction a boolean defaulting to False would have thrown away."""
    log = RequestLog(10)
    log.record(_record(id="hit", matched=True))
    log.record(_record(id="miss", matched=False, route=None, operation_id=None, status=404))
    assert [entry.id for entry in log.records(unmatched=True)] == ["miss"]
    assert [entry.id for entry in log.records(unmatched=False)] == ["hit"]
    assert {entry.id for entry in log.records()} == {"hit", "miss"}


def test_filters_are_a_conjunction() -> None:
    log = RequestLog(10)
    log.record(_record(id="both", route="POST /v2/orders", operation_id="CreateOrder"))
    log.record(_record(id="route-only", route="POST /v2/orders", operation_id="Other"))
    found = log.records(route="POST /v2/orders", operation_id="CreateOrder")
    assert [entry.id for entry in found] == ["both"]


def test_clearing_reports_what_it_dropped() -> None:
    log = RequestLog(10)
    log.record(_record())
    assert log.clear() == 1
    assert log.records() == ()


# ---------------------------------------------------------------------------
# what the kernel records
# ---------------------------------------------------------------------------


def _unit(**config: Any) -> Any:
    return make_unit(
        [
            route("GET", "/v2/orders", lambda args: json_({"orders": []}), operation_id="ListOrders"),
            route("POST", "/v2/orders", _boom, operation_id="CreateOrder"),
        ],
        control_routes=control_plane_routes,
        **config,
    )


def _boom(args: object) -> object:
    raise UnitError(UnitErrorKind.BAD_REQUEST, detail="no")


def _api(**config: Any) -> tuple[InProcessClient, Any]:
    unit = _unit(**config)
    return in_process(unit), unit


def test_every_vendor_request_is_recorded_whatever_it_answered() -> None:
    """A 4xx leaves no journal entry by design; it must leave a request record,
    or the log answers only the question the journal already answers."""
    api, unit = _api()
    api.get("/v2/orders")
    api.post("/v2/orders", {})
    rows = unit.requests.records()
    assert [(row.operation_id, row.status) for row in rows] == [("CreateOrder", 400), ("ListOrders", 200)]
    assert all(row.matched for row in rows)


def test_a_control_plane_request_is_never_recorded() -> None:
    """The plane is the observer. A log that recorded reads of itself would
    grow by a row per question asked of it and bury the traffic underneath."""
    api, unit = _api()
    api.get("/v2/orders")
    for _ in range(5):
        api.get("/__unit/requests")
        api.get("/__unit/health")
    assert [row.path for row in unit.requests.records()] == ["/v2/orders"]


def test_a_mistyped_control_plane_path_is_still_never_recorded() -> None:
    """Excluded by path prefix, not only by matched route: a request under
    `/__unit/*` that matches no route -- a fixture's typo, a stray preflight
    -- is still the observer's own traffic. Recording it would let a
    consumer's polling mistake masquerade as the vendor call under test, and
    would evict real traffic out from under a small `requests.capacity`."""
    api, unit = _api()
    api.get("/v2/orders")
    api.get("/__unit/reqests")
    assert [row.path for row in unit.requests.records()] == ["/v2/orders"]


def test_a_wrong_verb_on_a_real_control_route_is_still_never_recorded() -> None:
    """Same exclusion, reached through a 405 rather than a 404: the path is a
    real control route, only the verb is wrong, and it is still control-plane
    traffic that must stay absent from the log."""
    api, unit = _api()
    api.get("/v2/orders")
    api.put("/__unit/health")
    assert [row.path for row in unit.requests.records()] == ["/v2/orders"]


def test_an_unmatched_request_is_recorded_with_its_near_misses() -> None:
    api, unit = _api()
    api.get("/v2/order")
    (row,) = unit.requests.records()
    assert (row.matched, row.route, row.operation_id, row.status) == (False, None, None, 404)
    assert [miss.operation_id for miss in row.near_misses] == ["ListOrders", "CreateOrder"]


def test_a_wrong_verb_is_recorded_unmatched_and_without_near_misses() -> None:
    """A 405 already names the methods that are allowed, so a near-miss list
    beside it would repeat the answer the consumer already has."""
    api, unit = _api()
    api.delete("/v2/orders")
    (row,) = unit.requests.records()
    assert (row.matched, row.status, row.near_misses) == (False, 405, ())


def test_an_injected_fault_is_recorded_with_the_rule_that_armed_it() -> None:
    """The 429 a consumer cannot otherwise explain: the fault fired inside the
    pipeline and raised, so a record written on the way out of the handler
    would never have been written at all."""
    api, unit = _api(
        capabilities=("orders", "chaos"),
        chaos_rules=({"id": "flaky", "scope": "request", "fault": "rate_limit", "match": {"route": "GET /v2/orders"}},),
    )
    answered = api.get("/v2/orders")
    (row,) = unit.requests.records()
    assert (answered.status, row.status, row.fault, row.rule_id) == (429, 429, "rate_limit", "flaky")


def test_the_recorded_id_is_the_one_echoed_on_the_response() -> None:
    """The only handle a consumer has on a row, since no body is kept."""
    api, unit = _api()
    answered = api.get("/v2/orders")
    (row,) = unit.requests.records()
    assert row.id == answered.header("x-unit-request-id")


def test_a_state_reset_clears_the_log_with_the_state() -> None:
    """A per-test reset that left the previous test's calls behind would make
    assert_called(times=1) pass or fail on test order."""
    api, unit = _api()
    api.get("/v2/orders")
    api.post("/__unit/state/reset", {})
    assert unit.requests.records() == ()


def test_the_capacity_comes_from_the_profile() -> None:
    api, unit = _api(request_log_capacity=2)
    for _ in range(3):
        api.get("/v2/orders")
    assert unit.requests.capacity == 2
    assert len(unit.requests) == 2


# ---------------------------------------------------------------------------
# the control routes
# ---------------------------------------------------------------------------


def test_the_route_reports_the_bound_as_well_as_the_page() -> None:
    """`recorded` and `capacity` are what tell a reader that what they are
    looking at is a page of a ring rather than everything that happened."""
    api, _ = _api(request_log_capacity=50)
    api.get("/v2/orders")
    body = api.get("/__unit/requests").json()
    assert (body["count"], body["recorded"], body["capacity"], body["limit"]) == (1, 1, 50, 50)


def test_the_default_limit_is_a_hundred_and_is_clamped_by_the_capacity() -> None:
    api, _ = _api(request_log_capacity=DEFAULT_REQUEST_LIMIT * 2)
    assert api.get("/__unit/requests").json()["limit"] == DEFAULT_REQUEST_LIMIT
    assert api.get("/__unit/requests?limit=100000").json()["limit"] == DEFAULT_REQUEST_LIMIT * 2


def test_a_limit_that_is_not_a_positive_integer_is_refused() -> None:
    """`?limit=abc` silently meaning 'the default' is the same defect
    `?since=abc` had: an ignored knob answering something else."""
    api, _ = _api()
    for value in ("abc", "0", "-1"):
        answered = api.get(f"/__unit/requests?limit={value}")
        assert (answered.status, answered.header("x-unit-error")) == (400, "invalid_value")


def test_the_unmatched_flag_is_tri_state_and_refuses_a_guess() -> None:
    api, _ = _api()
    api.get("/v2/orders")
    api.get("/v2/order")
    assert api.get("/__unit/requests").json()["count"] == 2
    assert api.get("/__unit/requests?unmatched=true").json()["count"] == 1
    assert api.get("/__unit/requests?unmatched=false").json()["count"] == 1
    assert api.get("/__unit/requests?unmatched").json()["count"] == 1
    refused = api.get("/__unit/requests?unmatched=perhaps")
    assert (refused.status, refused.header("x-unit-error")) == (400, "invalid_value")


def test_the_route_filters_by_operation_id_and_by_route_key() -> None:
    api, _ = _api()
    api.get("/v2/orders")
    api.post("/v2/orders", {})
    assert api.get("/__unit/requests?operation_id=CreateOrder").json()["count"] == 1
    assert api.get("/__unit/requests?route=GET%20/v2/orders").json()["count"] == 1
    assert api.get("/__unit/requests?operation_id=Nonexistent").json()["count"] == 0


def test_deleting_the_log_forgets_the_requests_and_nothing_else() -> None:
    api, unit = _api()
    api.get("/v2/orders")
    digest = unit.context.store.entity_digest()
    assert api.delete("/__unit/requests").json() == {"cleared": 1}
    assert api.get("/__unit/requests").json()["requests"] == []
    assert unit.context.store.entity_digest() == digest


def test_the_near_misses_route_narrows_to_the_unmatched_ones() -> None:
    api, _ = _api()
    api.get("/v2/orders")
    api.get("/v2/order")
    body = api.get("/__unit/requests/unmatched/near-misses").json()
    assert body["count"] == 1
    (row,) = body["near_misses"]
    assert row["request"]["path"] == "/v2/order"
    assert [miss["operation_id"] for miss in row["near_misses"]] == ["ListOrders", "CreateOrder"]
