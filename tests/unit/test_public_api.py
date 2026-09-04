"""The public surface, pinned.

FOR: making a change to what vendorfake promises into an edit a reviewer sees.
``docs/api-contract.md`` says in prose which modules are public and what the
deprecation policy covers; this module is the same statement in a form that
fails a build. Adding a name to a public ``__all__`` -- or removing one, which
is the case that matters -- breaks :func:`test_the_public_surface_is_what_the_contract_says`
until the list below is edited in the same commit, and that edit is the review
trigger. A symbol that reaches a consumer because somebody added an import is
exactly the surface nobody can ever change.

The assertions here are deliberately about *shape*, not behaviour. What each
public function does is asserted everywhere else in this suite; what is
asserted here is that it is still called what it was called and that it did
not drag an internal module out with it.

WHY ``__all__`` AND NOT ``dir()``. ``dir()`` reports imports, re-exports and
every name a module happened to bind, which would make this test fail on a
refactor that changed nothing a consumer can see -- noise, and noise gets
silenced. ``__all__`` is a deliberate statement, so pinning it pins the
deliberate part.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

INTERNAL_PACKAGES = ("vendorfake.asgi", "vendorfake.core", "vendorfake.conformance")
"""Packages ``docs/api-contract.md`` names as internal.

``vendorfake.asgi`` is the one the third assertion below polices, because it
is the one an accident would reach for: it is where a real ASGI application
comes from, and a public module re-exporting anything from it would make
FastAPI part of what a consumer imports.
"""

PUBLIC_API: Mapping[str, tuple[str, ...]] = {
    "vendorfake": (
        "__version__",
        "available_profiles",
        "available_vendors",
        "create_unit",
        "resolve_vendor",
        "routes",
    ),
    "vendorfake.registry": (
        "ENTRY_POINT_GROUP",
        "ROLE_NAMES",
        "VENDOR_ENV_VAR",
        "ProfileInfo",
        "RouteInfo",
        "SeedingVendor",
        "VendorDefinition",
        "available_profiles",
        "available_vendors",
        "create_unit",
        "resolve_vendor",
        "routes",
    ),
    "vendorfake.testing": (
        "CLIENT_TIMEOUT_S",
        "DEFAULT_REQUEST_LIMIT",
        "DRAIN_TIMEOUT_S",
        "LOG_LINES",
        "NO_SEED_HINT",
        "SERVE_COMMAND",
        "ClockInfo",
        "CloverSeed",
        "CloverSeedOverlay",
        "Credentials",
        "Delivery",
        "Driver",
        "LightspeedSeed",
        "LightspeedSeedOverlay",
        "RouteInfo",
        "Seed",
        "SeedOverlay",
        "SeedT",
        "ServedUnit",
        "SquareSeed",
        "SquareSeedOverlay",
        "StartedUnit",
        "ToastSeed",
        "ToastSeedOverlay",
        "Token",
        "UnitTransport",
        "UnmatchedRequest",
        "WebhookReceiver",
        "async_unit",
        "checked_unmatched",
        "serve_in_thread",
        "served",
        "unit",
        "webhook_receiver",
    ),
    "vendorfake.testing.seeds": (
        "SEED_COLLECTIONS_ATTR",
        "CloverSeed",
        "CloverSeedOverlay",
        "Credentials",
        "LightspeedSeed",
        "LightspeedSeedOverlay",
        "Seed",
        "SeedOverlay",
        "SquareSeed",
        "SquareSeedOverlay",
        "ToastSeed",
        "ToastSeedOverlay",
        "Token",
        "seed_collections_for",
        "seed_for",
    ),
    "vendorfake.pytest": (
        "MARKER",
        "vendorfake_async_unit",
        "vendorfake_unit",
        "vendorfake_webhook_receiver",
    ),
    "vendorfake.square.paths": (
        "ACCUMULATE_LOYALTY_POINTS",
        "AUTHORIZE",
        "BATCH_CHANGE_INVENTORY",
        "BATCH_RETRIEVE_INVENTORY_COUNTS",
        "BATCH_RETRIEVE_ORDERS",
        "CANCEL_PAYMENT",
        "COMPLETE_PAYMENT",
        "CREATE_LOYALTY_ACCOUNT",
        "CREATE_ORDER",
        "CREATE_ORDER_AT_LOCATION",
        "CREATE_PAYMENT",
        "CREATE_WEBHOOK_SUBSCRIPTION",
        "DELETE_WEBHOOK_SUBSCRIPTION",
        "GET_PAYMENT",
        "LIST_CATALOG",
        "LIST_LOCATIONS",
        "LIST_MERCHANTS",
        "LIST_WEBHOOK_EVENT_TYPES",
        "LIST_WEBHOOK_SUBSCRIPTIONS",
        "OBTAIN_TOKEN",
        "PAY_ORDER",
        "RETRIEVE_CATALOG_OBJECT",
        "RETRIEVE_INVENTORY_COUNT",
        "RETRIEVE_LOYALTY_PROGRAM",
        "RETRIEVE_MERCHANT",
        "RETRIEVE_ORDER",
        "RETRIEVE_TOKEN_STATUS",
        "RETRIEVE_WEBHOOK_SUBSCRIPTION",
        "REVOKE_TOKEN",
        "SEARCH_CATALOG_OBJECTS",
        "SEARCH_LOYALTY_ACCOUNTS",
        "SEARCH_ORDERS",
        "TEST_WEBHOOK_SUBSCRIPTION",
        "UPDATE_ORDER",
        "UPSERT_CATALOG_OBJECT",
    ),
    "vendorfake.clover.paths": (
        "AUTHORIZE",
        "BULK_CREATE_LINE_ITEMS",
        "CHECKOUT_ATOMIC_ORDER",
        "CREATE_ATOMIC_ORDER",
        "CREATE_CUSTOMER",
        "CREATE_ITEM",
        "CREATE_LINE_ITEM",
        "CREATE_ORDER",
        "CREATE_PAYMENT",
        "CREATE_PRINT_EVENT",
        "DELETE_ORDER",
        "EXCHANGE_TOKEN",
        "GET_CUSTOMERS",
        "GET_DEFAULT_SERVICE_CHARGE",
        "GET_EMPLOYEES",
        "GET_ITEM",
        "GET_ITEMS",
        "GET_MERCHANT",
        "GET_MODIFIER",
        "GET_MODIFIERS",
        "GET_ORDER",
        "GET_ORDERS",
        "GET_ORDER_TYPES",
        "GET_TENDERS",
        "LIST_WEBHOOK_CALLBACKS",
        "REFRESH_TOKEN",
        "REGISTER_WEBHOOK_CALLBACK",
        "UPDATE_ITEM",
        "UPDATE_ORDER",
        "VERIFY_WEBHOOK_CALLBACK",
    ),
    "vendorfake.toast.paths": (
        "APPLICABLE_DISCOUNTS",
        "CHECK_DISCOUNTS_POST",
        "CHECK_PAYMENTS_POST",
        "CHECK_SELECTIONS_POST",
        "CONFIG_ALTERNATE_PAYMENT_TYPES_GET",
        "CONFIG_ALTERNATE_PAYMENT_TYPE_GET",
        "CONFIG_DINING_OPTIONS_GET",
        "CONFIG_DINING_OPTION_GET",
        "CONFIG_DISCOUNTS_GET",
        "CONFIG_DISCOUNT_GET",
        "CONFIG_MENUS_GET",
        "CONFIG_MENU_GET",
        "CONFIG_MENU_GROUPS_GET",
        "CONFIG_MENU_GROUP_GET",
        "CONFIG_MENU_ITEMS_GET",
        "CONFIG_MENU_ITEM_GET",
        "CONFIG_RESTAURANT_SERVICES_GET",
        "CONFIG_RESTAURANT_SERVICE_GET",
        "CONFIG_REVENUE_CENTERS_GET",
        "CONFIG_REVENUE_CENTER_GET",
        "CONFIG_SERVICE_AREAS_GET",
        "CONFIG_SERVICE_AREA_GET",
        "CONFIG_SERVICE_CHARGES_GET",
        "CONFIG_SERVICE_CHARGE_GET",
        "CONFIG_TABLES_GET",
        "CONFIG_TABLE_GET",
        "CONFIG_TAX_RATES_GET",
        "CONFIG_TAX_RATE_GET",
        "CONFIG_VOID_REASONS_GET",
        "CONFIG_VOID_REASON_GET",
        "LIST_WEBHOOK_SUBSCRIPTIONS",
        "LOGIN",
        "MENUS_V3_GET",
        "MENUS_V3_METADATA_GET",
        "ORDERS_BULK_GET",
        "ORDERS_GET",
        "ORDER_CREATE",
        "ORDER_DELIVERY_INFO_PATCH",
        "ORDER_GET",
        "ORDER_PRICES",
        "ORDER_VOID",
        "PARTNERS_CONNECTED_RESTAURANTS_GET",
        "PARTNERS_RESTAURANTS_GET",
        "PAYMENTS_GET",
        "PAYMENT_GET",
        "PAYMENT_TIP_PATCH",
        "REGISTER_WEBHOOK_SUBSCRIPTION",
        "REMOVE_WEBHOOK_SUBSCRIPTION",
        "RESTAURANT_GET",
        "RESTAURANT_GROUP_RESTAURANTS_GET",
        "SELECTION_DISCOUNTS_POST",
        "STOCK_INVENTORY_GET",
        "STOCK_INVENTORY_SEARCH",
        "STOCK_INVENTORY_UPDATE",
    ),
}
"""Every public module, and every name it exports, as of this commit.

Three vendor ``paths`` modules are listed in full rather than checked
structurally. ``tests/unit/test_paths_drift.py`` already pins their *values*
against the live router, which is the stronger check for a renamed route; what
it cannot see is a constant appearing or disappearing, because it derives both
sides from the same route table. A new route is a new public name, so it
belongs in a list somebody edits.
"""


# ---------------------------------------------------------------------------
# Attribute docstrings.
#
# `SquareSeed.__doc__` is readable at run time; `paths.OBTAIN_TOKEN` is a
# `str`, and the string literal underneath it -- the PEP 258 attribute
# docstring this code base uses everywhere -- is not readable at run time at
# all. So the source is parsed. A public constant with no explanation is
# exactly as undocumented as an unexplained function, and exempting constants
# would have exempted three quarters of the surface.
# ---------------------------------------------------------------------------


def _import_statements_paid_on_import(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    """The imports a consumer pays for by importing the module at all.

    Module level only, and not under an ``if TYPE_CHECKING:`` guard. Both
    exclusions are the point rather than leniency:

    ``serve_in_thread`` imports ``create_app`` from ``vendorfake.asgi`` inside
    its own body, because starting a real server is what a caller asked it to
    do. That is the same deliberate pattern ``cli.py``'s ``serve`` subcommand
    uses and ``tools/boundary.toml`` names by hand: a function-body import is
    paid by the caller who wants the behaviour, not by everyone who imports
    the module. Failing either of those would have said "do not annotate" and
    "do not offer a served unit", neither of which is the rule.

    What is left is the rule as it should be stated: importing a public module
    must not import a web framework, and no public name may come from one.
    """
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            yield node
        elif isinstance(node, ast.If) and not _is_type_checking_guard(node.test):
            for inner in node.body + node.orelse:
                if isinstance(inner, ast.Import | ast.ImportFrom):
                    yield inner


def _is_type_checking_guard(test: ast.expr) -> bool:
    """``TYPE_CHECKING`` or ``typing.TYPE_CHECKING``, however it was spelled."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


# ---------------------------------------------------------------------------
# The assertions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", sorted(PUBLIC_API))
def test_the_public_surface_is_what_the_contract_says(module_name: str) -> None:
    """``__all__`` matches the checked-in list, exactly, in both directions.

    Failing this test is not a defect on its own -- it is the question "did
    you mean to change what vendorfake promises?" asked at the only moment
    anyone can still answer it cheaply. Update ``PUBLIC_API`` above and
    ``docs/api-contract.md`` in the same commit, or move the symbol out of
    ``__all__``.
    """
    module = importlib.import_module(module_name)
    declared = getattr(module, "__all__", None)
    assert declared is not None, f"{module_name} is public but declares no __all__"
    assert list(declared) == list(PUBLIC_API[module_name]), (
        f"{module_name}.__all__ has changed. Added: "
        f"{sorted(set(declared) - set(PUBLIC_API[module_name]))}; removed: "
        f"{sorted(set(PUBLIC_API[module_name]) - set(declared))}."
    )
    for name in declared:
        assert hasattr(module, name), f"{module_name}.__all__ names {name!r}, which the module does not define"


@pytest.mark.parametrize("module_name", sorted(PUBLIC_API))
def test_no_public_module_hands_out_an_internal_one(module_name: str) -> None:
    """Nothing public is defined in, or re-exported from, ``vendorfake.asgi``.

    The failure this prevents is quiet: a helper moved into the ASGI adapter
    and re-exported for convenience makes a web framework part of what
    ``import vendorfake.testing`` costs and part of what a consumer is
    entitled to keep. ``tools/boundary_check.py`` polices which modules may
    *import* the framework; this polices which may hand it back.
    """
    module = importlib.import_module(module_name)
    for name in module.__all__:
        home = getattr(getattr(module, name), "__module__", "")
        assert not home.startswith("vendorfake.asgi"), f"{module_name}.{name} is defined in {home}, which is internal"

    source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
    for node in _import_statements_paid_on_import(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("vendorfake.asgi"):
            pytest.fail(f"{module_name} imports from {node.module} at module scope, line {node.lineno}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("vendorfake.asgi"), (
                    f"{module_name} imports {alias.name} at module scope, line {node.lineno}"
                )


# ---------------------------------------------------------------------------
# The white-box handles: prose that names an attribute path, checked by
# actually resolving it.
# ---------------------------------------------------------------------------
