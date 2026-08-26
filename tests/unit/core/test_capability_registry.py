"""Semantics of the capability registry.

Weighted at the three things a reviewer could disagree about: the exact
three-way answer ``blocked_by`` gives (it drives the error wording), the exact
shape of the ``capability_disabled`` error (a consumer reads it, and hiding the
route instead would be the defect this whole module exists to prevent), and the
delta grammar's replace-versus-subtract rule.
"""

from __future__ import annotations

import pytest

from vendorfake.core.capability.registry import (
    CONTROL_CAPABILITY,
    CapabilityRegistry,
    apply_capability_delta,
)
from vendorfake.core.kernel.types import (
    CapabilityDecl,
    HandlerArgs,
    ReplyInit,
    Route,
    UnitError,
    UnitErrorKind,
)

DECLS = (
    CapabilityDecl(name="oauth", summary="The token dance."),
    CapabilityDecl(name="order-lifecycle", summary="Orders that persist."),
    CapabilityDecl(name="webhooks", summary="Signed event delivery."),
    CapabilityDecl(name="chaos", summary="Request-scope fault injection.", kind="behavior"),
    CapabilityDecl(
        name="webhooks.chaos",
        summary="Delivery faults.",
        kind="behavior",
        requires=("webhooks", "chaos"),
    ),
)


def _handler(args: HandlerArgs) -> ReplyInit:
    return ReplyInit(json={})


ROUTES = (
    Route(method="POST", path="/oauth2/token", capability="oauth", handler=_handler),
    Route(method="POST", path="/v2/orders", capability="order-lifecycle", handler=_handler),
    Route(method="GET", path="/v2/orders/{order_id}", capability="order-lifecycle", handler=_handler),
    Route(method="GET", path="/__unit/health", capability=CONTROL_CAPABILITY, handler=_handler, internal=True),
)


def registry(*enabled: str, profile: str = "full") -> CapabilityRegistry:
    return CapabilityRegistry(DECLS, ROUTES, enabled, profile)


# ---------------------------------------------------------------------------
# The whole point: a disabled capability is answered, not hidden.
# ---------------------------------------------------------------------------


def test_a_disabled_capability_raises_and_names_itself_and_the_profile() -> None:
    """Hiding the route would make 'disabled here' indistinguishable from
    'this vendor has no such endpoint'. The answer must carry both names."""
    reg = registry("oauth", profile="oauth-only")

    with pytest.raises(UnitError) as caught:
        reg.assert_enabled("order-lifecycle", "POST /v2/orders")

    err = caught.value
    assert err.kind is UnitErrorKind.CAPABILITY_DISABLED
    assert err.detail is not None
    assert "'order-lifecycle'" in err.detail
    assert "'oauth-only'" in err.detail
    assert "POST /__unit/capabilities" in err.detail
    assert err.info == {
        "kind": "capability_disabled",
        "capability": "order-lifecycle",
        "blocked_by": "order-lifecycle",
        "profile": "oauth-only",
        "route": "POST /v2/orders",
        "enabled": ["oauth"],
    }


def test_the_route_key_is_absent_rather_than_null_when_no_route_is_named() -> None:
    """absent-means-absent, the same rule the entity store follows."""
    reg = registry("oauth")
    with pytest.raises(UnitError) as caught:
        reg.assert_enabled("webhooks")
    assert caught.value.info is not None
    assert "route" not in caught.value.info


def test_a_prerequisite_blocker_is_worded_differently_from_the_capability_itself() -> None:
    reg = registry("chaos", "webhooks.chaos", profile="custom")
    with pytest.raises(UnitError) as caught:
        reg.assert_enabled("webhooks.chaos")
    detail = caught.value.detail
    assert detail is not None
    assert "unavailable because its prerequisite 'webhooks'" in detail
    assert caught.value.info is not None
    assert caught.value.info["blocked_by"] == "webhooks"


def test_assert_enabled_is_silent_when_usable() -> None:
    registry("webhooks", "chaos", "webhooks.chaos").assert_enabled("webhooks.chaos")


# ---------------------------------------------------------------------------
# blocked_by: a three-way answer, resolved in a fixed order.
# ---------------------------------------------------------------------------


def test_blocked_by_returns_the_name_itself_when_it_is_simply_off() -> None:
    assert registry("oauth").blocked_by("webhooks") == "webhooks"


def test_blocked_by_prefers_the_immediate_parent_over_a_requires_entry() -> None:
    """Both 'webhooks' (parent) and 'chaos' (requires) are off; the parent wins,
    because the parent check runs first."""
    reg = registry("webhooks.chaos")
    assert reg.blocked_by("webhooks.chaos") == "webhooks"


def test_blocked_by_falls_through_to_the_first_unmet_requires_entry() -> None:
    reg = registry("webhooks", "webhooks.chaos")
    assert reg.blocked_by("webhooks.chaos") == "chaos"


def test_blocked_by_is_none_when_every_prerequisite_holds() -> None:
    reg = registry("webhooks", "chaos", "webhooks.chaos")
    assert reg.blocked_by("webhooks.chaos") is None
    assert reg.is_enabled("webhooks.chaos") is True


def test_the_parent_check_is_one_level_and_not_every_ancestor() -> None:
    """Ported exactly, and surprising enough to pin: with a.b enabled and a off,
    a.b.c reads as usable. Unreachable through disable(), which removes every
    dotted descendant -- see the next test -- but true of the resolver."""
    decls = (
        CapabilityDecl(name="a", summary="grandparent"),
        CapabilityDecl(name="a.b", summary="parent"),
        CapabilityDecl(name="a.b.c", summary="child"),
    )
    reg = CapabilityRegistry(decls, (), ("a.b", "a.b.c"), "custom")
    assert reg.blocked_by("a.b.c") is None
    assert reg.blocked_by("a.b") == "a"


# ---------------------------------------------------------------------------
# The enabled set.
# ---------------------------------------------------------------------------


def test_an_unknown_name_is_rejected_loudly_and_lists_what_was_declared() -> None:
    """A typo in a profile is a startup failure, not a profile with one fewer
    capability and a mystery 501 later."""
    with pytest.raises(UnitError) as caught:
        registry("oauth", "webhoooks")
    err = caught.value
    assert err.kind is UnitErrorKind.INVALID_VALUE
    assert err.field == "capabilities"
    assert err.detail is not None
    assert "'webhoooks'" in err.detail
    assert err.info == {"declared": ["oauth", "order-lifecycle", "webhooks", "chaos", "webhooks.chaos"]}


def test_the_control_capability_is_always_on_and_never_listed() -> None:
    reg = registry()
    assert reg.is_enabled(CONTROL_CAPABILITY) is True
    assert CONTROL_CAPABILITY not in reg.names()
    assert CONTROL_CAPABILITY not in reg.enabled_names()
    assert CONTROL_CAPABILITY not in [row.name for row in reg.view()]
    assert reg.is_declared(CONTROL_CAPABILITY) is True


def test_disabling_a_parent_takes_its_dotted_children_and_its_direct_dependents() -> None:
    reg = registry("oauth", "webhooks", "chaos", "webhooks.chaos")
    reg.disable("webhooks")
    assert reg.enabled_names() == ("chaos", "oauth")


def test_disable_follows_requires_one_level_only() -> None:
    """'webhooks.chaos' requires 'chaos' directly and goes with it; nothing
    requires 'webhooks.chaos', so nothing else moves."""
    reg = registry("oauth", "webhooks", "chaos", "webhooks.chaos")
    reg.disable("chaos")
    assert reg.enabled_names() == ("oauth", "webhooks")


def test_enable_adds_without_disturbing_the_control_capability() -> None:
    reg = registry("oauth")
    reg.enable("webhooks")
    assert reg.enabled_names() == ("oauth", "webhooks")
    assert reg.is_enabled(CONTROL_CAPABILITY) is True


def test_enabled_names_is_sorted_by_code_point_so_two_runs_agree() -> None:
    reg = registry("webhooks", "oauth", "chaos")
    assert reg.enabled_names() == ("chaos", "oauth", "webhooks")


# ---------------------------------------------------------------------------
# The view, and the route index.
# ---------------------------------------------------------------------------


def test_view_preserves_declaration_order_and_carries_the_route_keys() -> None:
    reg = registry("oauth", "order-lifecycle")
    rows = reg.view()
    assert [row.name for row in rows] == [
        "oauth",
        "order-lifecycle",
        "webhooks",
        "chaos",
        "webhooks.chaos",
    ]
    assert rows[1].routes == ("POST /v2/orders", "GET /v2/orders/{order_id}")
    assert rows[0].enabled is True
    assert rows[2].enabled is False


def test_a_behavior_capability_owns_no_routes() -> None:
    """The 'chaos' capability gates conduct with no surface of its own. If it
    owned a route, disabling it would hide an endpoint instead of changing
    behaviour, and conformance checks the two kinds differently."""
    reg = registry("chaos")
    assert reg.routes_for("chaos") == ()
    assert reg.declaration("chaos") is not None
    assert reg.declaration("chaos").kind == "behavior"  # type: ignore[union-attr]


def test_view_sets_blocked_by_only_when_the_blocker_is_someone_else() -> None:
    reg = registry("chaos", "webhooks.chaos")
    rows = {row.name: row for row in reg.view()}
    assert rows["webhooks"].enabled is False
    assert rows["webhooks"].blocked_by is None  # off in its own right
    assert rows["webhooks.chaos"].enabled is False
    assert rows["webhooks.chaos"].blocked_by == "webhooks"


def test_the_view_row_drops_blocked_by_from_json_rather_than_emitting_null() -> None:
    reg = registry("oauth")
    row = next(r for r in reg.view() if r.name == "oauth")
    assert row.as_json() == {
        "name": "oauth",
        "summary": "The token dance.",
        "enabled": True,
        "kind": "surface",
        "requires": [],
        "routes": ["POST /oauth2/token"],
    }


# ---------------------------------------------------------------------------
# The delta grammar.
# ---------------------------------------------------------------------------


class TestApplyCapabilityDelta:
    def test_a_list_with_no_sign_replaces_the_base_entirely(self) -> None:
        assert apply_capability_delta(["oauth", "webhooks"], "orders,catalog") == ["orders", "catalog"]

    def test_one_sign_anywhere_makes_the_whole_expression_a_delta(self) -> None:
        assert apply_capability_delta(["oauth", "webhooks"], "orders,-webhooks") == [
            "oauth",
            "orders",
        ]

    def test_additions_append_and_do_not_reorder_an_existing_name(self) -> None:
        assert apply_capability_delta(["a", "b"], "+b,+c") == ["a", "b", "c"]

    def test_removing_a_name_that_is_not_there_is_a_no_op(self) -> None:
        assert apply_capability_delta(["a"], "-z") == ["a"]

    def test_whitespace_and_empty_parts_are_dropped(self) -> None:
        assert apply_capability_delta([], " oauth , , webhooks ") == ["oauth", "webhooks"]

    def test_an_empty_expression_replaces_with_nothing(self) -> None:
        assert apply_capability_delta(["a", "b"], "") == []

    def test_apply_delta_on_the_registry_revalidates_the_result(self) -> None:
        reg = registry("oauth", "webhooks")
        reg.apply_delta("-webhooks")
        assert reg.enabled_names() == ("oauth",)
        with pytest.raises(UnitError):
            reg.apply_delta("+nonsense")
