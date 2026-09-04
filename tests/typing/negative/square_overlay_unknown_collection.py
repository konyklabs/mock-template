"""A module that MUST fail type checking. Nothing here runs, and mypy's
ordinary run does not look at it -- ``pyproject.toml`` excludes this directory.

FOR: proving the per-vendor overlay types are load-bearing rather than merely
present. That ``unit("square", seed_overlay={"catalog": {}})`` is *accepted* is
asserted next door in ``narrowing.py``; that a key which is not one of
Square's seed collections is *rejected* is a negative, and a negative cannot
be asserted by a passing type check. So it is asserted by running mypy on this
file from a test and reading both errors:
``tests/unit/testing/test_seed_typing.py``.

``merchants`` is the key to reach for -- the plural of the collection Square
actually has. It is the exact mistake the type exists to catch, because at run
time a partial document has nothing to be wrong against: the unit refuses it
at start (``core/config/overlay.py``), and this moves the same answer to the
editor.

TWO SHAPES, AND THEY FAIL DIFFERENTLY. That is a fact about overload
resolution, not an accident, and it is written down here so nobody "fixes" the
first one into silence:

- **Annotated, the way a fixture holds one.** ``overlay:
  SquareSeedOverlay = {...}`` is a ``TypedDict`` context and nothing else, so
  an unknown key is reported as exactly that: ``Extra key "merchants"``.
- **At the call site.** ``unit()``'s overloads end with one taking ``vendor:
  str``, for a vendor discovered at run time, whose ``seed_overlay`` is the
  untyped ``Mapping[str, Any]``. A literal ``"square"`` matches that overload
  too, so a bad overlay does not make the call *unresolvable* -- it makes it
  resolve to the fallback, and the seed comes back as the structural ``Seed``
  instead of ``SquareSeed``. Which is still an error the moment anything
  Square-specific is read off it, and is asserted here with ``assert_type``.
  Removing the fallback overload to sharpen this would cost the vendor-as-a-
  variable case, which is a real one; degrading is the trade that was chosen.
"""

from __future__ import annotations

from typing import assert_type

from vendorfake.testing import SquareSeed, SquareSeedOverlay, StartedUnit, unit


def a_square_overlay_rejects_a_collection_square_does_not_have() -> None:
    # The first expected error:
    #   error: Extra key "merchants" for TypedDict "SquareSeedOverlay"
    #   [typeddict-unknown-key]
    overlay: SquareSeedOverlay = {"merchants": {}}
    del overlay


def a_bad_overlay_at_the_call_site_loses_the_vendor_narrowing() -> None:
    with unit("square", seed_overlay={"merchants": {}}) as started:
        # The second expected error:
        #   error: Expression is of type "StartedUnit[Seed]", not
        #   "StartedUnit[SquareSeed]"  [assert-type]
        assert_type(started, StartedUnit[SquareSeed])
