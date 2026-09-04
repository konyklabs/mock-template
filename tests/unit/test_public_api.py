"""The public surface, pinned.

FOR: making a change to what vendorfake promises into an edit a reviewer sees.
``docs/api-contract.md`` says in prose which modules are public and what the
deprecation policy covers; this module is the same statement in a form that
fails a build. Adding a name to a public ``__all__`` -- or removing one, which
is the case that matters -- breaks :func:`test_the_public_surface_is_what_the_contract_says`
until the list below is edited in the same commit, and that edit is the review
trigger. A symbol that reaches a consumer because somebody added an import is
exactly the surface nobody can ever change.

The three assertions here are deliberately about *shape*, not behaviour. What
each public function does is asserted everywhere else in this suite; what is
asserted here is that it is still called what it was called, that it explains
itself, and that it did not drag an internal module out with it.

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
from functools import cache
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


def _attribute_docstrings(tree: ast.Module) -> Iterator[str]:
    """Names assigned at module level with a string literal directly under them."""
    body = tree.body
    for index, node in enumerate(body[:-1]):
        following = body[index + 1]
        documented = (
            isinstance(following, ast.Expr)
            and isinstance(following.value, ast.Constant)
            and isinstance(following.value.value, str)
        )
        if not documented:
            continue
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id


def _import_statements_paid_on_import(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    """The imports a consumer pays for by importing the module at all.

    Module level only, and not under an ``if TYPE_CHECKING:`` guard. Both
    exclusions are the point rather than leniency:

    ``vendorfake.testing`` imports ``FrameworkTripwire`` from
    ``vendorfake.asgi`` under that guard, for an annotation. Nothing is
    imported at run time and the name never reaches ``__all__``.

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


@cache
def _documented_constants() -> frozenset[str]:
    """Every module-level name in the package carrying an attribute docstring.

    Indexed by name across the whole package rather than per module, because a
    public constant is frequently re-exported from the module that defines it
    -- ``vendorfake.testing.CLIENT_TIMEOUT_S`` and
    ``vendorfake.testing.seeds.Seed`` are both read through a module other
    than their own -- and a plain ``str`` carries no ``__module__`` to follow
    home. Two modules defining the same name and only one documenting it would
    pass falsely; that is the accepted cost, and it is small against the
    alternative of not checking constants at all.
    """
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        found.update(_attribute_docstrings(ast.parse(path.read_text(encoding="utf-8"))))
    return frozenset(found)


def _is_documented(name: str, value: object) -> bool:
    """Whether ``name`` explains itself, by whichever of the two mechanisms applies.

    ``value.__doc__`` is only its own when it is not *equal to* the docstring
    of its own type: ``paths.OBTAIN_TOKEN.__doc__`` is ``str.__doc__``,
    several paragraphs about the ``str`` constructor, and reading that as
    "documented" would exempt every constant in the package. Anything that
    fails that test falls through to the attribute-docstring index.

    A value comparison (``!=``), not identity (``is not``): a builtin
    immutable's ``__doc__`` is not a per-instance attribute at all -- it is
    read off the type via the same getset descriptor every time -- and that
    descriptor hands back a freshly built ``str`` on each access, so
    ``(1).__doc__ is int.__doc__``, ``'x'.__doc__ is str.__doc__`` and
    ``().__doc__ is tuple.__doc__`` are all ``False`` even though the two
    sides are the identical generic text. An identity check here (what this
    read before) is therefore true for every builtin constant regardless of
    whether it carries a real docstring, which is exactly the "exempted
    every constant" failure the paragraph above warns against -- adversarial
    lens, F increment, konyklabs/roadmap#74, verified by mutation: deleting
    an attribute docstring under a checked-in constant left this file's own
    suite green.

    The value comparison also gets pytest's fixtures right for free. A
    ``@pytest.fixture`` is not a function by the time it reaches ``__all__``
    -- it is a ``FixtureFunctionDefinition`` -- but it carries the decorated
    function's own docstring, genuinely distinct from
    ``FixtureFunctionDefinition.__doc__``, so no unwrapping is needed here.
    """
    own = getattr(value, "__doc__", None)
    if own and own.strip() and own != type(value).__doc__:
        # A dataclass with no author docstring gets a generated one that is
        # just its own signature. That is not an explanation of anything.
        generated = own.startswith(f"{getattr(value, '__name__', chr(0))}(")
        if not generated:
            return True
    return name in _documented_constants()


def test_is_documented_does_not_exempt_a_bare_constant_via_identity() -> None:
    """Adversarial lens, F increment (konyklabs/roadmap#74): a bare builtin's
    ``__doc__`` is read off its *type* through a getset descriptor that hands
    back a freshly built ``str`` on every access, so ``own is
    type(value).__doc__`` is ``False`` even when the two sides are the
    identical generic text -- confirmed here for the three shapes the public
    surface actually uses. An identity check in :func:`_is_documented` (what
    this used to be, before this fix) therefore treated every bare
    int/float/str/tuple constant as self-documenting regardless of whether it
    carries a real docstring, which is exactly the "exempted every constant"
    failure the function's own docstring warns against. A name no attribute
    docstring anywhere in the tree will ever index must fall through and
    answer ``False`` for each of these, not pass on its class's generic text.
    """
    for value in (30.0, "some string", (1, 2, 3), 5, True):
        assert _is_documented("NOT_A_REAL_CONSTANT_NAME_ANYWHERE_IN_THE_TREE", value) is False


def test_is_documented_recognizes_a_real_own_docstring() -> None:
    """The positive case the fix must not break: a function's ``__doc__`` is
    genuinely its own -- distinct in content from ``type(value).__doc__``,
    ``function``'s generic description -- so it is documented without any
    attribute-docstring lookup.
    """

    def helper() -> None:
        """A real docstring, not shared with any type."""

    assert _is_documented("NOT_A_REAL_CONSTANT_NAME_ANYWHERE_IN_THE_TREE", helper) is True


# ---------------------------------------------------------------------------
# The three assertions.
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
def test_every_public_symbol_explains_itself(module_name: str) -> None:
    """A docstring on every exported name, and on the module itself.

    Not a style rule. The contract's promise is that these names keep working,
    which is only worth anything if a consumer can find out what they do
    without reading the implementation -- and the implementation is the half
    that is allowed to change.
    """
    module = importlib.import_module(module_name)
    assert module.__doc__ and module.__doc__.strip(), f"{module_name} has no module docstring"
    undocumented = [name for name in module.__all__ if not _is_documented(name, getattr(module, name))]
    assert not undocumented, f"{module_name} exports undocumented names: {undocumented}"


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

WHITE_BOX_HANDLES = ("unit", "unit.context.store")
"""Every dotted attribute path ``docs/api-contract.md``'s *White-box handles*
section names as documented and supported, read off a ``StartedUnit``.

Round 1 of this review found the doc naming ``started.unit.store`` -- copied
from an earlier internal shape rather than checked against the code, where
the real attribute is ``started.unit.context.store``. Prose describing an
attribute path is exactly the kind of claim this module exists to make
mechanical rather than trust to a read-through, so it is resolved here: a
rename to ``Unit.context`` or ``UnitContext.store`` fails this test instead of
leaving the contract quietly wrong again.
"""


def test_the_documented_white_box_handles_still_resolve() -> None:
    from vendorfake.testing import unit

    with unit("square") as started:
        for path in WHITE_BOX_HANDLES:
            target: object = started
            for attr in path.split("."):
                assert hasattr(target, attr), (
                    f"docs/api-contract.md names started.{path} as a white-box handle, but "
                    f"{type(target).__name__!r} has no {attr!r}"
                )
                target = getattr(target, attr)
