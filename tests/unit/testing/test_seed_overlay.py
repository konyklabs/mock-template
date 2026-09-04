"""``seed_overlay=`` on the three bindings, and the layer it becomes.

The merge rule itself is pinned next door, on the pure function
(``tests/unit/core/test_config_overlay.py``). What is asserted here is the
part a consumer touches: that the overlay reaches the store before the first
request, that it is a layer *under* ``env=`` exactly as ``seed`` and
``clock_start`` are, that a path and an inline document mean the same thing,
that ``GET /__unit/info`` reports it without publishing it, and that
``served()`` refuses the environment variable in favour of the parameter.

``served()`` is exercised through one subprocess test, marked ``integration``
like every other test that spawns one. The refusals around it need no child
and are ordinary unit tests: they are raised before ``Popen``, which is the
whole point of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import vendorfake.square
from vendorfake.core.config.overlay import seed_overlay_digest
from vendorfake.core.kernel.types import UnitError
from vendorfake.testing import async_unit, served, unit

_SHIPPED_SEED = Path(vendorfake.square.__file__).parent / "seed" / "default.seed.json"
"""The document the shipped profiles name. Read, never written: two tests
build an *alternate* seed from a copy of it, which is the only honest way to
exercise the ``VENDORFAKE_SEED`` layer under an overlay -- a hand-written
stand-in would lack the tokens the unit authenticates with, and would fail for
a reason that has nothing to do with the overlay."""

OVERLAY = {"merchant": {"business_name": "Overlaid Roasters"}}
"""One collection, one field: the shape a real overlay has. ``business_name``
because it is on the response body of a route the ``full`` profile serves, so
the assertion is on what the unit *answers*, not on what it loaded."""


def _merchant(started: object) -> dict[str, object]:
    driver = started
    body = driver.client.get(f"/v2/merchants/{driver.seed.merchant_id}", headers=driver.seed.auth).json()  # type: ignore[attr-defined]
    merchant: dict[str, object] = body["merchant"]
    return merchant


# ---------------------------------------------------------------------------
# It reaches the store, before the first request.
# ---------------------------------------------------------------------------


def test_an_inline_overlay_is_answered_from_the_first_request() -> None:
    """Not "after a reset", not "on the second call": the merge happens while
    the unit is built, so the very first read already sees it."""
    with unit("square", seed_overlay=OVERLAY) as square:
        assert _merchant(square)["business_name"] == "Overlaid Roasters"


def test_a_unit_with_no_overlay_still_answers_the_shipped_scenario() -> None:
    with unit("square") as square:
        assert _merchant(square)["business_name"] == "Jet Fuel Coffee"


def test_an_overlay_from_a_file_means_the_same_as_one_inline(tmp_path: Path) -> None:
    document = tmp_path / "overlay.json"
    document.write_text(json.dumps(OVERLAY), encoding="utf-8")
    with unit("square", seed_overlay=document) as from_file, unit("square", seed_overlay=OVERLAY) as inline:
        assert _merchant(from_file) == _merchant(inline)


def test_a_path_given_as_a_string_is_read_as_a_path_and_not_as_json(tmp_path: Path) -> None:
    document = tmp_path / "overlay.json"
    document.write_text(json.dumps(OVERLAY), encoding="utf-8")
    with unit("square", seed_overlay=str(document)) as square:
        assert _merchant(square)["business_name"] == "Overlaid Roasters"


def test_a_missing_overlay_file_is_refused_naming_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(UnitError) as refused, unit("square", seed_overlay=missing):
        pass
    assert str(missing) in str(refused.value)


def test_an_unknown_collection_is_refused_when_the_unit_is_built() -> None:
    with pytest.raises(UnitError) as refused, unit("square", seed_overlay={"merchants": {}}):
        pass
    message = str(refused.value)
    assert "'merchants'" in message
    assert "Valid collections:" in message


def test_the_overlay_leaves_the_collections_it_never_names_alone() -> None:
    """The merge is a delta, not a replacement: a consumer overriding the
    merchant must not lose the catalog the scenario is useful for."""
    with unit("square", seed_overlay=OVERLAY) as overlaid, unit("square") as plain:
        assert (
            overlaid.client.get("/__unit/state").json()["entities"]
            == (plain.client.get("/__unit/state").json()["entities"])
        )


def test_two_units_on_the_same_overlay_still_agree_entity_for_entity() -> None:
    """Determinism is not bought off by an overlay: the ids are still drawn
    from the profile's seed, so an assertion written against an overlaid unit
    is as stable as one against the shipped scenario."""
    with unit("square", seed_overlay=OVERLAY) as first, unit("square", seed_overlay=OVERLAY) as second:
        assert (
            first.client.get("/__unit/state").json()["digest"] == (second.client.get("/__unit/state").json()["digest"])
        )


# ---------------------------------------------------------------------------
# The layer, and its precedence.
# ---------------------------------------------------------------------------


def test_the_parameter_becomes_the_env_layer_and_an_explicit_entry_beats_it() -> None:
    """``seed_overlay=`` is the ``VENDORFAKE_SEED_OVERLAY`` layer, which is
    what ``seed=`` and ``clock_start=`` already are: one ``env`` mapping built
    for a module means the same thing whichever way the unit is started."""
    with unit(
        "square",
        seed_overlay=OVERLAY,
        env={"VENDORFAKE_SEED_OVERLAY": json.dumps({"merchant": {"business_name": "From env"}})},
    ) as square:
        assert _merchant(square)["business_name"] == "From env"


def test_the_env_variable_alone_works_with_no_parameter() -> None:
    with unit("square", env={"VENDORFAKE_SEED_OVERLAY": json.dumps(OVERLAY)}) as square:
        assert _merchant(square)["business_name"] == "Overlaid Roasters"


def test_the_overlay_merges_over_the_document_vendorfake_seed_names(tmp_path: Path) -> None:
    """The base is *whatever seed document actually loaded* -- the profile's,
    or the one ``VENDORFAKE_SEED`` pointed at instead. Seed first, overlay on
    top: a collection the alternate document carries survives, and the one the
    overlay names wins.
    """
    document = json.loads(_SHIPPED_SEED.read_text(encoding="utf-8"))
    document["merchant"]["business_name"] = "Alternate"
    document["merchant"]["language_code"] = "fr-CA"
    alternate = tmp_path / "alt.seed.json"
    alternate.write_text(json.dumps(document), encoding="utf-8")
    with unit(
        "square",
        seed_overlay={"merchant": {"business_name": "Overlaid Roasters"}},
        env={"VENDORFAKE_SEED": str(alternate)},
    ) as square:
        merchant = _merchant(square)
    # The overlay won on the field it named; the alternate document still won
    # on the field only it changed, which is what "seed first, overlay on top"
    # means.
    assert merchant["business_name"] == "Overlaid Roasters"
    assert merchant["language_code"] == "fr-CA"


def test_the_overlay_is_checked_against_the_document_vendorfake_seed_names(tmp_path: Path) -> None:
    """...and the refusal lists that document's collections, not the
    profile's: a key valid against the shipped seed is not valid against an
    alternate one that does not carry it."""
    alternate = tmp_path / "alt.seed.json"
    alternate.write_text(json.dumps({"merchant": {"id": "M1"}}), encoding="utf-8")
    with (
        pytest.raises(UnitError) as refused,
        unit("square", seed_overlay={"orders": []}, env={"VENDORFAKE_SEED": str(alternate)}),
    ):
        pass
    assert "Valid collections: merchant." in str(refused.value)


def test_an_env_value_starting_with_a_brace_is_read_as_inline_json() -> None:
    """The discriminator is the character, so a path and a document can never
    be confused -- and leading whitespace does not change the answer."""
    with unit("square", env={"VENDORFAKE_SEED_OVERLAY": "  \n" + json.dumps(OVERLAY)}) as square:
        assert _merchant(square)["business_name"] == "Overlaid Roasters"


def test_malformed_inline_json_is_refused_saying_it_was_read_as_json() -> None:
    with pytest.raises(UnitError) as refused, unit("square", env={"VENDORFAKE_SEED_OVERLAY": '{"merchant":'}):
        pass
    assert "inline JSON" in str(refused.value)


def test_an_overlay_file_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    """The only way to reach the case: a value starting with ``{`` can only
    decode to an object, so a document that is not one arrives from a file."""
    document = tmp_path / "overlay.json"
    document.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(UnitError) as refused, unit("square", seed_overlay=document):
        pass
    assert "must be a JSON object" in str(refused.value)


def test_the_overlay_survives_the_capability_route_into_a_profile() -> None:
    """``capabilities=`` resolves to a profile and re-lays the environment; an
    overlay passed alongside it must still arrive."""
    with unit("square", capabilities=["orders"], seed_overlay=OVERLAY) as square:
        assert square.client.get("/__unit/info").json()["seed_overlay"]["active"] is True


# ---------------------------------------------------------------------------
# What GET /__unit/info publishes, and what it does not.
# ---------------------------------------------------------------------------


def test_info_reports_the_overlay_as_active_with_its_digest() -> None:
    with unit("square", seed_overlay=OVERLAY) as square:
        reported = square.client.get("/__unit/info").json()["seed_overlay"]
    assert reported == {"active": True, "digest": seed_overlay_digest(OVERLAY)}


def test_info_reports_no_overlay_when_none_was_given() -> None:
    with unit("square") as square:
        assert square.client.get("/__unit/info").json()["seed_overlay"] == {"active": False, "digest": None}


def test_info_never_publishes_the_overlay_contents() -> None:
    """An inline overlay may carry a consumer's own credentials. The digest is
    the whole of what is published, and this asserts it over the entire
    document rather than over the one block, because a leak would be a value
    that appeared somewhere else."""
    secret = "sk-not-a-real-credential-9f2b"
    with unit("square", seed_overlay={"merchant": {"business_name": secret}}) as square:
        body = square.client.get("/__unit/info").text
    assert secret not in body
    assert "seed_overlay" in body


def test_the_digest_is_the_same_however_the_overlay_was_spelled() -> None:
    reordered = {"merchant": {"business_name": "Overlaid Roasters"}}
    with unit("square", seed_overlay=OVERLAY) as first, unit("square", seed_overlay=reordered) as second:
        assert (
            first.client.get("/__unit/info").json()["seed_overlay"]["digest"]
            == second.client.get("/__unit/info").json()["seed_overlay"]["digest"]
        )


# ---------------------------------------------------------------------------
# async_unit delegates, and served refuses the variable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_unit_carries_the_overlay_through() -> None:
    async with async_unit("square", seed_overlay=OVERLAY) as square:
        response = await square.async_client.get(f"/v2/merchants/{square.seed.merchant_id}", headers=square.seed.auth)
    assert response.json()["merchant"]["business_name"] == "Overlaid Roasters"


def test_served_refuses_the_variable_and_names_the_parameter() -> None:
    """Before ``Popen``, like every other refusal ``served(env=)`` makes: an
    entry that reached the child would be checked only there, and a misspelled
    collection would present as a child that died before announcing a port."""
    with (
        pytest.raises(ValueError, match="seed_overlay=") as refused,
        served("square", env={"VENDORFAKE_SEED_OVERLAY": "{}"}) as child,
    ):
        pytest.fail(f"served() spawned a child at {child.base_url} for an env entry it must refuse")
    assert "VENDORFAKE_SEED_OVERLAY" in str(refused.value)


def test_served_refuses_an_unknown_collection_in_the_parent_process() -> None:
    """The eager failure ``served()`` already promises for a bad profile and a
    seedless vendor. No child is spawned; the exception is the parent's."""
    with pytest.raises(UnitError) as refused, served("square", seed_overlay={"merchants": {}}) as child:
        pytest.fail(f"served() spawned a child at {child.base_url} for an overlay it must refuse")
    assert "'merchants'" in str(refused.value)


@pytest.mark.integration
def test_served_hands_the_overlay_to_the_child() -> None:
    with served("square", seed_overlay=OVERLAY) as child:
        body = child.client.get(f"/v2/merchants/{child.seed.merchant_id}", headers=child.seed.auth).json()
        reported = child.client.get("/__unit/info").json()["seed_overlay"]
    assert body["merchant"]["business_name"] == "Overlaid Roasters"
    assert reported == {"active": True, "digest": seed_overlay_digest(OVERLAY)}
