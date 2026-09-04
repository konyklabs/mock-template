"""The Menus API V3 document: how it is stored, projected, and indexed.

DOCUMENTED (toast-menus-api-v3.yaml, https://doc.toasttab.com/doc/devguide/apiMenusV3.html):
``GET /menus/v3/menus`` answers ``{restaurantGuid, lastUpdated,
restaurantTimeZone, menus[], modifierGroupReferences{}, modifierOptionReferences{},
preModifierGroupReferences{}}`` where the three reference maps are keyed by the
integer ``referenceId`` spelled as a string ("2", "6", "10"), and
``GET /menus/v3/metadata`` answers ``{restaurantGuid, lastUpdated}``.

STORED as one entity per restaurant in the ``menus`` collection: the seed's
document with money in cents and ``lastUpdated`` in epoch ms. The projection
walks it, converting the money keys (``price``, ``fixedPrice``, ``basePrice``,
``timeSpecificPrice``) to decimal dollars and the instant to the REST spelling,
and turns the reference lists into the documented maps.

The index -- :func:`menu_items_by_guid`, :func:`modifier_options_by_guid`,
:func:`pre_modifiers_by_guid` -- is what the orders surface prices a selection
from: an item's ``price`` and ``taxInfo``, an option's ``price``, a
pre-modifier's ``multiplicationFactor``/``fixedPrice``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.toast.model.dates import rest_date
from vendorfake.toast.model.money import to_dollars

__all__ = [
    "MONEY_KEYS",
    "menu_items_by_guid",
    "modifier_options_by_guid",
    "pre_modifiers_by_guid",
    "project_menu_metadata",
    "project_menu_v3",
]

MONEY_KEYS: frozenset[str] = frozenset({"price", "fixedPrice", "basePrice", "timeSpecificPrice"})
"""Keys whose integer value is cents in the store and dollars on the wire."""


def _dollars(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (_dollars(item) if key not in MONEY_KEYS else _money(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_dollars(item) for item in value]
    return value


def _money(value: Any) -> Any:
    return to_dollars(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _by_reference(rows: Any) -> dict[str, Any]:
    if not isinstance(rows, list):
        return {}
    return {str(row["referenceId"]): _dollars(row) for row in rows if isinstance(row, dict) and "referenceId" in row}


def _omit_nulls(node: Any) -> Any:
    """JUDGMENT: an optional field the document has no value for is omitted,
    not answered null. The menus specification marks the fields it means to
    be null with ``x-nullable`` and types the rest as plain strings and
    objects; the guide page that would show a full response answers 403 to
    anything but a browser, so the specification is the source. Found by the
    fidelity validator (konyklabs/roadmap#56)."""
    if isinstance(node, dict):
        return {key: _omit_nulls(value) for key, value in node.items() if value is not None}
    if isinstance(node, list):
        return [_omit_nulls(item) for item in node]
    return node


def project_menu_v3(entity: Mapping[str, Any], *, time_zone: str) -> dict[str, Any]:
    """The whole documented document, keys in the specification's order."""
    document = _omit_nulls(_menu_document(entity, time_zone=time_zone))
    return dict(document)


def _menu_document(entity: Mapping[str, Any], *, time_zone: str) -> dict[str, Any]:
    return {
        "restaurantGuid": str(entity["id"]),
        "lastUpdated": rest_date(int(entity.get("lastUpdated", 0))),
        "restaurantTimeZone": time_zone,
        "menus": _dollars(list(entity.get("menus", []))),
        "modifierGroupReferences": _by_reference(entity.get("modifierGroups")),
        "modifierOptionReferences": _by_reference(entity.get("modifierOptions")),
        "preModifierGroupReferences": _by_reference(entity.get("preModifierGroups")),
    }


def project_menu_metadata(entity: Mapping[str, Any]) -> dict[str, Any]:
    return {"restaurantGuid": str(entity["id"]), "lastUpdated": rest_date(int(entity.get("lastUpdated", 0)))}


def _walk_groups(groups: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not isinstance(groups, list):
        return found
    for group in groups:
        if not isinstance(group, dict):
            continue
        found.extend(item for item in group.get("menuItems", []) if isinstance(item, dict))
        found.extend(_walk_groups(group.get("menuGroups")))
    return found


def menu_items_by_guid(entity: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every item in every menu, as stored (cents), keyed by guid."""
    items: dict[str, dict[str, Any]] = {}
    for menu in entity.get("menus", []):
        if isinstance(menu, dict):
            for item in _walk_groups(menu.get("menuGroups")):
                items[str(item["guid"])] = item
    return items


def modifier_options_by_guid(entity: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = entity.get("modifierOptions", [])
    return {str(row["guid"]): row for row in rows if isinstance(row, dict)} if isinstance(rows, list) else {}


def pre_modifiers_by_guid(entity: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    groups = entity.get("preModifierGroups", [])
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict):
                for pre in group.get("preModifiers", []):
                    if isinstance(pre, dict):
                        found[str(pre["guid"])] = pre
    return found
