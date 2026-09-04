"""The control plane's thirty-three routes, asserted on shape rather than on 200.

Every test here pins something a reviewer could reasonably disagree about: a
path, a key name, an error kind, an ordering, or which of two plausible
readings of a body the plane took. A test that only asserted "it returns 200"
would pass under an implementation that answered every route with ``{}``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from tests.fakes import (
    WEBHOOK_CAPABILITIES,
    FakeEvents,
    FakeSigner,
    FakeVendor,
    VendorWithoutRoles,
    make_unit,
    route,
)
from vendorfake.core.capability.registry import CONTROL_CAPABILITY
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import UnitErrorKind
from vendorfake.core.state.machine import MachineDef, StateDef
from vendorfake.core.transport.inprocess import InProcessClient, in_process
from vendorfake.core.webhooks.sink import MemorySink

# ---------------------------------------------------------------------------
# The reference's twenty-one paths, copied out of packages/core/src/control/
# plane.ts and rewritten only where a `:param` becomes a `{param}`. Byte-
# identical otherwise: the conformance suite and both consumer example suites
# read these addresses, so a rename here is a break for every consumer.
# ---------------------------------------------------------------------------
REFERENCE_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/__unit/health"),
    ("GET", "/__unit/info"),
    ("GET", "/__unit/routes"),
    ("GET", "/__unit/capabilities"),
    ("POST", "/__unit/capabilities"),
    ("GET", "/__unit/chaos"),
    ("POST", "/__unit/chaos/rules"),
    ("DELETE", "/__unit/chaos/rules/{id}"),
    ("POST", "/__unit/chaos/reset"),
    ("GET", "/__unit/journal"),
    ("GET", "/__unit/state"),
    ("GET", "/__unit/state/snapshot"),
    ("POST", "/__unit/state/restore"),
    ("POST", "/__unit/state/reset"),
    ("GET", "/__unit/webhooks/subscriptions"),
    ("POST", "/__unit/webhooks/subscriptions"),
    ("DELETE", "/__unit/webhooks/subscriptions/{id}"),
    ("GET", "/__unit/webhooks/deliveries"),
    ("POST", "/__unit/webhooks/drain"),
    ("POST", "/__unit/webhooks/retry-policy"),
    ("POST", "/__unit/clock/advance"),
)

#: Nine the conformance design requires so that every check can be driven
#: through a URL instead of an in-process object graph -- three of those closed
#: measured holes: with no ``/__unit/auth`` no check could obtain a credential,
#: so the whole authentication layer could be deleted and the suite stayed
#: green; with no ``state/update`` and no ``state/page`` the store's version
#: and cursor rules were reachable only through whichever endpoint a particular
#: vendor happened to publish -- plus three carrying the request log, which
#: answers the question the journal cannot be asked: not "what changed" but
#: "what was called". A 4xx and a request that matched no route leave no
#: journal entry by design, so without these a consumer whose call never landed
#: has nothing to look at.
ADDED_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/__unit/errors"),
    ("GET", "/__unit/machines"),
    ("POST", "/__unit/machines/probe"),
    ("POST", "/__unit/echo"),
    ("POST", "/__unit/webhooks/emit"),
    ("POST", "/__unit/webhooks/sink"),
    ("GET", "/__unit/auth"),
    ("POST", "/__unit/state/update"),
    ("POST", "/__unit/state/page"),
    ("GET", "/__unit/requests"),
    ("DELETE", "/__unit/requests"),
    ("GET", "/__unit/requests/unmatched/near-misses"),
)

#: The routes whose handler blocks on machinery another request must feed.
UNSERIALIZED: frozenset[str] = frozenset({"POST /__unit/webhooks/drain", "POST /__unit/clock/advance"})

ORDER_MACHINE = MachineDef(
    field="state",
    initial="OPEN",
    states={
        "OPEN": StateDef(summary="Accepting changes.", to=("COMPLETED", "CANCELED"), allow_self=True),
        "COMPLETED": StateDef(summary="Paid; nothing further."),
        "CANCELED": StateDef(summary="Voided."),
    },
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _vendor(**kwargs: Any) -> FakeVendor:
    base: dict[str, Any] = {
        "capabilities": WEBHOOK_CAPABILITIES,
        "not_supported": {},
        "machines": {"order": ORDER_MACHINE},
        "signer": FakeSigner(),
        "events": FakeEvents(),
    }
    base.update(kwargs)
    return FakeVendor(**base)


def _unit(
    *,
    vendor: FakeVendor | None = None,
    sink: MemorySink | None = None,
    routes: Any = None,
    **config: Any,
) -> Any:
    settings: dict[str, Any] = {
        "capabilities": ("orders", "chaos", "webhooks", "webhooks.chaos"),
        "schedule_ms": (5, 10),
        "clock_mode": "virtual",
        "clock_start": "2024-01-01T00:00:00.000Z",
    }
    settings.update(config)
    return make_unit(
        routes if routes is not None else [route("GET", "/v2/orders", lambda args: json_({"orders": []}))],
        vendor=vendor if vendor is not None else _vendor(),
        control_routes=control_plane_routes,
        sink=sink if sink is not None else MemorySink(),
        **settings,
    )


def _api(**kwargs: Any) -> tuple[InProcessClient, Any]:
    unit = _unit(**kwargs)
    return in_process(unit), unit


# ---------------------------------------------------------------------------
# the route table itself
# ---------------------------------------------------------------------------


def test_every_reference_path_is_present_byte_for_byte() -> None:
    """The addresses are the contract. A consumer's example suite, the
    conformance runner and a container's HEALTHCHECK all hard-code them, so a
    rename is a break with no deprecation window."""
    unit = _unit()
    present = {(r.method, r.path) for r in unit.routes if r.internal}
    missing = [entry for entry in REFERENCE_ROUTES if entry not in present]
    assert missing == []


def test_the_plane_is_exactly_the_twenty_one_plus_twelve_and_nothing_else() -> None:
    """A count, and then the set, because a count alone would pass if one route
    were dropped and an unrelated one added."""
    unit = _unit()
    control = {(r.method, r.path) for r in unit.routes if r.internal}
    assert control == set(REFERENCE_ROUTES) | set(ADDED_ROUTES)
    assert len(control) == 33


def test_every_control_route_is_internal_and_owns_the_control_capability() -> None:
    """`internal` is what makes the kernel skip auth, chaos and idempotency. A
    control route that lost it would be gateable by the very chaos rule it
    exists to remove, and a unit could be locked out of its own recovery."""
    unit = _unit()
    plane = [r for r in unit.routes if r.path.startswith("/__unit/")]
    assert all(r.internal for r in plane)
    assert {r.capability for r in plane} == {CONTROL_CAPABILITY}
    assert all(r.auth is None for r in plane)


def test_exactly_two_control_routes_release_the_request_lock() -> None:
    """Both block on machinery another request must feed. Anything else
    declaring `serialized=False` has given up deterministic id minting for
    nothing; anything that drains or advances *without* it holds the whole unit
    for the delivery timeout against an unreachable subscriber."""
    unit = _unit()
    released = {r.key for r in unit.routes if not r.serialized}
    assert released == UNSERIALIZED


def test_every_control_route_carries_a_summary_and_an_operation_id() -> None:
    """Both are read by the generated OpenAPI document and by /__unit/routes.
    A route with neither is a route nobody can discover."""
    unit = _unit()
    for r in unit.routes:
        if r.internal:
            assert r.summary, r.key
            assert r.operation_id, r.key


# ---------------------------------------------------------------------------
# health, info, routes
# ---------------------------------------------------------------------------


def test_health_names_the_vendor_the_profile_and_the_framework_counter() -> None:
    api, _ = _api()
    body = api.get("/__unit/health").json()
    assert body["status"] == "ok"
    assert body["vendor"] == "acme"
    assert body["profile"] == "test"
    assert isinstance(body["uptime_ms"], int)
    # Zero over the in-process binding is the true answer, not a stub: there is
    # no framework present that could have answered anything.
    assert body["framework_answered"] == 0


def test_health_reports_the_transport_adapters_counter_when_one_is_supplied() -> None:
    """The counter is surfaced rather than kept in the serving process,
    because a list inside a uvicorn child is unreadable from the parent of an
    out-of-process test -- which is the only place the number matters."""
    hits = [3]
    unit = make_unit(
        [],
        vendor=_vendor(),
        control_routes=lambda binding: control_plane_routes(binding, framework_answered=lambda: hits[0]),
        sink=MemorySink(),
        capabilities=("orders", "chaos", "webhooks", "webhooks.chaos"),
        schedule_ms=(5,),
    )
    assert in_process(unit).get("/__unit/health").json()["framework_answered"] == 3


def test_info_carries_all_seven_keys_the_conformance_suite_asserts_by_name() -> None:
    api, _ = _api()
    body = api.get("/__unit/info").json()
    for key in ("vendor", "profile", "capabilities", "chaos", "webhooks", "clock", "state"):
        assert key in body, key


def test_info_publishes_empty_roles_for_a_vendor_that_predates_the_property() -> None:
    """``VendorDefinition.roles`` became a required protocol member in 0.2, so
    a third-party vendor from the ``vendorfake.vendors`` entry-point group
    built against 0.1.0 has no such attribute.

    ``GET /__unit/info`` is on the path of the CLI's ``info`` subcommand,
    ``Driver.clock()`` and the conformance runner, so reading ``roles`` as a
    plain attribute would make every one of those an ``AttributeError`` --
    turning a documented, fixable gap into a unit that cannot answer at all.
    It publishes ``{}`` instead, which is what lets conformance C34 report the
    real defect ("add ``VendorDefinition.roles``") against a live unit.
    """
    api, _ = _api(vendor=cast("FakeVendor", VendorWithoutRoles(_vendor())))
    assert api.get("/__unit/info").json()["vendor"]["roles"] == {}


def test_info_omits_an_absent_signer_rather_than_sending_null() -> None:
    """The reference writes `?? null`. Absent is absent here: a `"signer":
    null` is a value where there is no fact, and it would put a null into a
    document a consumer diffs against another unit's."""
    api, _ = _api(vendor=_vendor(signer=None))
    body = api.get("/__unit/info").json()
    assert "signer" not in body
    assert "magic" not in body


def test_info_echoes_the_vendors_reasons_for_the_capabilities_it_does_not_support() -> None:
    """A capability the core gates on but the vendor does not implement has to
    say so in prose, or "webhooks is off" and "this vendor has no webhooks" are
    indistinguishable to a consumer."""
    api, _ = _api(
        vendor=_vendor(
            capabilities=tuple(c for c in WEBHOOK_CAPABILITIES if not c.name.startswith("webhooks")),
            not_supported={"webhooks": "no delivery surface", "webhooks.chaos": "nothing to disturb"},
        ),
        capabilities=("orders", "chaos"),
        schedule_ms=(),
    )
    assert api.get("/__unit/info").json()["not_supported"]["webhooks"] == "no delivery surface"


def test_info_reports_state_entity_counts_with_sorted_keys() -> None:
    """Sorted where the reference iterates in materialisation order: two units
    read in a different order otherwise publish a different key order and a
    byte comparison between them fails for no reason anyone can see."""
    api, unit = _api()
    store = unit.context.store
    store.collection("zebras").insert({"id": "z1"})
    store.collection("aardvarks").insert({"id": "a1"})
    entities = api.get("/__unit/info").json()["state"]["entities"]
    assert list(entities) == sorted(entities)


def test_routes_publishes_the_whole_table_including_the_control_plane() -> None:
    """Including, because /__unit/routes is what a drift check and the
    generated OpenAPI read; a report that hid the control plane would let it
    change without any check noticing."""
    api, unit = _api()
    body = api.get("/__unit/routes").json()
    assert body["count"] == len(unit.routes)
    assert {(r["method"], r["path"]) for r in body["routes"]} >= set(REFERENCE_ROUTES)


def test_a_published_route_row_omits_an_absent_auth_rather_than_nulling_it() -> None:
    api, _ = _api()
    rows = {r["path"]: r for r in api.get("/__unit/routes").json()["routes"]}
    health = rows["/__unit/health"]
    assert "auth" not in health
    assert health["internal"] is True
    assert health["serialized"] is True
    assert rows["/__unit/clock/advance"]["serialized"] is False


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_errors_reports_every_one_of_the_twenty_core_kinds() -> None:
    """Twenty against a literal, never against the table's own length: a
    vendor's table that lost a row would otherwise agree with itself."""
    api, _ = _api()
    body = api.get("/__unit/errors").json()
    assert body["count"] == 20
    assert [row["kind"] for row in body["kinds"]] == [kind.value for kind in UnitErrorKind]


def test_every_shaped_error_is_a_4xx_or_5xx_with_a_body() -> None:
    api, _ = _api()
    for row in api.get("/__unit/errors").json()["kinds"]:
        assert 400 <= row["status"] < 600, row["kind"]
        assert row["body"], row["kind"]


def test_every_row_publishes_the_provenance_describe_reports() -> None:
    """The promise both vendors' docstrings make: a consumer can ask which
    statuses the vendor documents. Kept by the plane reading `describe()`,
    which the fake answers "judgment" for every row."""
    api, _ = _api()
    rows = api.get("/__unit/errors").json()["kinds"]
    assert {row["provenance"] for row in rows} == {"judgment"}


def test_a_provenance_that_goes_missing_after_start_is_a_500_that_says_so() -> None:
    """Unreachable past the startup check, and defended anyway: a 500 naming
    the kind rather than a 200 carrying `provenance: null`."""
    api, unit = _api()
    errors = unit.context.vendor.errors
    full = errors.describe()

    def short() -> dict[str, Any]:
        return {kind: row for kind, row in full.items() if kind != "timeout"}

    errors.describe = short  # type: ignore[method-assign]
    response = api.get("/__unit/errors")
    assert response.status == 500
    assert response.json()["error"]["code"] == "internal"
    assert "no provenance for 'timeout'" in response.json()["error"]["detail"]


def test_errors_also_publishes_the_no_route_shape_which_no_kind_covers() -> None:
    """`not_found` on the router path is a different hook from `shape`, and it
    is the one a consumer meets first when they mistype a URL."""
    api, _ = _api()
    assert api.get("/__unit/errors").json()["no_route"]["status"] == 404


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def test_capabilities_reports_the_core_gate_table_so_completeness_is_readable() -> None:
    """C11's data source. Without it, "the vendor declared every capability the
    core gates on" can only be checked by importing the core."""
    api, _ = _api()
    body = api.get("/__unit/capabilities").json()
    assert {gate["capability"] for gate in body["core_gates"]} == {"chaos", "webhooks", "webhooks.chaos"}
    assert all(gate["gated_at"] for gate in body["core_gates"])


def test_set_then_delta_then_enable_then_disable_is_the_order() -> None:
    """It is contract: `{"set": [...], "enable": [...]}` means something
    different under any other reading, and a consumer sends both in one body
    precisely when they mean "this list, plus that one"."""
    api, _ = _api()
    res = api.post("/__unit/capabilities", {"set": ["chaos"], "enable": ["orders"], "disable": ["chaos"]})
    enabled = {row["name"] for row in res.json()["capabilities"] if row["enabled"]}
    assert "orders" in enabled
    assert "chaos" not in enabled


def test_a_delta_applies_against_what_set_just_produced_not_against_the_profile() -> None:
    api, _ = _api()
    res = api.post("/__unit/capabilities", {"set": ["orders"], "delta": "+chaos"})
    enabled = {row["name"] for row in res.json()["capabilities"] if row["enabled"]}
    assert {"orders", "chaos"} <= enabled


def test_a_capability_toggle_body_with_a_misspelled_key_is_a_400_not_a_no_op() -> None:
    api, _ = _api()
    res = api.post("/__unit/capabilities", {"enabled": ["orders"]})
    assert res.status == 400
    assert res.header("x-unit-error") == "invalid_value"


# ---------------------------------------------------------------------------
# chaos
# ---------------------------------------------------------------------------


def test_a_rule_reports_the_routes_it_actually_resolves_to() -> None:
    """The answer to "why did my rule never fire". The reference validates
    `id`, `fault` and `scope` and never checks the route, so a typo is a rule
    that matches nothing, forever, silently."""
    api, _ = _api()
    res = api.post(
        "/__unit/chaos/rules",
        {"id": "r1", "scope": "request", "fault": "rate_limit", "match": {"route": "GET /v2/orders"}},
    )
    assert res.json()["rules"][0]["matched_routes"] == ["GET /v2/orders"]


def test_matched_routes_never_counts_a_control_route() -> None:
    """The pipeline short-circuits internal routes before fault selection ever
    runs, so counting them would report a rule as matching routes it can never
    fire on -- exactly the mistake this field exists to surface."""
    api, _ = _api()
    res = api.post("/__unit/chaos/rules", {"id": "wide", "scope": "request", "fault": "server_error"})
    resolved = res.json()["rules"][0]["matched_routes"]
    assert resolved == ["GET /v2/orders"]
    assert not any(key.startswith("GET /__unit/") for key in resolved)


def test_a_rule_matching_no_route_is_a_note_by_default_and_a_400_under_strict() -> None:
    """Both halves. A hard error by default would refuse a rule aimed at a
    route whose capability is temporarily off, which is legitimate; silence is
    how a chaos transcript ships with two dead rules in it."""
    api, _ = _api()
    lenient = api.post(
        "/__unit/chaos/rules",
        {"id": "dead", "scope": "request", "fault": "server_error", "match": {"route": "GET /v2/orders/:id"}},
    )
    assert lenient.status == 200
    assert lenient.json()["rules"][0]["matched_routes"] == []

    api_strict, _ = _api(chaos_strict_rules=True)
    strict = api_strict.post(
        "/__unit/chaos/rules",
        {"id": "dead", "scope": "request", "fault": "server_error", "match": {"route": "GET /v2/orders/:id"}},
    )
    assert strict.status == 400
    assert strict.header("x-unit-error") == "invalid_value"


def test_a_profile_rule_matching_no_route_stops_construction_under_strict() -> None:
    """Checked at construction, not on first request: the first symptom
    otherwise is a demo transcript in which the rule simply did nothing."""
    from vendorfake.core.kernel.types import UnitError

    with pytest.raises(UnitError) as caught:
        _unit(
            chaos_strict_rules=True,
            chaos_rules=(
                {"id": "ghost", "scope": "request", "fault": "server_error", "match": {"route": "GET /nope"}},
            ),
        )
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "chaos.rules"


def test_a_profile_rule_matching_no_route_is_only_a_note_by_default() -> None:
    unit = _unit(
        chaos_rules=({"id": "ghost", "scope": "request", "fault": "server_error", "match": {"route": "GET /nope"}},)
    )
    assert [r.id for r in unit.context.chaos.list()] == ["ghost"]


def test_a_bare_toggle_with_no_rule_is_two_hundred_and_switches_the_engine() -> None:
    """The reference's own toggle test. A single strict model would answer 400
    for a body carrying no `id` and no `fault`."""
    api, unit = _api()
    assert api.post("/__unit/chaos/rules", {"enabled": False}).status == 200
    assert unit.context.chaos.is_enabled is False
    assert api.get("/__unit/chaos").json()["enabled"] is False


def test_sending_a_replacement_set_and_a_bare_rule_at_once_is_refused() -> None:
    """Stricter than the reference's `else if`, which honours `rules` and
    silently drops the rule. Two instructions in one body is a caller who does
    not know which will win, and finding out from a transcript is worse."""
    api, _ = _api()
    res = api.post(
        "/__unit/chaos/rules",
        {"rules": [], "id": "r1", "scope": "request", "fault": "rate_limit"},
    )
    assert res.status == 400
    assert res.header("x-unit-error") == "invalid_value"


def test_a_rule_with_no_id_reports_missing_field_on_id() -> None:
    """The reference's `validateRule` kinds, verbatim: `missing_field` on `id`
    and on `fault`, `invalid_value` on `scope`."""
    api, _ = _api()
    res = api.post("/__unit/chaos/rules", {"scope": "request", "fault": "rate_limit"})
    assert (res.status, res.header("x-unit-error")) == (400, "missing_field")
    assert res.json()["error"]["field"] == "id"


def test_a_rule_with_a_bad_scope_reports_invalid_value_on_scope() -> None:
    api, _ = _api()
    res = api.post("/__unit/chaos/rules", {"id": "r", "fault": "rate_limit", "scope": "elsewhere"})
    assert (res.status, res.header("x-unit-error")) == (400, "invalid_value")
    assert res.json()["error"]["field"] == "scope"


def test_a_webhook_scope_rule_meets_the_webhooks_chaos_gate_here() -> None:
    """A behaviour capability has no surface of its own, so this route is where
    a consumer meets its "disabled" answer at all."""
    api, _ = _api(capabilities=("orders", "chaos", "webhooks"))
    res = api.post("/__unit/chaos/rules", {"id": "d", "scope": "webhook", "fault": "webhook.duplicate"})
    assert (res.status, res.header("x-unit-error")) == (501, "capability_disabled")


def test_a_misspelled_condition_key_is_refused_by_the_rule_grammar() -> None:
    """`{"when": {"nth_": [2]}}` is an unconditional rule in the reference --
    `shouldFire` sees no recognised condition and fires on every match."""
    api, _ = _api()
    res = api.post(
        "/__unit/chaos/rules",
        {"id": "r", "scope": "request", "fault": "rate_limit", "when": {"nth_": [2]}},
    )
    assert res.status == 400


def test_deleting_an_unknown_rule_is_a_404_naming_the_id() -> None:
    api, _ = _api()
    res = api.delete("/__unit/chaos/rules/nope")
    assert (res.status, res.header("x-unit-error")) == (404, "not_found")


def test_reset_drops_rules_and_keep_rules_keeps_them() -> None:
    api, _ = _api()
    api.post("/__unit/chaos/rules", {"id": "r1", "scope": "request", "fault": "rate_limit"})
    assert api.post("/__unit/chaos/reset", {"keep_rules": True}).json()["rules"][0]["id"] == "r1"
    assert api.post("/__unit/chaos/reset", {}).json()["rules"] == []


# ---------------------------------------------------------------------------
# journal and state
# ---------------------------------------------------------------------------


def test_the_journal_reports_its_own_since_so_a_reader_can_page_it() -> None:
    api, unit = _api()
    unit.context.store.collection("orders").insert({"id": "o1"})
    unit.context.store.collection("orders").insert({"id": "o2"})
    body = api.get("/__unit/journal", query={"since": "1"}).json()
    assert body["since"] == 1
    assert [entry["id"] for entry in body["entries"]] == ["o2"]
    assert body["count"] == 1
    assert body["seq"] == 2


def test_an_unparseable_since_is_a_400_and_not_the_whole_journal() -> None:
    """The reference falls back to 0, so `?since=abc` silently returns
    everything -- an ignored knob answering with the opposite of what was
    asked. Recorded as provenance: judgment."""
    api, _ = _api()
    res = api.get("/__unit/journal", query={"since": "abc"})
    assert (res.status, res.header("x-unit-error")) == (400, "invalid_value")
    assert res.json()["error"]["field"] == "since"


def test_a_negative_since_is_refused_rather_than_counted_from_the_end() -> None:
    api, _ = _api()
    assert api.get("/__unit/journal", query={"since": "-1"}).status == 400


def test_state_snapshot_restores_into_a_second_unit_with_the_same_digest() -> None:
    """The whole point of the pair: pin a scenario here, reproduce it there."""
    api_a, unit_a = _api()
    unit_a.context.store.collection("orders").insert({"id": "o1", "state": "OPEN"})
    taken = api_a.get("/__unit/state/snapshot").json()

    api_b, _ = _api()
    restored = api_b.post("/__unit/state/restore", {"snapshot": taken["snapshot"]})
    assert restored.status == 200
    assert restored.json()["digest"] == taken["digest"]
    assert api_b.get("/__unit/state").json()["entities"]["orders"] == 1


def test_a_restore_with_no_snapshot_is_missing_field_naming_snapshot() -> None:
    api, _ = _api()
    res = api.post("/__unit/state/restore", {})
    assert (res.status, res.header("x-unit-error")) == (400, "missing_field")
    assert res.json()["error"]["field"] == "snapshot"


def test_a_restore_with_a_snapshot_that_is_not_an_object_is_a_400_not_a_500() -> None:
    """The claim in one line: no control route answers 500 for valid JSON."""
    api, _ = _api()
    res = api.post("/__unit/state/restore", {"snapshot": [1, 2, 3]})
    assert res.status == 400


def test_state_reset_wipes_the_store_and_restarts_the_journal_sequence() -> None:
    """Both. A reset that emptied the collections but left `journal_seq` where
    it was would make two units seeded identically report different sequences,
    and the journal is the event source everything downstream is derived from."""
    api, unit = _api()
    unit.context.store.collection("orders").insert({"id": "o1"})
    body = api.post("/__unit/state/reset", {}).json()
    assert body["entities"].get("orders", 0) == 0
    assert body["journal_seq"] == 0
    assert unit.context.store.journal() == []


def test_state_reset_calls_the_vendors_hydrate_again() -> None:
    vendor = _vendor()
    unit = _unit(vendor=vendor)
    api = in_process(unit)
    started = vendor.hydrated
    api.post("/__unit/state/reset", {})
    assert vendor.hydrated == started + 1


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


def test_a_subscriber_can_be_registered_without_touching_the_vendor_api() -> None:
    api, _ = _api()
    res = api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    assert res.status == 201
    entity = res.json()["subscription"]
    assert entity["notification_url"] == "https://sub.test/hook"
    assert entity["event_types"] == ["*"]
    assert entity["id"].startswith("wbhk_ctl_")


def test_a_registered_subscriber_is_an_ordinary_journalled_entity() -> None:
    """Not a private list. That is what lets a vendor's own subscription API
    and the control plane produce the same kind of thing."""
    api, unit = _api()
    api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    assert any(entry.collection == "subscriptions" for entry in unit.context.store.journal())


def test_a_subscriber_with_no_notification_url_is_missing_field() -> None:
    api, _ = _api()
    res = api.post("/__unit/webhooks/subscriptions", {"name": "mine"})
    assert (res.status, res.header("x-unit-error")) == (400, "missing_field")
    assert res.json()["error"]["field"] == "notification_url"


def test_reusing_a_subscriber_id_is_a_conflict_rather_than_a_silent_overwrite() -> None:
    api, _ = _api()
    api.post("/__unit/webhooks/subscriptions", {"id": "s1", "notification_url": "https://a.test/h"})
    res = api.post("/__unit/webhooks/subscriptions", {"id": "s1", "notification_url": "https://b.test/h"})
    assert (res.status, res.header("x-unit-error")) == (409, "conflict")


def test_a_listed_subscriber_omits_the_optional_keys_it_does_not_have() -> None:
    api, _ = _api()
    api.post("/__unit/webhooks/subscriptions", {"id": "s1", "notification_url": "https://a.test/h"})
    listed = api.get("/__unit/webhooks/subscriptions").json()["subscriptions"][0]
    assert "api_version" not in listed
    assert listed["signature_key"] == "unit-signature-key"


def test_deleting_an_unknown_subscriber_is_a_404() -> None:
    api, _ = _api()
    assert api.delete("/__unit/webhooks/subscriptions/nope").status == 404


def test_an_emitted_event_travels_the_real_prepare_sign_and_deliver_path() -> None:
    """It exists because a profile with no mutating route otherwise cannot make
    a delivery happen at all, and a check that only runs where the vendor has a
    write surface tests the vendor rather than the unit."""
    sink = MemorySink()
    api, _ = _api(sink=sink)
    api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    emitted = api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o1"})
    assert emitted.status == 202
    api.post("/__unit/webhooks/drain")

    delivered = api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [d["event_id"] for d in delivered] == [emitted.json()["event_id"]]
    assert delivered[0]["status"] == "delivered"
    # The signer's hook ran: the header is spelled in the vendor's own prefix,
    # which the core could not have produced.
    assert "acme-initial-delivery" in delivered[0]["headers"]


def test_an_emitted_event_id_does_not_consume_a_draw_from_the_seeded_rng() -> None:
    """Deriving rather than drawing. A probe event that moved the RNG would
    renumber every entity id a check was about to assert on."""
    api, unit = _api()
    before = unit.context.rng.draw_count
    api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o1"})
    assert unit.context.rng.draw_count == before


def test_deliveries_can_be_filtered_by_event_type() -> None:
    api, _ = _api()
    api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o1"})
    api.post("/__unit/webhooks/emit", {"type": "order.updated", "entity_id": "o1"})
    api.post("/__unit/webhooks/drain")

    filtered = api.get("/__unit/webhooks/deliveries", query={"event_type": "order.updated"}).json()
    assert filtered["count"] == 1
    assert filtered["deliveries"][0]["event_type"] == "order.updated"


def test_drain_reports_how_many_deliveries_the_log_now_holds() -> None:
    api, _ = _api()
    api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o1"})
    assert api.post("/__unit/webhooks/drain").json()["deliveries"] == 1


def test_the_retry_policy_patch_leaves_unmentioned_knobs_alone() -> None:
    """Sparse. A full-replacement body would make a consumer restate a
    vendor's documented schedule to change one multiplier."""
    api, _ = _api()
    before = api.get("/__unit/info").json()["webhooks"]["retry"]
    after = api.post("/__unit/webhooks/retry-policy", {"time_scale": 0.25}).json()["retry"]
    assert after["time_scale"] == 0.25
    assert after["schedule_ms"] == before["schedule_ms"]
    assert after["timeout_ms"] == before["timeout_ms"]


def test_programming_the_sink_forces_a_retry_that_is_visible_over_the_wire() -> None:
    """The only way a language-independent check can observe the retry
    schedule: it cannot reach into the sink object."""
    sink = MemorySink()
    api, _ = _api(sink=sink)
    api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    programmed = api.post("/__unit/webhooks/sink", {"statuses": [500], "then": 200})
    assert programmed.status == 200
    assert programmed.json()["sink"] == "memory"

    api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o1"})
    api.post("/__unit/webhooks/drain")
    attempts = api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert [a["response_status"] for a in attempts] == [500, 200]
    assert [a["status"] for a in attempts] == ["failed", "delivered"]


def test_a_sink_programme_starts_from_the_calls_already_made() -> None:
    """`call_index` counts calls to the sink for its whole life. A programme
    that ignored the calls already made would replay itself for whoever
    happened to go first."""
    sink = MemorySink()
    api, _ = _api(sink=sink)
    api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o1"})
    api.post("/__unit/webhooks/drain")

    programmed = api.post("/__unit/webhooks/sink", {"statuses": [500], "then": 200})
    assert programmed.json()["from_call"] == len(sink.received)
    api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o2"})
    api.post("/__unit/webhooks/drain")
    second = [d for d in api.get("/__unit/webhooks/deliveries").json()["deliveries"] if d["entity_id"] == "o2"]
    assert [d["response_status"] for d in second] == [500, 200]


def test_programming_a_sink_that_is_not_the_memory_sink_is_a_conflict() -> None:
    """`conflict`, not the brief's `invalid_state`: the twenty kinds are fixed
    by a conformance check asserting the literal count, and "the unit is not in
    a state where this is possible" is what `conflict` already means."""
    import tempfile

    from vendorfake.core.webhooks.sink import FileSink

    with tempfile.TemporaryDirectory() as tmp:
        api, _ = _api(sink=FileSink(tmp))
        res = api.post("/__unit/webhooks/sink", {"statuses": [500]})
        assert (res.status, res.header("x-unit-error")) == (409, "conflict")


# ---------------------------------------------------------------------------
# clock
# ---------------------------------------------------------------------------


def test_advancing_a_real_clock_is_a_bad_request_that_says_what_to_change() -> None:
    api, _ = _api(clock_mode="real", clock_start=None)
    res = api.post("/__unit/clock/advance", {"ms": 10})
    assert (res.status, res.header("x-unit-error")) == (400, "bad_request")
    assert "virtual" in res.json()["error"]["detail"]


def test_advancing_a_virtual_clock_moves_it_and_reports_what_fired() -> None:
    api, unit = _api()
    fired: list[str] = []
    unit.context.clock.after(5, "probe", lambda: fired.append("x"))
    body = api.post("/__unit/clock/advance", {"ms": 10}).json()
    assert body["fired_timers"] == 1
    assert body["now"] == "2024-01-01T00:00:00.010Z"
    assert body["pending"] == []
    assert fired == ["x"]


def test_a_negative_advance_is_invalid_value_naming_ms() -> None:
    api, _ = _api()
    res = api.post("/__unit/clock/advance", {"ms": -1})
    assert (res.status, res.header("x-unit-error")) == (400, "invalid_value")
    assert res.json()["error"]["field"] == "ms"


def test_a_non_numeric_advance_is_a_400_rather_than_a_500() -> None:
    api, _ = _api()
    assert api.post("/__unit/clock/advance", {"ms": "soon"}).status == 400


def test_advancing_the_clock_settles_a_retry_cascade_in_one_call() -> None:
    """The whole reason `settle=` is passed. Deliveries run on one worker
    thread, so a re-scan that ran before the worker registered its next retry
    would report a twelve-attempt cascade as three, and this route would answer
    as though the subscriber had stopped failing."""
    sink = MemorySink(respond_with=500)
    api, _ = _api(sink=sink, schedule_ms=(1, 1, 1))
    api.post("/__unit/webhooks/subscriptions", {"notification_url": "https://sub.test/hook"})
    api.post("/__unit/webhooks/emit", {"type": "order.created", "entity_id": "o1"})
    api.post("/__unit/clock/advance", {"ms": 1000})
    attempts = api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [a["attempt"] for a in attempts] == [1, 2, 3, 4]
    assert attempts[-1]["status"] == "exhausted"


# ---------------------------------------------------------------------------
# machines
# ---------------------------------------------------------------------------


def test_terminal_is_derived_from_an_empty_transition_list() -> None:
    """Derived in the machine, not stored beside a `to` list it can
    contradict -- which is one of the two defects the reference's
    `state/machine.ts` carries."""
    api, _ = _api()
    states = api.get("/__unit/machines").json()["machines"]["order"]["states"]
    for name, state in states.items():
        assert state["terminal"] == (state["to"] == []), name


def test_a_legal_transition_probes_ok_and_mutates_nothing() -> None:
    api, unit = _api()
    before = unit.context.store.entity_digest()
    res = api.post("/__unit/machines/probe", {"machine": "order", "from": "OPEN", "to": "COMPLETED"})
    assert res.status == 200
    assert res.json()["ok"] is True
    assert unit.context.store.entity_digest() == before


def test_an_illegal_transition_probes_invalid_transition() -> None:
    api, _ = _api()
    res = api.post("/__unit/machines/probe", {"machine": "order", "from": "COMPLETED", "to": "OPEN"})
    assert (res.status, res.header("x-unit-error")) == (400, "invalid_transition")


def test_a_self_transition_is_legal_only_where_the_state_lists_itself() -> None:
    """The reference returns early on `from === to`, so paying an already-paid
    order succeeds and re-pays it. Here `allow_self` decides, per state."""
    api, _ = _api()
    assert api.post("/__unit/machines/probe", {"machine": "order", "from": "OPEN", "to": "OPEN"}).status == 200
    refused = api.post("/__unit/machines/probe", {"machine": "order", "from": "COMPLETED", "to": "COMPLETED"})
    assert (refused.status, refused.header("x-unit-error")) == (400, "invalid_transition")


def test_a_probe_with_no_target_asks_whether_the_entity_may_be_mutated_at_all() -> None:
    """Not just a state change: a completed order does not get new line items
    either. That is `assert_mutable`, and it is the check a terminal state
    exists to fail."""
    api, _ = _api()
    assert api.post("/__unit/machines/probe", {"machine": "order", "from": "OPEN"}).status == 200
    refused = api.post("/__unit/machines/probe", {"machine": "order", "from": "COMPLETED"})
    assert (refused.status, refused.header("x-unit-error")) == (400, "invalid_transition")


def test_a_probe_answers_one_question_per_call_and_never_both() -> None:
    """With `to`, the probe evaluates the transition predicate ALONE.

    If mutability answered first, a terminal state's transition predicate could
    never be observed through this route -- and a machine treating `from == to`
    as always legal would be undetectable on any vendor whose non-terminal
    states all allow themselves. The `allowed` list in the info block is the
    evidence that assert_transition, and not assert_mutable, produced this.
    """
    api, _ = _api()
    refused = api.post("/__unit/machines/probe", {"machine": "order", "from": "COMPLETED", "to": "CANCELED"})
    assert (refused.status, refused.header("x-unit-error")) == (400, "invalid_transition")
    info = refused.json()["error"]["info"]
    assert info["allowed"] == []
    assert info["from"] == "COMPLETED"
    assert info["to"] == "CANCELED"


def test_an_undeclared_from_state_is_invalid_value_rather_than_a_cheerful_ok() -> None:
    """Without this, a probe with no `to` would sail past `assert_mutable` --
    an unknown state is not terminal -- and answer `ok` about a state that does
    not exist."""
    api, _ = _api()
    res = api.post("/__unit/machines/probe", {"machine": "order", "from": "NONESUCH"})
    assert (res.status, res.header("x-unit-error")) == (400, "invalid_value")
    assert res.json()["error"]["field"] == "from"


def test_an_unknown_machine_is_a_404_listing_the_ones_that_exist() -> None:
    api, _ = _api()
    res = api.post("/__unit/machines/probe", {"machine": "nope", "from": "OPEN"})
    assert (res.status, res.header("x-unit-error")) == (404, "not_found")
    assert res.json()["error"]["info"]["declared"] == ["order"]


def test_a_vendor_with_no_machines_reports_an_empty_table_rather_than_failing() -> None:
    api, _ = _api(vendor=_vendor(machines={}))
    assert api.get("/__unit/machines").json() == {"count": 0, "machines": {}}


# ---------------------------------------------------------------------------
# echo
# ---------------------------------------------------------------------------


def test_a_form_encoded_body_reaches_the_handler_as_fields() -> None:
    """The exact shape that broke two of three bake-off entries, asserted with
    no vendor, no capability and no OAuth endpoint in sight -- which is the
    point: vendor #2 inherits the guarantee rather than rediscovering the trap."""
    api, _ = _api()
    res = api.post(
        "/__unit/echo",
        raw_body="grant_type=authorization_code&code=abc",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert res.json()["fields"] == {"grant_type": "authorization_code", "code": "abc"}


def test_a_repeated_form_key_is_last_wins_in_fields_and_a_list_in_fields_multi() -> None:
    """Both, because `URLSearchParams` is last-wins and a vendor that genuinely
    wants the repeats has nowhere else to get them."""
    api, _ = _api()
    body = api.post(
        "/__unit/echo",
        raw_body="scope=a&scope=b",
        headers={"content-type": "application/x-www-form-urlencoded"},
    ).json()
    assert body["fields"] == {"scope": "b"}
    assert body["fields_multi"] == {"scope": ["a", "b"]}


def test_echo_reports_the_content_type_without_its_parameters() -> None:
    api, _ = _api()
    body = api.post(
        "/__unit/echo",
        raw_body="a=1",
        headers={"content-type": "application/x-www-form-urlencoded; charset=utf-8"},
    ).json()
    assert body["content_type"] == "application/x-www-form-urlencoded"
    assert body["fields"] == {"a": "1"}


def test_echo_reports_a_json_body_and_its_exact_received_length() -> None:
    api, _ = _api()
    body = api.post("/__unit/echo", {"a": 1}).json()
    assert body["json"] == {"a": 1}
    assert body["raw_len"] == len(b'{"a":1}')


def test_echo_reports_both_query_views() -> None:
    """The conformance suite sees only what crosses the boundary, so the
    repeated-query contract needs the echo route to say what the handler saw."""
    api, _ = _api()
    body = api.post("/__unit/echo?scope=a&scope=b&flag").json()
    assert body["query"] == {"scope": "b", "flag": ""}
    assert body["query_all"] == {"scope": ["a", "b"], "flag": [""]}


def test_echo_omits_the_json_key_when_there_was_no_body_at_all() -> None:
    """`null` is a legitimate JSON body, so an always-present key could not
    distinguish "the body was null" from "there was no body"."""
    api, _ = _api()
    body = api.post("/__unit/echo").json()
    assert "json" not in body
    assert body["raw_len"] == 0


# ---------------------------------------------------------------------------
# the invariant, stated once and checked over the whole plane
# ---------------------------------------------------------------------------


def test_no_control_route_answers_five_hundred_for_a_syntactically_valid_body() -> None:
    """The module's whole reason for existing. An unmapped Pydantic
    ValidationError reaches the kernel's catch-all and becomes `internal`/500,
    where the contract is a shaped 400 carrying `x-unit-error`."""
    api, unit = _api()
    bodies: tuple[object, ...] = ({}, {"unexpected": True}, {"ms": "x"}, {"statuses": "no"}, [], "text", 1, None)
    for control in (r for r in unit.routes if r.internal and r.method == "POST"):
        for body in bodies:
            res = api.post(control.path.replace("{id}", "probe"), body)
            assert res.status != 500, f"{control.key} answered 500 for {body!r}"
            if res.status >= 400:
                assert res.header("x-unit-error"), control.key


def test_every_control_get_answers_without_a_vendor_credential() -> None:
    """The plane is `internal`, so no route here consults the auth adapter. A
    consumer locked out of `/__unit/*` by their own token being wrong has no
    way to find out why."""
    api, unit = _api(vendor=_vendor())
    for control in (r for r in unit.routes if r.internal and r.method == "GET"):
        res = api.get(control.path)
        assert res.status == 200, control.key


def test_the_control_plane_survives_a_chaos_rule_that_matches_everything() -> None:
    """Otherwise the rule that broke the unit is the rule a consumer cannot
    remove, and the fake is unrecoverable without a restart."""
    api, _ = _api()
    api.post("/__unit/chaos/rules", {"id": "all", "scope": "request", "fault": "server_error"})
    assert api.get("/__unit/health").status == 200
    assert api.delete("/__unit/chaos/rules/all").status == 200


def test_the_control_plane_survives_every_capability_being_switched_off() -> None:
    api, _ = _api()
    assert api.post("/__unit/capabilities", {"set": []}).status == 200
    assert api.get("/__unit/capabilities").status == 200
    assert api.post("/__unit/capabilities", {"enable": ["orders"]}).status == 200
