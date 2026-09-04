"""The scope this unit demands on a route is the scope the specification's own
prose demands on that operation.

WHY THIS EXISTS. Lightspeed does not state a required scope where OpenAPI has a
place for one. ``components.securitySchemes`` holds a single flat
``http``/``bearer`` scheme with no scopes at all, the document's root
``security`` is ``[{"bearerAuth": []}]``, and the real rule lives in each
operation's *description* as a line reading

    🔒 Requires: ``products:write`` scope

-- or two backticked scopes and the plural for the three operations that need a
pair. Prose is exactly what a fidelity extract strips, so before
konyklabs/roadmap#94's L3 the scope table in ``lightspeed/capabilities.py`` and
the ``scopes=(...)`` on every ``Route`` were checkable against nothing at all,
and a typo in either would have been invisible.

WHAT MAKES IT CHECKABLE NOW. The declaration carries an ``annotations`` row
(``vendorfake.fidelity.types.Annotation``): the cutter reads each modeled
operation's description *before* stripping it, applies the declared regular
expressions, and records what it found under ``x-vendorfake.annotations.scopes``
in ``extract.json`` -- where ``pin.json``'s sha256 covers it, so the scope list
cannot be edited by hand without ``fidelity pin --check --offline`` going red.
This module is the other half: it compares that record against the routes the
unit actually serves.

The mechanism is vendor-neutral. Nothing in ``vendorfake.fidelity`` knows what a
Lightspeed scope is; it knows how to run two regular expressions a declaration
gave it.
"""

from __future__ import annotations

from vendorfake.fidelity import load_declaration, load_extract
from vendorfake.fidelity.types import route_key
from vendorfake.registry import resolve_vendor

ANCHOR = "vendorfake.lightspeed.fidelity"
ANNOTATION = "scopes"

#: Routes this unit serves that the specification does not describe at all, so
#: there is no annotation to check them against. Both are documented in prose
#: on https://x-series-api.lightspeedhq.com/docs/authorization and both are
#: excused in ``declaration.json`` for the same reason. **JUDGMENT**: neither
#: demands a scope here, because one issues the credential and the other
#: exchanges it -- there is no granted scope set to check against yet.
UNANNOTATED_BY_DESIGN = {
    "GET /connect": "the authorize stand-in: it issues the code, so there is no token to carry a scope",
    "POST /api/1.0/token": "the token endpoint: it issues the credential the scopes are granted on",
}


def _routes() -> tuple[object, ...]:
    return tuple(resolve_vendor("lightspeed").routes)


def _vendor_scopes() -> dict[str, set[str]]:
    return {
        route_key(route.method, route.path): set(route.scopes)  # type: ignore[attr-defined]
        for route in _routes()
        if not route.internal and not route.path.startswith("/__")  # type: ignore[attr-defined]
    }


def test_every_modeled_route_carries_the_scope_the_specification_annotates() -> None:
    annotated = load_extract(ANCHOR).annotations(ANNOTATION)
    assert annotated, (
        f"no scope annotations in the extract; x-vendorfake carries {sorted(load_extract(ANCHOR).metadata)}"
    )
    disagreements = {
        key: {"spec": sorted(spec), "unit": sorted(_vendor_scopes().get(key, set()))}
        for key, spec in ((key, set(values)) for key, values in annotated.items())
        if spec != _vendor_scopes().get(key, set())
    }
    assert disagreements == {}, (
        "these routes demand a different scope set from the one their operation's "
        f"own description annotates: {disagreements}"
    )


def test_the_three_operations_that_name_a_PAIR_of_scopes_get_both() -> None:
    """Read out of the document directly, because a single-scope pattern misses
    them: ``GetRetailer``, ``CloseRegister`` and ``initReturnSale`` each name two."""
    annotated = load_extract(ANCHOR).annotations(ANNOTATION)
    pairs = {key: sorted(values) for key, values in annotated.items() if len(values) > 1}
    assert pairs == {
        "GET /api/2026-07/retailer": ["payment_types:read", "retailer:read"],
        "POST /api/2026-07/sales/{sale_id}/actions/return": ["sales:write", "users:read"],
        "PUT /api/2026-07/registers/{register_id}/actions/close": ["payment_types:read", "register:close"],
    }


def test_every_route_the_specification_describes_is_annotated() -> None:
    """All 35 of them. A modeled operation with no scope line would be a hole in
    the check above -- it would simply not be compared -- so the absence is
    asserted rather than assumed."""
    declaration = load_declaration(ANCHOR)
    extract = load_extract(ANCHOR)
    modeled = {str(key) for key in extract.metadata.get("modeled", ())}
    annotated = set(extract.annotations(ANNOTATION))
    assert modeled - annotated == set(), (
        f"modeled operations with no '🔒 Requires: ...' line in their description: {sorted(modeled - annotated)}"
    )
    assert len(modeled) == 35
    assert {excuse.key for excuse in declaration.excused} == set(UNANNOTATED_BY_DESIGN)


def test_the_routes_with_no_annotation_are_exactly_the_two_that_are_excused() -> None:
    annotated = set(load_extract(ANCHOR).annotations(ANNOTATION))
    unannotated = {key for key in _vendor_scopes() if key not in annotated}
    assert unannotated == set(UNANNOTATED_BY_DESIGN)
    assert all(not scopes for key, scopes in _vendor_scopes().items() if key in UNANNOTATED_BY_DESIGN)


def test_a_scope_the_unit_demands_is_one_the_documented_scope_list_contains() -> None:
    """Every scope on a route is one of the seventeen this unit publishes, and
    every published scope is used by at least one route: an annotation naming a
    scope the config does not know would otherwise pass the comparison above by
    agreeing with a typo on both sides."""
    from vendorfake.lightspeed.config import DOCUMENTED_SCOPES

    demanded = {scope for scopes in _vendor_scopes().values() for scope in scopes}
    assert demanded - set(DOCUMENTED_SCOPES) == set()
    assert set(DOCUMENTED_SCOPES) - demanded == set()
