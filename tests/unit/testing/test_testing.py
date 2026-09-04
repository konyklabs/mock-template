"""``vendorfake.testing``: the consumer's fixtures, driven the way a consumer would.

Each test here is the smallest thing a consumer's own suite would do first --
hold a unit, make a seeded call, subscribe a receiver, arm a fault -- because
the module's whole job is that those things work on the first try.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx
import pytest

from tests.fakes import FakeVendor
from vendorfake.conformance.runner import resolve_target, run_check, select_checks
from vendorfake.conformance.types import Outcome
from vendorfake.core.kernel.types import UnitError
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.square.signer import verify_square_signature
from vendorfake.testing import (
    LOG_LINES,
    NO_SEED_HINT,
    ClockInfo,
    CloverSeed,
    Driver,
    ServedUnit,
    SquareSeed,
    StartedUnit,
    ToastSeed,
    serve_in_thread,
    served,
    unit,
    webhook_receiver,
)

# ---------------------------------------------------------------------------
# In process.
# ---------------------------------------------------------------------------


def test_a_square_unit_answers_a_seeded_call_through_httpx() -> None:
    with unit("square") as square:
        assert isinstance(square, StartedUnit)
        assert isinstance(square.seed, SquareSeed)
        assert square.vendor == "square"
        assert square.profile == "full"
        locations = square.client.get("/v2/locations", headers=square.seed.auth)
        assert locations.status_code == 200
        assert square.seed.location_id in [row["id"] for row in locations.json()["locations"]]
        assert square.health()["status"] == "ok"


def test_a_clover_unit_answers_a_seeded_call_under_its_merchant() -> None:
    with unit("clover") as clover:
        assert isinstance(clover.seed, CloverSeed)
        items = clover.client.get(clover.seed.path("/items"), headers=clover.seed.auth)
        assert items.status_code == 200
        assert clover.seed.item_beer_id in [row["id"] for row in items.json()["elements"]]


def test_the_transport_carries_query_strings_and_repeated_keys() -> None:
    with unit("clover") as clover:
        # `expand` is a query parameter Clover reads; a transport that dropped
        # the query string would return the bare item.
        item = clover.client.get(
            clover.seed.path(f"/items/{clover.seed.item_espresso_id}"),
            params={"expand": "modifierGroups"},
            headers=clover.seed.auth,
        ).json()
        assert "modifierGroups" in item
    with unit("square") as square:
        # `types` repeated: the last value wins in the scalar view, and both
        # reach the unit through query_all.
        catalog = square.client.get(
            "/v2/catalog/list",
            params=[("types", "ITEM"), ("types", "ITEM_VARIATION")],
            headers=square.seed.auth,
        )
        assert catalog.status_code == 200, catalog.text


def test_a_vendor_error_comes_back_as_the_vendor_shapes_it() -> None:
    with unit("square") as square:
        refused = square.client.get("/v2/locations")
        assert refused.status_code == 401
        assert refused.json()["errors"][0]["category"] == "AUTHENTICATION_ERROR"
        assert refused.headers["x-unit-error"]


def test_the_process_environment_is_ignored_unless_passed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The isolation invariant, tested where it can actually fail.

    ``VENDORFAKE_CLOCK`` is the probe because no explicit ``unit()`` argument
    names the clock: if ``unit()`` ever read ``os.environ``, the ambient
    variable would flip a consumer's units to a virtual clock with nothing
    overriding it. (An earlier version probed ``VENDORFAKE_PROFILE``, which
    ``unit()`` always passes explicitly and ``load_profile`` then never looks
    up -- that test passed regardless of what ``unit()`` did with ``env``.)
    """
    monkeypatch.setenv("VENDORFAKE_CLOCK", "virtual")
    with unit("square") as square:
        assert square.info()["clock"]["mode"] == "real"
        with pytest.raises(RuntimeError, match="answered 400"):
            square.advance_clock(1000)
    # The same variable, passed in deliberately, is honoured.
    with unit("square", env={"VENDORFAKE_CLOCK": "virtual"}) as square:
        assert square.info()["clock"]["mode"] == "virtual"
        square.advance_clock(1000)
    assert os.environ["VENDORFAKE_CLOCK"] == "virtual"


def test_the_profile_argument_wins_and_the_default_is_full() -> None:
    with unit("square") as square:
        assert square.profile == "full"
    with unit("square", "oauth-only") as square:
        assert square.profile == "oauth-only"
        assert square.client.get("/v2/locations", headers=square.seed.auth).status_code == 501


# ---------------------------------------------------------------------------
# clock_start and Driver.clock() (konyklabs/roadmap#71, D1)
# ---------------------------------------------------------------------------


def test_driver_clock_reports_the_mode_and_the_pinned_instant() -> None:
    with unit("square", env={"VENDORFAKE_CLOCK": "virtual"}, clock_start="2026-01-01T00:00:00Z") as square:
        info = square.clock()
        assert isinstance(info, ClockInfo)
        assert info.mode == "virtual"
        assert info.now == datetime(2026, 1, 1, tzinfo=UTC)
        square.advance_clock(1_000)
        assert square.clock().now == datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)


def test_clock_start_accepts_a_timezone_aware_datetime_the_same_as_a_string() -> None:
    as_string = datetime(2026, 6, 15, 12, 30, tzinfo=UTC)
    with unit("square", env={"VENDORFAKE_CLOCK": "virtual"}, clock_start=as_string) as square:
        assert square.clock().now == as_string
    with unit("square", env={"VENDORFAKE_CLOCK": "virtual"}, clock_start="2026-06-15T12:30:00Z") as square:
        assert square.clock().now == as_string


def test_a_naive_clock_start_datetime_raises_rather_than_guessing_local_time() -> None:
    with (
        pytest.raises(ValueError, match="no timezone"),
        unit("square", env={"VENDORFAKE_CLOCK": "virtual"}, clock_start=datetime(2026, 1, 1)),
    ):
        pass  # pragma: no cover - the context manager never yields


def test_clock_start_on_a_real_clock_refuses_rather_than_silently_switching_modes() -> None:
    with pytest.raises(UnitError, match="virtual"), unit("square", clock_start="2026-01-01T00:00:00Z"):
        pass  # pragma: no cover - the context manager never yields


def test_served_s_clock_start_reaches_the_child_through_this_process_s_own_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child inherits this process's ``os.environ`` (see ``served``'s
    docstring), and ``clock_start`` is layered onto exactly that -- so a mode
    set in the shell, with no ``env=`` at all, still reaches it. The mapping
    form of the same test is the one below."""
    monkeypatch.setenv("VENDORFAKE_CLOCK", "virtual")
    with served("square", "no-faults", clock_start="2026-01-01T00:00:00Z") as child:
        info = child.clock()
        assert info.mode == "virtual"
        assert info.now == datetime(2026, 1, 1, tzinfo=UTC)


def test_served_s_env_is_a_layer_over_the_inherited_environment_that_beats_it_and_never_writes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consumer feedback item 20 (konyklabs/roadmap#102): a suite that wants
    two differently-configured children in one process had to mutate its own
    ``os.environ`` between fixtures, which is unsafe under xdist. ``env=``
    is the per-child layer instead, and this pins its three properties at
    once: an entry beats the ambient variable of the same name, ``clock_start``
    still layers beneath it, and this process's environment is untouched
    after the call.
    """
    monkeypatch.setenv("VENDORFAKE_CLOCK", "real")
    monkeypatch.delenv("VENDORFAKE_CLOCK_START", raising=False)
    with served(
        "square", "no-faults", env={"VENDORFAKE_CLOCK": "virtual"}, clock_start="2026-01-01T00:00:00Z"
    ) as child:
        info = child.clock()
        assert info.mode == "virtual"
        assert info.now == datetime(2026, 1, 1, tzinfo=UTC)
    assert os.environ["VENDORFAKE_CLOCK"] == "real"
    assert "VENDORFAKE_CLOCK_START" not in os.environ
    # An explicit VENDORFAKE_CLOCK_START in the mapping wins over the kwarg,
    # exactly as it does for unit() -- one mapping means one thing to both.
    with served(
        "square",
        "no-faults",
        env={"VENDORFAKE_CLOCK": "virtual", "VENDORFAKE_CLOCK_START": "2026-06-15T12:30:00Z"},
        clock_start="2026-01-01T00:00:00Z",
    ) as child:
        assert child.clock().now == datetime(2026, 6, 15, 12, 30, tzinfo=UTC)


def test_served_refuses_a_seed_document_in_env_before_spawning_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review found that ``VENDORFAKE_SEED`` in the mapping made the child
    hydrate from one document while ``.seed`` -- derived from the vendor's
    constants, never from a document -- described another: a measured 401 on
    ``.seed.auth``. Refused eagerly, and without a child ever being spawned."""
    import vendorfake.testing as testing

    monkeypatch.setattr(testing, "SERVE_COMMAND", (sys.executable, "-c", "raise SystemExit('spawned')"))
    with pytest.raises(ValueError, match="VENDORFAKE_SEED"):  # noqa: SIM117 - the `with served(...)` is the subject
        with served("square", "no-faults", env={"VENDORFAKE_SEED": "/nope/other.seed.json"}) as driver:
            pytest.fail(f"served() yielded {driver!r} with a seed document in env=")


@pytest.mark.parametrize(
    "name", ["VENDORFAKE_PROFILE", "VENDORFAKE_HOST", "VENDORFAKE_PORT", "VENDORFAKE_LOG_LEVEL", "VENDORFAKE_TRANSPORT"]
)
def test_served_refuses_an_env_entry_a_flag_would_beat_rather_than_ignoring_it(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """konyklabs/roadmap#105: ``_served`` passes profile, host, port and
    log level to the child as flags, and ``serve`` only binds HTTP, so an
    entry for any of the five would change nothing -- a silent no-op in a
    library whose pitch is strictness. Refused before a child is spawned,
    naming the parameter to use; the ambient variable of the same name is
    still inherited untouched, because that is the shell's business."""
    import vendorfake.testing as testing

    monkeypatch.setattr(testing, "SERVE_COMMAND", (sys.executable, "-c", "raise SystemExit('spawned')"))
    # The explanation, not just the name: the transport has no parameter to
    # point at, and a message that said "use the parameter" for it sent the
    # reader looking for one (review of konyklabs/roadmap#105).
    explanation = "only ever binds HTTP" if name == "VENDORFAKE_TRANSPORT" else "Use the parameter instead"
    with pytest.raises(ValueError, match=name) as refused:  # noqa: SIM117 - the `with served(...)` is the subject
        with served("square", "no-faults", env={name: "x"}) as driver:
            pytest.fail(f"served() yielded {driver!r} with {name} in env=")
    assert explanation in str(refused.value)


def test_served_s_env_credential_override_reaches_the_child_and_the_seed_alike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The example from the feedback: a second Square child with a deliberately
    different application secret, to rehearse a misconfigured consumer without
    a chaos rule. The override has to land in two places or the fixture lies:
    the child's real config (what ``/__unit/auth`` offers, what
    ``POST /oauth2/revoke`` checks) and the parent-resolved ``.seed`` handed
    back -- the same drift the 0.2 review caught for the ambient case. Two
    children in one process disagree only where told to.
    """
    monkeypatch.delenv("VENDORFAKE_VENDOR_APPLICATION_SECRET", raising=False)

    def client_secret(child: ServedUnit[SquareSeed]) -> str:
        offered = child.client.get("/__unit/auth").json()["credentials"]
        (row,) = [credential for credential in offered if credential["label"] == "client-secret"]
        return str(row["headers"]["authorization"])

    with (
        served("square", "no-faults") as stock,
        served("square", "no-faults", env={"VENDORFAKE_VENDOR_APPLICATION_SECRET": "sandbox-sq0csb-from-env"}) as odd,
    ):
        assert odd.seed.credentials.app_secret == "sandbox-sq0csb-from-env"
        assert client_secret(odd) == "Client sandbox-sq0csb-from-env"
        assert stock.seed.credentials.app_secret != "sandbox-sq0csb-from-env"
        assert client_secret(stock) == f"Client {stock.seed.credentials.app_secret}"
        # The child really checks the overridden secret, not just reports it.
        refused = odd.client.post(
            "/oauth2/revoke", headers={"authorization": f"Client {stock.seed.credentials.app_secret}"}
        )
        assert refused.status_code == 401
    assert "VENDORFAKE_VENDOR_APPLICATION_SECRET" not in os.environ


def test_unit_resolves_profile_through_the_argument_then_env_then_the_default() -> None:
    """The three-level precedence :func:`~vendorfake.registry.create_unit`
    documents, pinned at all three levels: an explicit ``profile=`` argument
    beats ``VENDORFAKE_PROFILE`` in the ``env=`` mapping, which beats the
    ``full`` default.

    **Behaviour change**, recorded in ``CHANGELOG.md``: v0.1.0's ``unit()``
    passed the literal string ``"full"`` to ``create_unit`` regardless of
    ``env``, so ``env={"VENDORFAKE_PROFILE": ...}`` was silently ignored for
    this call. A caller who builds one ``env`` mapping for a whole test
    module and passes it to both :func:`~vendorfake.testing.served` and
    :func:`~vendorfake.testing.unit` now gets the same profile from both.
    """
    # Level 3: neither an argument nor VENDORFAKE_PROFILE -- the "full" default.
    with unit("square") as square:
        assert square.profile == "full"
    # Level 2: no explicit profile= -- VENDORFAKE_PROFILE in env= wins.
    with unit("square", env={"VENDORFAKE_PROFILE": "oauth-only"}) as square:
        assert square.profile == "oauth-only"
    # Level 1: an explicit profile= beats VENDORFAKE_PROFILE in env=, even
    # when the two name different, both-real profiles.
    with unit("square", "no-faults", env={"VENDORFAKE_PROFILE": "oauth-only"}) as square:
        assert square.profile == "no-faults"


def test_a_memory_sink_captures_instead_of_delivering() -> None:
    sink = MemorySink()
    with unit("square", sink=sink) as square:
        square.subscribe("http://127.0.0.1:1/never", ["order.created"], "k")
        _create_square_order(square)
        square.drain()
        assert len(sink.received) == 1
        assert sink.received[0].url == "http://127.0.0.1:1/never"


def test_a_receiver_on_loopback_gets_a_signed_delivery_and_a_retry() -> None:
    with unit("square") as square, webhook_receiver() as receiver:
        receiver.respond_with = lambda index: 500 if index == 0 else 200
        square.subscribe(receiver.url, ["order.created"], "receiver-key")
        _create_square_order(square)
        # The delivery worker runs on its own thread: wait_for is the
        # consumer's way to meet it, drain settles the bookkeeping after.
        assert len(receiver.wait_for(2)) == 2
        square.drain()

        first, retry = receiver.received
        for delivery in (first, retry):
            assert verify_square_signature(
                "receiver-key", receiver.url, delivery.body, delivery.header("x-square-hmacsha256-signature") or ""
            )
        assert json.loads(first.body)["event_id"] == json.loads(retry.body)["event_id"]
        assert retry.header("square-retry-number") == "1"
        assert [row["status"] for row in square.deliveries()] == ["failed", "delivered"]


def test_a_receiver_answers_404_off_its_path_and_records_nothing() -> None:
    """A handler mounted on one path and subscribed on another must fail,
    as it would against the vendor -- not record the delivery anyway."""
    with webhook_receiver(path="/hooks/square") as receiver:
        wrong = receiver.url.replace("/hooks/square", "/webhooks")
        assert httpx.post(wrong, content=b'{"nope": true}').status_code == 404
        assert receiver.received == []
        assert httpx.post(receiver.url, content=b'{"yes": true}').status_code == 200
        assert [delivery.body for delivery in receiver.received] == [b'{"yes": true}']
        with pytest.raises(AssertionError, match="expected 2 webhook deliveries"):
            receiver.wait_for(2, timeout_s=0.2)


def test_a_receiver_can_bind_all_interfaces_for_a_container_and_still_name_loopback() -> None:
    with webhook_receiver(host="0.0.0.0") as receiver:
        # `url` names loopback -- the routable address for a wildcard bind
        # depends on who is asking (host.docker.internal, ...), so the class
        # publishes `port` and the recipe rather than guessing.
        assert receiver.url == f"http://127.0.0.1:{receiver.port}/webhooks"
        assert httpx.post(receiver.url, content=b"{}").status_code == 200
        assert len(receiver.received) == 1


def test_reset_drops_control_plane_subscribers_and_keeps_seed_and_profile_ones(tmp_path: Any) -> None:
    """Pins what reset()'s docstring says: the scenario's subscriber and the
    profile's survive a reset because hydrate re-inserts them; the one a test
    registered through the control plane is gone and must be re-registered."""
    from importlib.resources import files

    profile = json.loads((files("vendorfake.clover") / "profiles" / "full.json").read_text())
    profile["webhooks"]["subscribers"] = [
        {
            "id": "wbhk_profile_cfg",
            "notification_url": "https://example.test/profile",
            "event_types": ["O:*"],
            "signature_key": "cfg",
            "enabled": False,
        }
    ]
    profile_path = tmp_path / "with-subscriber.json"
    profile_path.write_text(json.dumps(profile))

    with unit("clover", str(profile_path)) as clover:

        def ids() -> set[str]:
            return {row["id"] for row in clover.client.get("/__unit/webhooks/subscriptions").json()["subscriptions"]}

        seed_id = clover.seed.webhook_subscription_id
        assert ids() == {seed_id, "wbhk_profile_cfg"}
        clover.subscribe("http://127.0.0.1:1/mine", ["O:*"], "mine", id="wbhk_mine")
        assert ids() == {seed_id, "wbhk_profile_cfg", "wbhk_mine"}
        clover.reset()
        assert ids() == {seed_id, "wbhk_profile_cfg"}


def test_two_units_mint_the_same_ids_unless_reseeded() -> None:
    with unit("square") as first, unit("square") as second, unit("square", seed=2) as diverged:
        same = _create_square_order(first)["id"], _create_square_order(second)["id"]
        assert same[0] == same[1]
        assert _create_square_order(diverged)["id"] != same[0]
        # Separate stores: the order first minted is not visible to second.
        assert second.client.get(f"/v2/orders/{same[0]}", headers=second.seed.auth).status_code == 200
        assert first.reset()["entities"]["orders"] == 2
        assert second.client.get(f"/v2/orders/{same[0]}", headers=second.seed.auth).status_code == 200


def test_a_chaos_rule_and_a_reset_through_the_driver() -> None:
    with unit("square") as square:
        square.add_chaos_rule(
            {
                "id": "t-429",
                "scope": "request",
                "fault": "rate_limit",
                "match": {"route": "GET /v2/locations"},
                "when": {"nth": [1]},
            }
        )
        auth = square.seed.auth
        assert square.client.get("/v2/locations", headers=auth).status_code == 429
        assert square.client.get("/v2/locations", headers=auth).status_code == 200
        square.reset_chaos()
        assert square.client.get("/v2/locations", headers=auth).status_code == 200

        _create_square_order(square)
        assert square.reset()["entities"]["orders"] == 2


def test_a_driver_refuses_loudly_when_the_control_plane_does() -> None:
    with unit("square") as square, pytest.raises(RuntimeError, match="answered 400"):
        square.advance_clock(1000)  # a real clock cannot be advanced


def test_a_toast_unit_answers_seeded_calls_under_the_restaurant_header() -> None:
    with unit("toast") as toast:
        assert isinstance(toast.seed, ToastSeed)
        seed = toast.seed
        menus = toast.client.get("/menus/v3/menus", headers=seed.auth)
        assert menus.status_code == 200
        assert menus.json()["restaurantGuid"] == seed.restaurant_guid
        order = toast.client.get(f"/orders/v2/orders/{seed.open_order_guid}", headers=seed.auth)
        assert order.status_code == 200
        # Toast scopes with the header, not the path: the bearer alone is
        # refused, which is why the seed pairs them in `auth`.
        bare = toast.client.get("/menus/v3/menus", headers=seed.bearer_only)
        assert bare.status_code == 400
        assert "Toast-Restaurant-External-ID" in bare.json()["message"]
        with pytest.raises(ValueError, match="'toast' sends none of"):
            toast.subscribe("http://127.0.0.1:1/x", ["order.created"], "sec")
        toast.subscribe("http://127.0.0.1:1/x", ["order_updated", "menus_updated"], "sec")


def test_every_installed_vendor_ships_a_conformance_target_and_a_seed() -> None:
    """The gap class this pins: a fourth vendor landing without a shipped
    target or seed. Toast was that gap once -- it merged while this package
    still described two vendors -- and the instance is less interesting than
    the rule. Every name the registry resolves must have a ``<vendor>_target``
    factory in ``vendorfake.testing.conformance``, and a started unit must
    carry a seed with a bearer and a non-empty event vocabulary
    (``subscribe``'s validation runs on the latter).

    Driven through :func:`unit` rather than calling ``seed_for`` directly, so
    the config keys the seed reads are the ones a real unit publishes: a seed
    that only builds from a hand-written mapping would pass while the
    consumer path raised."""
    from vendorfake import available_vendors
    from vendorfake.testing import conformance as shipped

    vendors = available_vendors()
    assert len(vendors) >= 3
    for vendor in vendors:
        factory = getattr(shipped, f"{vendor}_target", None)
        assert factory is not None, f"vendorfake.testing.conformance ships no {vendor}_target"
        built = factory()
        assert built.name == vendor
        assert tuple(built.profiles) == shipped.PROFILES
        with unit(vendor) as driver:
            seed = driver.seed
            assert seed is not None, f"vendorfake.testing.seeds.seed_for knows no {vendor!r}"
            assert seed.event_types, f"{vendor!r} publishes no event types for subscribe() validation"
            assert "Authorization" in seed.auth, f"{vendor!r}'s seed.auth carries no bearer"


def test_no_vendor_lets_a_control_plane_read_consume_or_wander() -> None:
    """The gap class Toast's error catalogue was an instance of: a *read* on
    the control plane that is not a pure read.

    Two ways it went wrong at once, and both are checked for every installed
    vendor rather than for Toast. ``GET /__unit/errors`` shaped all twenty
    kinds through the live refusal path, so it drew twenty-one ids from the
    vendor's request-id stream -- a diagnostic GET silently renumbered every
    id in the caller's remaining scenario. And its 429 row carried
    ``floor(now/1000)``, so two renderings disagreed across a wall-clock
    second; that is what failed conformance C10 on CI, where the two bindings
    are called far enough apart to straddle one.

    The clock half is driven on a *virtual* clock advanced by an hour, which
    is the same observation a slow runner makes and takes no wall time. Only
    the catalogue is compared byte for byte: ``/__unit/info`` and
    ``/__unit/health`` report the clock and the request counters, so moving is
    what they are for, while a description of a static table has no such
    excuse.
    """
    from vendorfake import available_vendors

    for vendor in available_vendors():
        with unit(vendor, env={"VENDORFAKE_CLOCK": "virtual"}) as driver:
            first = driver.client.get("/__unit/errors")
            driver.advance_clock(3_600_000)
            second = driver.client.get("/__unit/errors")
            assert second.content == first.content, (
                f"{vendor}: GET /__unit/errors answered differently after the clock moved "
                f"({len(first.content)} vs {len(second.content)} bytes)"
            )

            # No control-plane read may consume an id, on any vendor: a
            # diagnostic GET that drew one would renumber everything the
            # caller mints afterwards.
            streams = {
                name: getattr(driver.unit.context.vendor, name)
                for name in ("ids", "request_ids")
                if hasattr(driver.unit.context.vendor, name)
            }
            reads = [
                row["path"]
                for row in driver.client.get("/__unit/routes").json()["routes"]
                if row["path"].startswith("/__unit/") and row["method"] == "GET"
            ]
            before = {name: stream.draw_count for name, stream in streams.items()}
            for path in reads:
                driver.client.get(path)
            after = {name: stream.draw_count for name, stream in streams.items()}
            assert after == before, f"{vendor}: {len(reads)} control-plane reads drew ids, {before} -> {after}"


def test_no_internal_marker_reaches_a_wire_body_from_any_vendor() -> None:
    """``UnitError.info`` is published verbatim in the ``unit_error`` sidecar,
    so nothing internal may be routed through it.

    The regression this pins is one the catalogue fix introduced and the gate
    on #31 caught: the first version signalled "describe, do not consume" with
    an ``info`` key, and that key was then rendered into the sidecar of all
    twenty rows on all three vendors -- an internal control-plane flag on a
    consumer-visible wire. The signal is an argument to ``shape`` now, and
    this checks the consequence rather than the spelling: no dunder-prefixed
    key anywhere in a described body or in a real refusal's sidecar.
    """
    from vendorfake import available_vendors

    def dunder_keys(node: Any, trail: str = "") -> list[str]:
        if isinstance(node, dict):
            found = [f"{trail}.{k}" for k in node if isinstance(k, str) and k.startswith("__")]
            for key, value in node.items():
                found += dunder_keys(value, f"{trail}.{key}")
            return found
        if isinstance(node, list):
            return [hit for i, item in enumerate(node) for hit in dunder_keys(item, f"{trail}[{i}]")]
        return []

    for vendor in available_vendors():
        # Two opt-outs, one for each stream that touched this test.
        # `unmatched="vendor-404"` (konyklabs/roadmap#72): this test's whole
        # subject is the BODY of a real refusal, so it is one of the cases the
        # strict default is not for -- it probes an unmodelled path
        # deliberately. `errors.sidecar=both` (konyklabs/roadmap#71): the check
        # below wants the sidecar as a real dict it can walk, and `both` is the
        # one mode that still puts it there without giving up coverage of the
        # headers form (both channels build from the same dict; see
        # core/kernel/shaping.py).
        with unit(vendor, unmatched="vendor-404", env={"VENDORFAKE_ERROR_SIDECAR": "both"}) as driver:
            catalogue = driver.client.get("/__unit/errors")
            assert dunder_keys(catalogue.json()) == [], f"{vendor}: internal key in the error catalogue"
            # The sidecar has to be ON, or this proves nothing: the leak lived
            # in `unit_error`, which a profile can switch off.
            assert "unit_error" in catalogue.json()["kinds"][0]["body"], f"{vendor}: no sidecar to leak into"

            refused = driver.client.get("/definitely/not/a/route/at/all")
            assert refused.status_code == 404, f"{vendor}: expected a 404, got {refused.status_code}"
            assert dunder_keys(refused.json()) == [], f"{vendor}: internal key in a real refusal"


def test_the_error_sidecar_rides_headers_by_default_and_errors_sidecar_moves_it() -> None:
    """konyklabs/roadmap#71 D2: no 4xx or 5xx body from any vendor contains
    `unit_error` under the default profile -- the whole point being that a
    consumer substituting a recorded real response for this fake's answer
    should see no field the real vendor never sends. `errors.sidecar` says
    where the one dict `unit_error_sidecar` builds rides; walked here through
    every row of `GET /__unit/errors` -- all twenty kinds plus `no_route` --
    not just the first. A regression confined to one shaper branch (a single
    kind, or the not-found path) would not show up if only the first kind
    were ever inspected, which is exactly the gap an earlier review round
    found in this test.
    """
    from vendorfake import available_vendors

    kind_header = "Vendorfake-Error-Kind"
    provenance_header = "Vendorfake-Status-Provenance"

    def rows(catalogue: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Every kind, labelled by its own name, plus `no_route` last."""
        return [(row["kind"], row) for row in catalogue["kinds"]] + [("no_route", catalogue["no_route"])]

    for vendor in available_vendors():
        with unit(vendor) as driver:  # the true default: no env at all
            catalogue = driver.client.get("/__unit/errors").json()
            assert catalogue["count"] == len(catalogue["kinds"])
            for label, row in rows(catalogue):
                assert "unit_error" not in row["body"], (
                    f"{vendor}/{label}: default profile still leaks unit_error into the body"
                )
                assert row["headers"].get(kind_header), f"{vendor}/{label}: default profile has no sidecar kind header"
                assert row["headers"].get(provenance_header), (
                    f"{vendor}/{label}: default profile has no sidecar provenance header"
                )

        with unit(vendor, env={"VENDORFAKE_ERROR_SIDECAR": "body"}) as driver:
            catalogue = driver.client.get("/__unit/errors").json()
            for label, row in rows(catalogue):
                assert "unit_error" in row["body"], f"{vendor}/{label}: errors.sidecar=body dropped the v0.1 body key"
                assert kind_header not in row["headers"], f"{vendor}/{label}: errors.sidecar=body still emits headers"

        with unit(vendor, env={"VENDORFAKE_ERROR_SIDECAR": "both"}) as driver:
            catalogue = driver.client.get("/__unit/errors").json()
            for label, row in rows(catalogue):
                assert "unit_error" in row["body"], f"{vendor}/{label}: errors.sidecar=both dropped the body key"
                assert row["headers"].get(kind_header), f"{vendor}/{label}: errors.sidecar=both dropped the headers"


# ---------------------------------------------------------------------------
# The sidecar headers are ASCII-safe (konyklabs/roadmap#71 review, blocker)
# ---------------------------------------------------------------------------

_INFO_HEADER = "Vendorfake-Error-Info"
_FIELD_HEADER = "Vendorfake-Error-Field"


def test_a_non_ascii_chaos_rule_id_survives_both_transports() -> None:
    """A chaos rule's own `id` reaches `UnitError.info` verbatim
    (`core/chaos/faults.py`'s `rule = decision.rule_id`), and it is
    consumer-supplied text with no ASCII guarantee. Before this fix: in
    process, `httpx.Response(headers=...)` raised `UnicodeEncodeError` before
    a response ever came back; over real HTTP, the ASGI stack's Latin-1
    header encoding turned the intended 429 into an unrelated 500 with no
    sidecar headers at all.
    """
    rule_id = "sushi-寿司-rule"
    rule = {
        "id": rule_id,
        "scope": "request",
        "fault": "rate_limit",
        "match": {"route": "GET /v2/locations"},
        "when": {"nth": [1]},
    }

    with unit("square") as square:
        square.add_chaos_rule(rule)
        response = square.client.get("/v2/locations", headers=square.seed.auth)
        assert response.status_code == 429
        info = json.loads(response.headers[_INFO_HEADER])
        assert info["chaos_rule"] == rule_id

    with unit("square") as square, serve_in_thread(square) as over_http:
        square.add_chaos_rule(rule)
        response = over_http.client.get("/v2/locations", headers=square.seed.auth)
        assert response.status_code == 429
        info = json.loads(response.headers[_INFO_HEADER])
        assert info["chaos_rule"] == rule_id


def test_a_non_ascii_entity_id_survives_both_transports() -> None:
    """A consumer-supplied entity id reaches `UnitError.info` verbatim from a
    URL path segment rather than a request body, which is the same failure
    family as the chaos rule id above (a body field) from a different
    consumer-supplied string: `DELETE /__unit/chaos/rules/{id}` on a rule
    that was never added raises `chaos_rule_delete`'s own inline `UnitError`
    (`core/control/plane.py`), `info={"id": entity_id}`.

    CORRECTION (konyklabs/roadmap#71 review round 2): an earlier version of
    this docstring claimed the `info` shape matched `core/state/store.py`'s
    generic not-found -- `Collection.require` / `Collection.update`, which
    carries a `collection` key alongside `id` (`info={"collection": ...,
    "id": ...}`, pinned in isolation by
    `tests/unit/core/test_state_store.py::test_require_raises_not_found_where_get_returns_none`).
    That was wrong: `chaos_rule_delete` raises its own `UnitError` inline and
    never reaches the store for this miss, so its `info` never carries a
    `collection` key. What this test actually pins is narrower and still
    real -- a non-ASCII id survives ASCII-safe header encoding when it
    arrives as a *path segment* -- and
    :func:`test_a_non_ascii_id_survives_both_transports_through_the_stores_own_not_found`
    below is what proves the store's own two-key shape.
    """
    entity_id = "order-寿司-99"
    path = f"/__unit/chaos/rules/{quote(entity_id, safe='')}"

    with unit("square") as square:
        response = square.client.delete(path)
        assert response.status_code == 404
        info = json.loads(response.headers[_INFO_HEADER])
        assert info["id"] == entity_id

    with unit("square") as square, serve_in_thread(square) as over_http:
        response = over_http.client.delete(path)
        assert response.status_code == 404
        info = json.loads(response.headers[_INFO_HEADER])
        assert info["id"] == entity_id


def test_a_non_ascii_id_survives_both_transports_through_the_stores_own_not_found() -> None:
    """The *store's* own generic not-found -- `core/state/store.py`'s
    `Collection.update` (`Collection.require`'s sibling; both build
    `info={"collection": self.name, "id": entity_id}` the same way, and
    `Collection.require`'s shape is pinned in isolation by
    `tests/unit/core/test_state_store.py::test_require_raises_not_found_where_get_returns_none`)
    -- carries a non-ASCII id through ASCII-safe header encoding, over both
    transports.

    No *vendor* business route reaches this shape: every vendor's own
    not-found -- `_require_order`, `_require_item`, `_require_payment`, and
    their siblings across Square, Clover and Toast -- raises its own
    `UnitError` with the vendor's own wording, by design, so that a
    consumer's test sees what the real vendor would say rather than this
    fake's internal bookkeeping. `POST /__unit/state/update` is the one
    route that reaches the store's generic shape directly, and
    `core/control/schemas.py::StateUpdateBody` says why it exists at all:
    "a check that could only reach it through whichever endpoint a
    particular vendor happens to expose... would be a contract about that
    vendor instead." Run against Square and Clover, so the shape is shown to
    be generic to the store and not a coincidence of one vendor's plumbing.
    """
    entity_id = "order-寿司-99"
    body = {"collection": "orders", "id": entity_id, "patch": {}}

    for vendor in ("square", "clover"):
        with unit(vendor) as driver:
            response = driver.client.post("/__unit/state/update", json=body)
            assert response.status_code == 404
            info = json.loads(response.headers[_INFO_HEADER])
            assert info == {"collection": "orders", "id": entity_id}

        with unit(vendor) as driver, serve_in_thread(driver) as over_http:
            response = over_http.client.post("/__unit/state/update", json=body)
            assert response.status_code == 404
            info = json.loads(response.headers[_INFO_HEADER])
            assert info == {"collection": "orders", "id": entity_id}


def test_a_non_ascii_field_name_survives_both_transports() -> None:
    """An extra-forbidden key's own name becomes both `field` and an `info`
    entry (`core/config/models.py`'s `unit_error_from_validation`, reached
    here through a chaos rule document's `extra="forbid"` schema) -- the one
    path that exercises `Vendorfake-Error-Field`'s percent-encoding and
    `Vendorfake-Error-Info`'s ASCII-safe JSON on the same response. The field
    header must also round-trip through `urllib.parse.unquote` back to the
    exact key a consumer wrote.

    Toast, not Square: Square's own error body already names the field, so
    its `errors.py` never puts `field` into the sidecar at all (see
    `sidecar_headers`'s docstring) and this response would carry no
    `Vendorfake-Error-Field` header to check. Toast (like Clover) passes
    `field=err.field or None` through, which is the vendor family this header
    actually exists for.
    """
    bad_key = "寿司"
    document = {
        "id": "r-field",
        "scope": "request",
        "fault": "rate_limit",
        "match": {"route": "GET /menus/v3/menus"},
        bad_key: "unexpected",
    }

    with unit("toast") as toast:
        response = toast.client.post("/__unit/chaos/rules", json=document)
        assert response.status_code == 400
        assert unquote(response.headers[_FIELD_HEADER]) == bad_key
        info = json.loads(response.headers[_INFO_HEADER])
        assert info["errors"][0]["field"] == bad_key

    with unit("toast") as toast, serve_in_thread(toast) as over_http:
        response = over_http.client.post("/__unit/chaos/rules", json=document)
        assert response.status_code == 400
        assert unquote(response.headers[_FIELD_HEADER]) == bad_key
        info = json.loads(response.headers[_INFO_HEADER])
        assert info["errors"][0]["field"] == bad_key


# ---------------------------------------------------------------------------
# A URL: in a thread, and in a child process.
# ---------------------------------------------------------------------------


def test_serve_in_thread_shares_state_with_the_in_process_client() -> None:
    with unit("square") as square, serve_in_thread(square) as over_http:
        assert isinstance(over_http, Driver)
        assert over_http.base_url.startswith("http://127.0.0.1:")
        order = _create_square_order(square)
        fetched = over_http.client.get(f"/v2/orders/{order['id']}", headers=square.seed.auth)
        assert fetched.status_code == 200
        assert fetched.json()["order"]["id"] == order["id"]


def _slow_retry_square_profile(tmp_path: Any) -> str:
    """Square's full profile with one unscaled seven-second retry: long
    enough that a retry is demonstrably *pending* while a test looks, short
    enough that the unit's own stop() -- which drains for real -- does not
    hold the suite the way a genuinely uncompressed schedule would."""
    from importlib.resources import files

    profile = json.loads((files("vendorfake.square") / "profiles" / "full.json").read_text())
    profile["webhooks"]["retry"]["schedule_ms"] = [7_000]
    profile["webhooks"]["retry"]["time_scale"] = 1.0
    path = tmp_path / "slow-retry.json"
    path.write_text(json.dumps(profile))
    return str(path)


def _await_pending_timers(driver: Driver, *, timeout_s: float = 10.0) -> list[dict[str, Any]]:
    """Block until the dispatcher has a retry on the clock, and return them.

    A delivery receipt does NOT mean the retry exists yet, which is the race
    these two tests used to run: ``WebhookReceiver``'s handler appends to
    ``received`` *before* it computes the status and writes the response, and
    the timer is only scheduled once that response has crossed back over
    loopback, been recorded and re-signed on the worker thread. The gap is
    about a millisecond on an idle machine and unbounded on a loaded one, so
    the timer is waited for directly rather than inferred.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pending = driver.pending_webhook_timers()
        if pending:
            return pending
        time.sleep(0.005)
    raise AssertionError(
        f"no webhook retry timer was scheduled within {timeout_s}s; deliveries so far: {driver.deliveries()}"
    )


def test_pending_webhook_timers_sees_a_scheduled_retry(tmp_path: Any) -> None:
    with unit("square", _slow_retry_square_profile(tmp_path)) as square, webhook_receiver() as receiver:
        receiver.respond_with = lambda index: 500 if index == 0 else 200
        square.subscribe(receiver.url, ["order.created"], "k")
        assert square.pending_webhook_timers() == []
        _create_square_order(square)
        (timer,) = _await_pending_timers(square)
        assert str(timer["label"]).startswith("webhook")
        assert float(timer["due_in_ms"]) > 5_000


def test_drain_raises_instead_of_pretending_an_early_return_settled(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dangerous half of the pass-bounded drain: on an uncompressed
    schedule the unit's drain returns EARLY with retries still scheduled.
    Reaching that for real costs ~125s of wall clock (500 passes x 250ms),
    so the drain POST alone is stubbed to return at once; the state it
    returns into -- a genuinely pending retry timer on the unit's real
    clock -- is not stubbed, and the check that raises reads it over the
    same client every other call uses."""
    with unit("square", _slow_retry_square_profile(tmp_path)) as square, webhook_receiver() as receiver:
        receiver.respond_with = lambda index: 500 if index == 0 else 200
        square.subscribe(receiver.url, ["order.created"], "k")
        _create_square_order(square)
        _await_pending_timers(square)

        real_post = square.client.post

        def post_without_the_wait(url: str, **kwargs: Any) -> httpx.Response:
            if str(url).endswith("/__unit/webhooks/drain"):
                return httpx.Response(200, json={"deliveries": 1}, request=httpx.Request("POST", url))
            return real_post(url, **kwargs)

        monkeypatch.setattr(square.client, "post", post_without_the_wait)
        with pytest.raises(RuntimeError, match=r"pass-bounded .* run the unit on a virtual clock"):
            square.drain()


def test_drain_over_a_thread_server_settles_a_real_retry_cascade() -> None:
    """The regression the #29 gate caught: serve_in_thread's client carried
    httpx's 5s default while served()'s said 30s, and a real-clock drain of
    an exhausting cascade sleeps the scaled retry timers -- about fifteen
    seconds on the shipped Square profiles. The README's own subscribe ->
    trigger -> drain pattern then raised ReadTimeout from the fixture. Here
    the cascade really runs to exhaustion over the thread server: drain()
    settles it, and the elapsed time is asserted past the old 5s default so
    a client timeout shorter than the cascade fails this test again."""
    with unit("square") as square, serve_in_thread(square) as over_http:
        # Nothing listens on port 1, so every attempt fails at the transport
        # and the dispatcher walks the whole retry schedule.
        over_http.subscribe("http://127.0.0.1:1/never", ["order.created"], "k")
        _create_square_order(square)
        began = time.monotonic()
        over_http.drain()
        elapsed = time.monotonic() - began
        rows = over_http.deliveries()
        assert [row["attempt"] for row in rows] == list(range(1, 13))
        assert rows[-1]["status"] == "exhausted"
        assert elapsed > 5.0, f"the cascade settled in {elapsed:.1f}s -- it no longer proves the old default fails"


def test_the_tripwire_is_wired_so_framework_answered_is_a_measurement() -> None:
    """The regression tests/conformance/harness.py records: a counter wired
    at neither end reports a literal 0 forever, and both contracts on it
    become vacuous. Here the positive half (requests the unit answers leave
    it at 0) and the negative half (the one request a framework can still
    answer first -- an exotic verb outside HTTP_METHODS -- moves the number
    on the wire) prove unit() and serve_in_thread() share a live tripwire."""
    with unit("square") as square, serve_in_thread(square) as over_http:
        assert over_http.client.get("/v2/locations", headers=square.seed.auth).status_code == 200
        assert over_http.health()["framework_answered"] == 0
        assert square.tripwire.count == 0

        answered = over_http.client.request("PROPFIND", "/v2/locations", headers=square.seed.auth)
        # The consumer still gets a vendor-shaped answer (the handler
        # dispatches to the unit after recording), and the counter moved.
        assert answered.status_code != 500
        assert over_http.health()["framework_answered"] == 1
        assert square.health()["framework_answered"] == 1  # same tripwire, read in process
        assert square.tripwire.count == 1


def test_repeated_headers_reach_the_unit_identically_through_the_transport_and_the_server() -> None:
    """UnitTransport is a third binding, so its parity with HTTP is pinned
    here: the same request with two ``accept`` and two ``x-forwarded-for``
    headers, once through the transport and once through a real server, must
    be echoed byte for byte. A last-wins dict on the transport side would
    drop one value in process and keep both over the socket."""
    from tests.fakes import make_unit, route
    from vendorfake.asgi import create_app
    from vendorfake.asgi import serve_in_thread as serve_app
    from vendorfake.core.kernel.reply import json_
    from vendorfake.testing.transport import UnitTransport

    def reflect(args: Any) -> Any:
        return json_({name: args.req.headers[name] for name in ("accept", "x-forwarded-for", "x-one")})

    fake = make_unit([route("GET", "/v2/headers", reflect)])
    try:
        headers = [
            ("Accept", "text/plain"),
            ("accept", "application/json"),
            ("X-Forwarded-For", "10.0.0.1"),
            ("X-Forwarded-For", "10.0.0.2"),
            ("X-One", "only"),
        ]
        with httpx.Client(transport=UnitTransport(fake), base_url="http://unit.local") as direct:
            in_process = direct.get("/v2/headers", headers=headers)
        with serve_app(create_app(fake)) as base_url, httpx.Client(base_url=base_url) as over_http:
            served_reply = over_http.get("/v2/headers", headers=headers)
        assert in_process.status_code == served_reply.status_code == 200
        assert in_process.content == served_reply.content
        assert in_process.json() == {
            "accept": "text/plain, application/json",
            "x-forwarded-for": "10.0.0.1, 10.0.0.2",
            "x-one": "only",
        }
    finally:
        fake.stop()


def test_subscribe_refuses_an_event_type_the_vendor_never_sends() -> None:
    with unit("clover") as clover:
        with pytest.raises(
            ValueError, match=r"'clover' sends none of \['order.created'\]; its event types are \['O:CREATE'"
        ):
            clover.subscribe("http://127.0.0.1:1/x", ["order.created"], "code")
        # Globs the dispatcher honours pass, and so does the exact vocabulary.
        clover.subscribe("http://127.0.0.1:1/x", ["O:*"], "code")
        clover.subscribe("http://127.0.0.1:1/y", ["*"], "code")
        clover.subscribe("http://127.0.0.1:1/z", ["P:CREATE", "O:UPDATE"], "code")
    with unit("square") as square:
        with pytest.raises(ValueError, match="'square' sends none of"):
            square.subscribe("http://127.0.0.1:1/x", ["O:CREATE"], "k")
        square.subscribe("http://127.0.0.1:1/x", ["order.*", "payment.created"], "k")


def test_served_enforces_its_startup_deadline_on_a_child_that_never_announces(monkeypatch: pytest.MonkeyPatch) -> None:
    import vendorfake.testing as testing

    monkeypatch.setattr(testing, "SERVE_COMMAND", (sys.executable, "-c", "import time; time.sleep(60)", "--"))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"did not announce a port within 1\.0s"), served("square", timeout_s=1.0):
        pass  # pragma: no cover - never entered
    assert time.monotonic() - started < 10


@pytest.fixture
def seedless_vendor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vendor named ``acme`` that ``registry.resolve_vendor`` reports, for
    the duration of one test. No seed describes ``acme``, so any caller that
    reaches :func:`~vendorfake.testing._require_seed` for it is refused.

    ``served()`` now loads the profile before that refusal, the same way
    :func:`~vendorfake.testing.unit` always has -- resolving the vendor's real
    config is what lets a ``SeedingVendor`` hook see it (see the seed-hook
    tests below and ``tests/unit/testing/test_seed_hook.py``), and a seedless
    vendor is refused only once that load succeeds and the hook still has
    nothing to say. So, like ``tests/unit/testing/test_seed_typing.py``'s
    fixture of the same name, this one needs a real profile document on disk.

    Patches the attribute on ``vendorfake.registry``, not a name imported
    into another module: that is the substitution :func:`served` must route
    through for a test to reach it at all (see the test below).
    """
    (tmp_path / "seedless.json").write_text(
        json.dumps({"name": "seedless", "capabilities": ["orders", "chaos"]}), encoding="utf-8"
    )
    definition = FakeVendor(name="acme", profile_dir=tmp_path, base_dir=tmp_path)
    monkeypatch.setattr("vendorfake.registry.resolve_vendor", lambda name: definition)


@pytest.mark.usefixtures("seedless_vendor")
def test_served_refuses_a_seedless_vendor_before_spawning_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """``served()`` must resolve the vendor and refuse one with no seed
    *before* ``subprocess.Popen`` runs -- paying for a child that boots,
    announces its port and answers a health check only to be told the vendor
    has no seed wastes the startup on every call in a suite that does this
    per test, and points the traceback at a line inside a connected client
    rather than at the vendor argument that is actually wrong.

    This is also the regression test for *how* ``served()`` resolves the
    vendor. It used to reach ``resolve_vendor`` through a name imported
    straight into ``vendorfake.testing``'s namespace at import time --
    ``from vendorfake.registry import resolve_vendor`` -- which is a second,
    separate binding that ``seedless_vendor``'s substitution of
    ``vendorfake.registry.resolve_vendor`` does not touch. With that bug,
    this test's ``pytest.raises(LookupError)`` would not even match: the
    unpatched lookup fails ``acme`` for a different reason (``ValueError``,
    no such vendor) before ``served()`` ever gets far enough to load a
    profile or consult a seed at all. The ``subprocess.Popen`` sentinel below
    is the direct check that no child is ever spawned for a vendor that was
    going to be refused.
    """

    def refuse_to_spawn(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        pytest.fail(f"subprocess.Popen called with args={args!r} kwargs={kwargs!r}")

    # `vendorfake.testing` imports the same `subprocess` module object this
    # file does -- module lookups are singletons through `sys.modules` -- so
    # patching the attribute here reaches the `subprocess.Popen(...)` call
    # inside `served()` without naming `vendorfake.testing` as an attribute
    # path (it does not re-export `subprocess`, and mypy's
    # `no_implicit_reexport` refuses an attribute access that assumes it does).
    monkeypatch.setattr(subprocess, "Popen", refuse_to_spawn)

    with pytest.raises(LookupError) as refused:  # noqa: SIM117 - the `with served(...)` is the subject
        with served("acme", "seedless") as driver:
            pytest.fail(f"served() yielded {driver!r} for a vendor with no seed")

    message = str(refused.value)
    assert "'acme'" in message
    assert "'seedless'" in message
    assert NO_SEED_HINT in message


def test_served_refuses_a_nonexistent_profile_before_spawning_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the profile in the parent (round 1's fix for the vendor-config
    finding above) changed ``served()``'s failure mode for a bad profile name
    too, and not just for a seedless vendor: it used to spawn a child that
    then failed through its own startup/health-check path, and now raises
    ``UnitError`` -- the same exception :func:`unit` raises for the identical
    mistake -- from ``load_profile`` in the parent, before ``subprocess.Popen``
    ever runs. Pinned here, with a real shipped vendor and no fixture needed,
    so a refactor cannot silently reintroduce the slow-fail path or change the
    exception type with nothing red.
    """

    def refuse_to_spawn(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        pytest.fail(f"subprocess.Popen called with args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse_to_spawn)

    with pytest.raises(UnitError) as refused:  # noqa: SIM117 - the `with served(...)` is the subject
        with served("square", "nosuchprofile") as driver:
            pytest.fail(f"served() yielded {driver!r} for a nonexistent profile")

    assert "nosuchprofile" in str(refused.value)


def test_served_keeps_the_child_output_readable_and_never_blocks_on_it() -> None:
    with served("square", "no-faults", log_level="debug") as child:
        # Debug logging for the life of the child. Whatever it writes, the
        # pipe is drained on a thread, so the child keeps answering, and the
        # tail stays bounded and keeps the startup line.
        for _ in range(400):
            assert child.client.get("/__unit/health").status_code == 200
        lines = child.logs()
    assert any('"msg":"unit started"' in line for line in lines)
    assert len(lines) <= LOG_LINES


@pytest.mark.integration
@pytest.mark.parametrize("vendor", ["square", "clover"])
def test_served_runs_the_shipped_command_in_a_child(vendor: str) -> None:
    with served(vendor, "no-faults") as child:
        assert isinstance(child, ServedUnit)
        assert child.pid != os.getpid()
        assert child.vendor == vendor
        assert child.profile == "no-faults"
        assert child.seed is not None
        assert child.health()["vendor"] == vendor
        chaos = next(row for row in child.info()["capabilities"] if row["name"] == "chaos")
        assert chaos["enabled"] is False
    assert child.process.poll() is not None


# ---------------------------------------------------------------------------
# The shipped conformance targets.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec", ["vendorfake.testing.conformance:square_target", "vendorfake.testing.conformance:clover_target"]
)
def test_the_shipped_targets_resolve_and_pass_a_contract_on_both_bindings(spec: str) -> None:
    target = resolve_target(spec)
    first = select_checks(["C01"])[0]
    for transport in ("inprocess", "http"):
        result = run_check(first, target, "full", transport)
        assert result.outcome is Outcome.PASS, result.detail


# ---------------------------------------------------------------------------


def _create_square_order(square: Driver) -> dict[str, object]:
    seed = square.seed
    assert isinstance(seed, SquareSeed)
    created = square.client.post(
        "/v2/orders",
        headers=seed.auth,
        json={
            "idempotency_key": f"t-{id(square)}-{len(square.deliveries())}",
            "order": {
                "location_id": seed.location_id,
                "line_items": [{"catalog_object_id": seed.tea_mug_variation_id, "quantity": "1"}],
            },
        },
    )
    assert created.status_code == 200, created.text
    order: dict[str, object] = created.json()["order"]
    return order
