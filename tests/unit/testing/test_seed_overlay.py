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

OVERLAY = {"loyalty_program": {"terminology_one": "Overlaid Point"}}
"""One collection, one field: the shape a real overlay has.

``loyalty_program`` because it is a collection an overlay may actually name --
``merchant`` and ``tokens`` are the two ``.seed`` is built from and are refused
-- and ``terminology_one`` because it reaches the response body of a route the
``full`` profile serves, so the assertion is on what the unit *answers*, not on
what it loaded. The shipped value is ``"Point"``.
"""


def _terminology(started: object) -> str:
    """The loyalty program's singular term, as the unit answers it."""
    driver = started
    body = driver.client.get(  # type: ignore[attr-defined]
        f"/v2/loyalty/programs/{driver.seed.loyalty_program_id}",  # type: ignore[attr-defined]
        headers=driver.seed.auth,  # type: ignore[attr-defined]
    ).json()
    term: str = body["program"]["terminology"]["one"]
    return term


# ---------------------------------------------------------------------------
# It reaches the store, before the first request.
# ---------------------------------------------------------------------------


def test_an_inline_overlay_is_answered_from_the_first_request() -> None:
    """Not "after a reset", not "on the second call": the merge happens while
    the unit is built, so the very first read already sees it."""
    with unit("square", seed_overlay=OVERLAY) as square:
        assert _terminology(square) == "Overlaid Point"


def test_a_unit_with_no_overlay_still_answers_the_shipped_scenario() -> None:
    with unit("square") as square:
        assert _terminology(square) == "Point"


def test_an_overlay_from_a_file_means_the_same_as_one_inline(tmp_path: Path) -> None:
    document = tmp_path / "overlay.json"
    document.write_text(json.dumps(OVERLAY), encoding="utf-8")
    with unit("square", seed_overlay=document) as from_file, unit("square", seed_overlay=OVERLAY) as inline:
        assert _terminology(from_file) == _terminology(inline) == "Overlaid Point"


def test_a_path_given_as_a_string_is_read_as_a_path_and_not_as_json(tmp_path: Path) -> None:
    document = tmp_path / "overlay.json"
    document.write_text(json.dumps(OVERLAY), encoding="utf-8")
    with unit("square", seed_overlay=str(document)) as square:
        assert _terminology(square) == "Overlaid Point"


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
    loyalty program must not lose the catalog the scenario is useful for."""
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
# The collections `.seed` is built from are refused.
#
# Review measured the hole these close: `served("square", seed_overlay={"tokens":
# [...]})` spawned a child that hydrated the overlaid token while the parent's
# `.seed.access_token` still carried the shipped one, so every
# `child.client.get(path, headers=child.seed.auth)` answered 401 with nothing
# anywhere naming the overlay. `unit()` had the identical hole. The fix is a
# refusal rather than a seed that follows the document, and these pin it on
# every vendor and every binding.
# ---------------------------------------------------------------------------

SEED_BOUND: tuple[tuple[str, str, object], ...] = (
    ("square", "tokens", []),
    ("square", "merchant", {}),
    ("clover", "tokens", []),
    ("clover", "merchant", {}),
    ("toast", "tokens", []),
    ("toast", "restaurant", {}),
)
"""Every (vendor, collection) pair ``.seed`` is built from, and a value of the
shape that collection has.

Written out rather than read from ``seed_collections_for``: a test that asked
the implementation what it refuses would pass whatever the implementation said,
including nothing. This is the list changing it has to disagree with.

The value is shaped correctly on purpose. The refusal is raised once the
profile has loaded, which is after the vendor has hydrated the merged document,
so an overlay that is *also* the wrong shape is refused first and by hydration
-- a correct refusal with a different message. What a consumer actually writes
is well-shaped (``{"tokens": [<a token>]}``, ``{"merchant": {"id": ...}}``),
and that is the case this pins.
"""


@pytest.mark.parametrize(("vendor", "collection", "value"), SEED_BOUND)
def test_an_overlay_on_a_seed_bound_collection_is_refused(vendor: str, collection: str, value: object) -> None:
    with pytest.raises(UnitError) as refused, unit(vendor, seed_overlay={collection: value}):
        pytest.fail(f"unit({vendor!r}) started on an overlay of {collection!r}, which .seed is built from")
    message = str(refused.value)
    assert repr(collection) in message
    assert ".seed" in message


def test_the_refusal_says_what_seed_describes_and_what_to_do_instead() -> None:
    """The message is the whole value of the refusal: a consumer who reads it
    must learn why ``.seed`` cannot follow the document and what to reach for
    instead, without going to the source."""
    with pytest.raises(UnitError) as refused, unit("square", seed_overlay={"tokens": []}):
        pass
    message = str(refused.value)
    assert "SHIPPED credentials and identity" in message
    assert "VENDORFAKE_SEED" in message


def test_a_seed_bound_collection_is_refused_through_the_env_variable_too() -> None:
    """The check is on the overlay that actually loaded, not on the parameter,
    so the variable route is closed by the same code."""
    with (
        pytest.raises(UnitError) as refused,
        unit("square", env={"VENDORFAKE_SEED_OVERLAY": json.dumps({"tokens": []})}),
    ):
        pass
    assert "'tokens'" in str(refused.value)


def test_an_overlay_naming_one_seed_bound_collection_among_others_is_refused() -> None:
    """The refusal is on the intersection, and names only the offending half:
    an overlay is a document, and a consumer editing it needs to know which key
    to take out."""
    with (
        pytest.raises(UnitError) as refused,
        unit("square", seed_overlay={"orders": [], "tokens": []}),
    ):
        pass
    message = str(refused.value)
    assert "'tokens'" in message
    assert "'orders'" not in message


def test_served_refuses_a_seed_bound_collection_in_the_parent_process() -> None:
    """Before ``Popen``: the child would start perfectly and every request made
    with ``child.seed.auth`` would 401."""
    with pytest.raises(UnitError) as refused, served("square", seed_overlay={"tokens": []}) as child:
        pytest.fail(f"served() spawned a child at {child.base_url} for an overlay it must refuse")
    assert "'tokens'" in str(refused.value)


def test_a_collection_seed_does_not_speak_for_is_still_overlayable() -> None:
    """The refusal is two collections wide, not a ban on overlays: the ids
    ``.seed`` also carries diverge visibly (a 404 on the entity you replaced)
    rather than silently, so they are left alone."""
    with unit("square", seed_overlay={"orders": []}) as square:
        assert square.client.get("/__unit/state").json()["entities"].get("orders", 0) == 0
        assert (
            square.client.get("/v2/merchants/" + square.seed.merchant_id, headers=square.seed.auth).status_code == 200
        )


# ---------------------------------------------------------------------------
# `null` deletes, and hydration still validates what is left.
# ---------------------------------------------------------------------------


def test_a_null_deletion_reaches_hydration_and_the_unit_starts_without_it() -> None:
    """``docs/concepts/seed.md`` documents ``null`` as deletion and this is the
    end-to-end half of it: the pure function's rule is pinned next door, and
    what a consumer needs to know is that the unit *starts* with the collection
    gone."""
    with unit("square", seed_overlay={"orders": None}) as square:
        entities = square.client.get("/__unit/state").json()["entities"]
    assert "orders" not in entities or entities["orders"] == 0


def test_deleting_a_collection_another_one_references_is_refused_by_hydration() -> None:
    """The merge rule says what comes out; the vendor still decides whether it
    loads. Review found the page recommending exactly this deletion as its
    motivating example, so the page now says so and this pins it."""
    with pytest.raises(UnitError) as refused, unit("square", seed_overlay={"loyalty_program": None}):
        pass
    assert "loyalty_accounts" in str(refused.value)


def test_deleting_the_referrer_alongside_it_starts_the_unit() -> None:
    """...and the fix the page gives: remove what pointed at it, in the same
    overlay."""
    with unit("square", seed_overlay={"loyalty_program": None, "loyalty_accounts": None}) as square:
        entities = square.client.get("/__unit/state").json()["entities"]
    assert entities.get("loyalty_programs", 0) == 0
    assert entities.get("loyalty_accounts", 0) == 0


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
        env={"VENDORFAKE_SEED_OVERLAY": json.dumps({"loyalty_program": {"terminology_one": "From env"}})},
    ) as square:
        assert _terminology(square) == "From env"


def test_the_env_variable_alone_works_with_no_parameter() -> None:
    with unit("square", env={"VENDORFAKE_SEED_OVERLAY": json.dumps(OVERLAY)}) as square:
        assert _terminology(square) == "Overlaid Point"


def test_the_overlay_merges_over_the_document_vendorfake_seed_names(tmp_path: Path) -> None:
    """The base is *whatever seed document actually loaded* -- the profile's,
    or the one ``VENDORFAKE_SEED`` pointed at instead. Seed first, overlay on
    top: a collection the alternate document carries survives, and the one the
    overlay names wins.
    """
    document = json.loads(_SHIPPED_SEED.read_text(encoding="utf-8"))
    document["loyalty_program"]["terminology_one"] = "Alternate"
    document["loyalty_program"]["terminology_other"] = "Alternates"
    alternate = tmp_path / "alt.seed.json"
    alternate.write_text(json.dumps(document), encoding="utf-8")
    with unit(
        "square",
        seed_overlay=OVERLAY,
        env={"VENDORFAKE_SEED": str(alternate)},
    ) as square:
        terminology = square.client.get(
            f"/v2/loyalty/programs/{square.seed.loyalty_program_id}", headers=square.seed.auth
        ).json()["program"]["terminology"]
    # The overlay won on the field it named; the alternate document still won
    # on the field only it changed, which is what "seed first, overlay on top"
    # means.
    assert terminology["one"] == "Overlaid Point"
    assert terminology["other"] == "Alternates"


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
        assert _terminology(square) == "Overlaid Point"


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
    with unit("square", seed_overlay={"loyalty_program": {"terminology_one": secret}}) as square:
        body = square.client.get("/__unit/info").text
    assert secret not in body
    assert "seed_overlay" in body


def test_the_digest_is_the_same_however_the_overlay_was_spelled() -> None:
    spelled_one_way = {"loyalty_program": {"terminology_one": "One", "terminology_other": "Many"}}
    reordered = {"loyalty_program": {"terminology_other": "Many", "terminology_one": "One"}}
    with (
        unit("square", seed_overlay=spelled_one_way) as first,
        unit("square", seed_overlay=reordered) as second,
    ):
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
        response = await square.async_client.get(
            f"/v2/loyalty/programs/{square.seed.loyalty_program_id}", headers=square.seed.auth
        )
    assert response.json()["program"]["terminology"]["one"] == "Overlaid Point"


@pytest.mark.asyncio
async def test_async_unit_refuses_a_seed_bound_collection_too() -> None:
    """The third binding, asserted rather than inferred from the shared
    generator: its docstring makes the same promise, so it carries the same
    test."""
    with pytest.raises(UnitError) as refused:
        async with async_unit("square", seed_overlay={"tokens": []}) as started:
            pytest.fail(f"async_unit() started on an overlay of 'tokens', with seed {started.seed!r}")
    assert "'tokens'" in str(refused.value)


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
        body = child.client.get(f"/v2/loyalty/programs/{child.seed.loyalty_program_id}", headers=child.seed.auth).json()
        reported = child.client.get("/__unit/info").json()["seed_overlay"]
    assert body["program"]["terminology"]["one"] == "Overlaid Point"
    assert reported == {"active": True, "digest": seed_overlay_digest(OVERLAY)}


# ---------------------------------------------------------------------------
# The parent checks the overlay against the document the CHILD will load.
#
# `served()` validates the overlay in this process so a misspelled collection
# is refused where the caller can see it. That promise only holds if the
# parent merges over the same base the child will -- and the deep review lens
# measured it merging over the profile's own seed while the child merged over
# the document `VENDORFAKE_SEED` named, in both directions: an overlay the
# parent passed killed the child before it announced a port, and an overlay
# the child would have taken was refused here with a listing of collections
# the child never uses.
# ---------------------------------------------------------------------------


def _house_scenario(tmp_path: Path) -> Path:
    """The shipped Square seed with ``orders`` taken out, written to disk.

    Stands in for a container image that exports ``VENDORFAKE_SEED`` for a
    whole suite -- a documented, first-class variable, not a contrivance. It
    is derived from the real document rather than hand-written so that the
    unit it produces still authenticates and still hydrates; only the one
    collection under test is missing.
    """
    document = json.loads(_SHIPPED_SEED.read_text(encoding="utf-8"))
    del document["orders"]
    house = tmp_path / "house.seed.json"
    house.write_text(json.dumps(document), encoding="utf-8")
    return house


def test_served_checks_the_overlay_against_the_document_ambient_seed_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collection the ambient scenario dropped is refused HERE, and the
    listing is that document's collections rather than the profile's."""
    monkeypatch.setenv("VENDORFAKE_SEED", str(_house_scenario(tmp_path)))

    with pytest.raises(UnitError) as refused, served("square", seed_overlay={"orders": []}) as child:
        pytest.fail(f"served() spawned a child at {child.base_url} for an overlay the child would refuse")
    message = str(refused.value)
    assert "'orders'" in message
    _, marker, listing = message.partition("Valid collections: ")
    assert marker, message
    assert "orders" not in listing
    assert "catalog" in listing


@pytest.mark.integration
def test_served_accepts_an_overlay_the_ambient_document_supports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: an overlay valid against the document the child
    will load must not be refused by the parent, and the child is asked what
    it hydrated, so the two are pinned to the same document.

    HONESTLY, this half is a regression guard rather than a discriminating
    test, and the reason is worth writing down: the fully discriminating case
    needs an ambient document carrying a collection the profile's own seed
    lacks, and no such document exists -- every vendor's seed schema is closed
    (an unknown top-level key is "Extra inputs are not permitted"), so an
    alternate scenario can only ever be a subset. What this does pin is that
    widening the parent's view did not break the working call, and that the
    child hydrated the ambient scenario (no orders) rather than the profile's.
    """
    monkeypatch.setenv("VENDORFAKE_SEED", str(_house_scenario(tmp_path)))

    with served("square", seed_overlay=OVERLAY) as child:
        entities = child.client.get("/__unit/state").json()["entities"]
        reported = child.client.get("/__unit/info").json()["seed_overlay"]
        terminology = child.client.get(
            f"/v2/loyalty/programs/{child.seed.loyalty_program_id}", headers=child.seed.auth
        ).json()["program"]["terminology"]
    assert entities.get("orders", 0) == 0
    assert terminology["one"] == "Overlaid Point"
    assert reported == {"active": True, "digest": seed_overlay_digest(OVERLAY)}


# ---------------------------------------------------------------------------
# A profile with no seed document at all.
# ---------------------------------------------------------------------------


def test_an_empty_overlay_on_a_seedless_profile_changes_nothing(tmp_path: Path) -> None:
    """``None`` stays ``None``: an overlay that names nothing must not turn a
    legal seedless profile into one carrying an empty document.

    Toast's hydrator takes ``None`` as "load nothing, legal" and rejects
    ``{}`` as a document missing its required collections, so this starts
    either way only if the merge left the seed absent -- which is the whole
    of the claim.
    """
    seedless = tmp_path / "seedless.json"
    seedless.write_text(json.dumps({"name": "seedless", "capabilities": ["auth"]}), encoding="utf-8")

    with unit("toast", str(seedless)) as plain, unit("toast", str(seedless), seed_overlay={}) as overlaid:
        assert (
            overlaid.client.get("/__unit/state").json()["entities"]
            == (plain.client.get("/__unit/state").json()["entities"])
        )
        assert overlaid.client.get("/__unit/info").json()["seed_overlay"]["active"] is True
