"""The shipped profiles: every one loads, and every one means what it says.

A profile is package data a consumer selects by name, so a profile that does
not load is a broken product rather than a broken test. This file boots each
one and asserts the surface it serves.
"""

from __future__ import annotations

import importlib.resources
import json
import re

import pytest

from tests.unit.square.harness import APPLICATION_ID, APPLICATION_SECRET, CONFIGURED_REDIRECT_URI
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.capabilities import SQUARE_CAPABILITIES
from vendorfake.square.vendor import create_square_vendor

PROFILE_DIR = create_square_vendor().profile_dir
SHIPPED = sorted(path.stem for path in PROFILE_DIR.glob("*.json"))


def test_the_expected_profiles_are_shipped() -> None:
    """Six, named as a literal.

    A consumer selects a profile by name and a rename is a breaking change, so
    the set is asserted rather than derived. `chaos-demo` could only land once
    the Orders surface existed: its rules name `POST /v2/orders` and
    `GET /v2/orders/{order_id}`, every shipped profile sets `strict_rules`, and
    a rule matching no registered route is a startup failure here rather than a
    rule that quietly never fires.
    """
    assert SHIPPED == ["chaos-demo", "full", "no-chaos", "no-faults", "oauth-only", "orders-only"]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_starts(name: str) -> None:
    for h in build_harness(name):
        health = h.api.get("/__unit/health").json()
        assert health["status"] == "ok"
        assert health["profile"] == name


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_names_only_declared_capabilities(name: str) -> None:
    declared = {decl.name for decl in SQUARE_CAPABILITIES}
    document = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert set(document["capabilities"]) <= declared


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_seeds_the_shipped_scenario(name: str) -> None:
    document = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert document["seed"] == "seed/default.seed.json"


@pytest.mark.parametrize("name", SHIPPED)
def test_every_profile_refuses_a_chaos_rule_that_can_never_fire(name: str) -> None:
    """The reference validated a rule's id, fault and scope and never its
    route, so a typo was a rule that matched nothing, forever, silently."""
    document = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert document["chaos"]["strict_rules"] is True


def test_the_vendor_block_is_snake_case_and_typed() -> None:
    """The reference's profiles carry camelCase keys, because its whole entity
    model is camelCase. `SquareConfig` is a Pydantic model with snake_case
    fields and `extra="forbid"`, so a camelCase key here would be a startup
    failure naming it -- which is the check, not the inconvenience."""
    document = json.loads((PROFILE_DIR / "full.json").read_text(encoding="utf-8"))
    assert document["vendor"] == {
        "application_id": APPLICATION_ID,
        "application_secret": APPLICATION_SECRET,
        "redirect_uri": CONFIGURED_REDIRECT_URI,
        "environment": "Sandbox",
        "api_version": "2026-08-19",
    }


def test_oauth_only_registers_the_whole_surface_and_enables_only_its_own() -> None:
    """The surface belongs to the vendor and the profile only gates it.

    `oauth-only` still *registers* the Orders routes -- the router is the
    vendor's -- and the capability gate is what answers 501. Asserting the
    route list rather than the capability list would make this test a mirror
    of whichever surfaces happen to have landed; asserting both is what pins
    the distinction the gate exists to make.
    """
    for h in build_harness("oauth-only"):
        routes = h.api.get("/__unit/routes").json()["routes"]
        vendor_paths = {route["path"] for route in routes if not route["internal"]}
        assert {
            "/oauth2/authorize",
            "/oauth2/token",
            "/oauth2/revoke",
            "/oauth2/token/status",
        } <= vendor_paths
        enabled = {row["name"] for row in h.api.get("/__unit/capabilities").json()["capabilities"] if row["enabled"]}
        assert enabled == {"oauth", "chaos"}
        assert h.api.get("/v2/orders/CAISENgvlJ6jLWAzERDzjyHVybY").status == 501


def test_orders_only_serves_no_oauth_surface() -> None:
    """The OAuth routes are still *registered* -- the surface is the vendor's,
    not the profile's -- and the capability gate is what answers 501. That is
    the distinction `GET /__unit/capabilities` exists to make visible."""
    for h in build_harness("orders-only"):
        capabilities = {row["name"]: row for row in h.api.get("/__unit/capabilities").json()["capabilities"]}
        assert capabilities["oauth"]["enabled"] is False
        assert h.api.post("/oauth2/token", {"client_id": APPLICATION_ID}).status == 501


def test_the_default_profile_is_full() -> None:
    """`create_unit` with no profile resolves `full`, which is what a consumer
    running the container with no configuration gets."""
    for h in build_harness(profile=None):
        assert h.api.get("/__unit/info").json()["profile"] == "full"


# ---------------------------------------------------------------------------
# The sixth capability, and the two profiles it made necessary.
# ---------------------------------------------------------------------------


def test_chaos_is_on_everywhere_except_the_profile_named_for_having_it_off() -> None:
    """The mapping decision, written down as an assertion.

    `chaos` gates request-scope fault injection from every source. The
    reference had no such capability, so a request-scope rule fired on all five
    of its profiles including `no-chaos` -- whose capability list drops only
    `webhooks.chaos`. Preserving that behaviour means `chaos` is on in
    `no-chaos`, which makes the name narrower than it reads: what `no-chaos`
    switches off is *delivery* faults. `no-faults` is the profile that means
    what `no-chaos` sounds like, and it exists precisely because the two are
    different configurations a consumer might genuinely want.
    """
    without_chaos = set()
    for name in SHIPPED:
        document = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
        if "chaos" not in document["capabilities"]:
            without_chaos.add(name)
    assert without_chaos == {"no-faults"}


def test_no_faults_switches_off_both_fault_gates() -> None:
    for h in build_harness("no-faults"):
        enabled = {row["name"] for row in h.api.get("/__unit/capabilities").json()["capabilities"] if row["enabled"]}
        assert "chaos" not in enabled
        assert "webhooks.chaos" not in enabled
        assert {"oauth", "order-lifecycle", "merchant-directory", "webhooks"} <= enabled


def test_a_profile_declaring_a_behavior_capability_declares_its_prerequisites() -> None:
    """`webhooks.chaos` requires `webhooks` and `chaos`; a profile that named
    the child without its parents would start with the child silently blocked,
    which reads in `/__unit/capabilities` exactly like a profile that never
    asked for it."""
    declared = {decl.name: decl for decl in SQUARE_CAPABILITIES}
    for name in SHIPPED:
        listed = set(json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))["capabilities"])
        for capability in listed:
            assert set(declared[capability].requires) <= listed, f"{name}: {capability}"


# ---------------------------------------------------------------------------
# chaos-demo: the profile whose whole content is rules.
# ---------------------------------------------------------------------------


def test_chaos_demo_ships_four_rules_and_every_one_of_them_can_fire() -> None:
    """`strict_rules` makes a dead rule a startup failure, so this test would
    not even reach its assertions if a template had drifted -- but the
    `matched_routes` count is asserted anyway, because "it started" and "the
    rule selects a route" are different claims and only the second is the one
    a demo transcript depends on."""
    for h in build_harness("chaos-demo"):
        status = h.api.get("/__unit/chaos").json()
        assert [rule["id"] for rule in status["rules"]] == [
            "rate-limit-every-third-create",
            "token-expires-on-fourth-read",
            "duplicate-order-created",
            "reorder-order-updated",
        ]
        for rule in status["rules"]:
            assert rule["matched_routes"], rule["id"]
        assert status["enabled"] is True


def test_chaos_demo_runs_on_a_virtual_clock() -> None:
    """The one profile with a preloaded `timeout`-capable rule set, and the one
    the deadlock reversal in `chaos/faults.py` was written for: a request-scope
    stall must never park on a timer only another request could fire."""
    document = json.loads((PROFILE_DIR / "chaos-demo.json").read_text(encoding="utf-8"))
    assert document["clock"] == {"mode": "virtual", "start": "2026-08-24T12:00:00.000Z"}


def test_no_profile_writes_a_colon_path_template() -> None:
    """The DoD grep, as a test, because a colon template matches nothing
    forever and the reference shipped one."""
    for name in SHIPPED:
        text = (PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8")
        assert not re.search(r'"route": "[A-Z]+ [^"]*:', text), name


SEED_DIGEST = "60ff7744bce9cbb20ce8a63dcd1d366a819bebaa5b327e7e2131ecef4f15dfa3"
"""The entity digest of the shipped scenario, pinned as a literal.

Identical on every profile because all six share ``seed/default.seed.json``,
seeded ids come from the document rather than the id stream, and every
hydrate-time instant is a volatile field whose value the digest ignores. A
change to the scenario changes this line on purpose; a change to anything
else that moves it is the regression this test exists to catch (the same
claim the conformance C06/C22 contracts make across units and across
processes). Re-pinned for konyklabs/roadmap#55, when the seeded reward tier
gained the (empty) ``pricing_rule_reference`` the published schema requires. First
pinned for konyklabs/roadmap#35, when the digest began
hashing a volatile field's presence rather than dropping it, so the Square
side has the same tripwire the Clover side had."""


@pytest.mark.parametrize("name", SHIPPED)
def test_the_seeded_digest_is_pinned_and_identical_on_every_profile(name: str) -> None:
    for h in build_harness(name):
        assert h.unit.context.store.entity_digest() == SEED_DIGEST


# ---------------------------------------------------------------------------
# Package data: a profile a consumer cannot read is not shipped.
# ---------------------------------------------------------------------------


def test_every_profile_and_the_seed_resolve_as_package_data() -> None:
    """`importlib.resources` is how an installed consumer reaches these files,
    and it is not the same path as `Path(__file__).parent` -- a package data
    glob that stopped matching would leave the second working from a source
    tree and the first failing in the wheel."""
    package = importlib.resources.files("vendorfake.square")
    for name in SHIPPED:
        assert package.joinpath(f"profiles/{name}.json").is_file(), name
    assert package.joinpath("seed/default.seed.json").is_file()


def test_the_distribution_declares_itself_typed() -> None:
    """`py.typed` is what makes the `Typing :: Typed` classifier true for a
    consumer: without the marker file, mypy ignores every annotation in this
    package once it is installed."""
    assert importlib.resources.files("vendorfake").joinpath("py.typed").is_file()
