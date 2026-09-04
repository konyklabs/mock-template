"""Laying a partial seed document (``seed_overlay=`` / ``VENDORFAKE_SEED_OVERLAY``)
over the profile's own. INVARIANT: objects merge recursively with the
overlay's keys winning; a ``null`` value deletes the key; an array or scalar
replaces the base whole; and an overlay may not invent a collection -- a typo
in its top-level keys is a start-time refusal. Contents are never published:
``GET /__unit/info`` reports only whether one is active and its digest.
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
"""Prefix for :func:`seed_overlay_digest`'s hex."""


def merge_seed(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """``overlay`` laid over ``base``, by the rules in the module docstring.
    Pure; not :func:`~vendorfake.core.config.profile._deep_merge`, which has
    no notion of deletion."""
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
    """The collection names an overlay may name, sorted; a key starting with
    ``_`` is left out of the listing but still accepted."""
    return tuple(sorted(key for key in base if not key.startswith("_")))


def unknown_collections(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> tuple[str, ...]:
    """The overlay's top-level keys that ``base`` does not have, sorted."""
    return tuple(sorted(key for key in overlay if key not in base))


def seed_overlay_digest(overlay: Mapping[str, Any]) -> str:
    """``"sha256:<hex>"`` over the overlay's canonical JSON, key order independent."""
    return DIGEST_PREFIX + sha256_hex(canonical_json(overlay))


def apply_seed_overlay(
    base: object | None,
    overlay: Mapping[str, Any],
    *,
    profile: str,
) -> dict[str, Any] | None:
    """Check ``overlay`` against ``base``, then merge it. Start-time or never.
    ``None`` comes back only for no-seed-and-empty-overlay, since a hydrator
    treats ``None``, not ``{}``, as "load nothing, legal". The refusal names
    ``Valid collections:``, parsed by ``conformance/checks/state.py``.
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
    if base is None and not overlay:
        return None
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
