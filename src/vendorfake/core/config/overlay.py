"""Laying a partial seed document over the profile's own.

FOR: the one description of what ``seed_overlay=`` /
``VENDORFAKE_SEED_OVERLAY`` mean -- how a partial document is merged onto the
profile's seed, which overlays are refused, and how the result is
fingerprinted for ``GET /__unit/info``.

INVARIANT: **the merge rule is stated once and lives here.** Three rules, and
nothing else:

1. **Objects merge, recursively.** A key present in both, whose value is an
   object on both sides, merges key by key; the overlay's keys win.
2. **``null`` deletes.** A key whose overlay value is ``null`` is removed from
   the result rather than set to ``None``. This is the only way an overlay can
   take something away, and it follows the store's own rule that *absent means
   absent* (``core/util/json.py``): a seed carrying ``"reference_id": null``
   and a seed carrying no ``reference_id`` are different documents, and an
   overlay that meant "remove it" must produce the second.
3. **Arrays replace, and so does every scalar.** An overlay array replaces the
   base array whole -- it never concatenates, never merges by index and never
   merges by id. A seed's ``orders`` is a list a reader can see; an overlay
   that lists two orders means two, exactly as
   :func:`~vendorfake.core.config.profile.merge_documents` already treats a
   profile's subscriber list. Merging by index would silently pair unrelated
   entities, and merging by id would need a per-vendor notion of what an id is
   -- which is precisely the vendor knowledge the core may not have.

SECOND INVARIANT: **an overlay may not invent a collection.** Its top-level
keys are checked against the base document's before anything is merged, and an
unknown one is a refusal at *start* time naming the offending key and the
collections that do exist. The alternative is the failure this rule exists to
prevent: ``{"order": ...}`` for a vendor whose collection is ``orders`` merges
cleanly, hydrates nothing, and presents an hour later as "the fake ignored my
scenario". A typo in a partial document has no other symptom, because there is
nothing in a partial document to be wrong *against*.

The contents of an overlay are never published. ``GET /__unit/info`` reports
whether one is active and the digest of its canonical JSON, which is enough to
tell two runs apart and to pin one in a report, and carries no value from a
document a consumer may have filled with credentials of their own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.util.json import canonical_json, sha256_hex

__all__ = [
    "DIGEST_PREFIX",
    "apply_seed_overlay",
    "merge_seed",
    "overlay_collections",
    "seed_overlay_digest",
    "unknown_collections",
]

DIGEST_PREFIX = "sha256:"
"""What :func:`seed_overlay_digest` prefixes its hex with.

Spelled on the wire (``"sha256:<hex>"``) rather than left bare so that the
algorithm is part of the published value: a consumer pinning a digest in a
report, or comparing two runs, can tell a changed *algorithm* from a changed
overlay instead of discovering it as a mismatch with no explanation.
"""


def merge_seed(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """``overlay`` laid over ``base``, by the three rules in the module docstring.

    Pure: neither argument is mutated and nothing is read from disk. The
    result is a new ``dict`` at every level the merge touched; a subtree the
    overlay never mentioned is carried over by reference, which is safe
    because every caller here treats a seed document as read-only after it is
    loaded.

    Deliberately NOT :func:`~vendorfake.core.config.profile._deep_merge`,
    which is the *profile* document's merge and has no notion of deletion: a
    profile layer never removes a key, because the layer beneath it is a set
    of defaults rather than a scenario. A seed overlay does remove keys --
    "this scenario, but with no orders yet" is a thing a consumer needs to say
    and cannot say by assigning a value.

    Deletion is a merge rule and not a promise that the result loads. What
    comes out is still a whole seed document and the vendor still hydrates it,
    so removing a collection that another one references is refused there,
    with hydration's own message: a reader following this rule has to remove
    what pointed at it too. Review caught the earlier wording here recommending
    exactly such a deletion.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if value is None:
            merged.pop(key, None)
            continue
        previous = merged.get(key)
        if isinstance(previous, Mapping) and isinstance(value, Mapping):
            merged[key] = merge_seed(previous, value)
        else:
            merged[key] = value
    return merged


def overlay_collections(base: Mapping[str, Any]) -> tuple[str, ...]:
    """The collection names an overlay may name, sorted.

    A key beginning with an underscore is a document's own annotation --
    ``_comment`` in every seed this distribution ships -- and is left out of
    the *listing* while still being accepted by :func:`unknown_collections`:
    it is a real key of the document, so an overlay that names it is not a
    typo, but offering it to a reader looking for a collection to override
    would be.
    """
    return tuple(sorted(key for key in base if not key.startswith("_")))


def unknown_collections(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> tuple[str, ...]:
    """The overlay's top-level keys that ``base`` does not have, sorted."""
    return tuple(sorted(key for key in overlay if key not in base))


def seed_overlay_digest(overlay: Mapping[str, Any]) -> str:
    """``"sha256:<hex>"`` over the overlay's canonical JSON.

    Canonical -- keys sorted at every depth, no whitespace -- so that two
    callers who wrote the same overlay with their keys in a different order
    get the same digest, which is the only way the value is comparable
    between two runs at all.
    """
    return DIGEST_PREFIX + sha256_hex(canonical_json(overlay))


def apply_seed_overlay(
    base: object | None,
    overlay: Mapping[str, Any],
    *,
    profile: str,
) -> dict[str, Any]:
    """Check ``overlay`` against ``base``, then merge it. Start-time or never.

    ``base`` is ``None`` for a profile that names no seed document at all, in
    which case *every* overlay key is unknown and the refusal says so rather
    than reporting an empty list of valid collections, which would read as a
    vendor with no collections rather than a profile with no seed.

    ``base`` is typed ``object`` because a seed document is whatever JSON the
    profile pointed at decoded to (``LoadedProfile.seed``), and a document
    that is not an object has no collections for an overlay to name at all --
    refused here rather than merged into something that would hydrate
    differently for reasons no message explained.

    The refusal is a ``UnitError`` of kind ``invalid_value`` on the
    ``seed_overlay`` field, so it reaches a CLI, a served child's startup and
    an in-process ``unit()`` call by the same path every other malformed
    configuration value does. Its message carries the phrase ``Valid
    collections:`` followed by the listing, which the conformance clause
    parses -- see ``conformance/checks/state.py``.
    """
    if base is not None and not isinstance(base, Mapping):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"a seed overlay was given, but the seed document for profile {profile!r} decoded to "
                f"{type(base).__name__}, not a JSON object, so it has no top-level collections to override. "
                f"Valid collections: (none)."
            ),
            field="seed_overlay",
            info={"unknown": sorted(overlay), "available": []},
        )
    document: Mapping[str, Any] = {} if base is None else dict(base)
    unknown = unknown_collections(document, overlay)
    if unknown:
        offending = ", ".join(repr(name) for name in unknown)
        if base is None:
            detail = (
                f"seed overlay names {offending}, but profile {profile!r} loads no seed document at all, "
                f"so there is no collection to override. Point the profile at a seed document (its `seed` "
                f"key), or drop the overlay. Valid collections: (none)."
            )
        else:
            detail = (
                f"seed overlay names {offending}, which the seed document for profile {profile!r} does not "
                f"have. An overlay may only override a collection the seed already carries -- a name that is "
                f"merely close to one would merge cleanly, hydrate nothing, and look like the fake ignoring "
                f"the scenario. Fix the key, or add it to the seed document itself. "
                f"Valid collections: {', '.join(overlay_collections(document))}."
            )
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=detail,
            field="seed_overlay",
            info={"unknown": list(unknown), "available": list(overlay_collections(document))},
        )
    return merge_seed(document, overlay)
