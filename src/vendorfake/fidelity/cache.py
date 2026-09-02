"""The fetch-never-commit path: a non-vendored vendor's extract, cut at run time.

FOR: konyklabs/roadmap#56. A vendor whose terms do not permit a copy of its
specification in a public repository declares ``vendored: false``; its
``pin.json`` still ships (sha256, size, version, fetch date of each upstream
file -- facts, not copies) and its extract is cut here from a fresh fetch
into a local cache directory that is never inside the repository.

INVARIANT: **no upstream byte is written under the package.** The cache lives
under ``$VENDORFAKE_FIDELITY_CACHE``, else ``$XDG_CACHE_HOME/vendorfake/fidelity``,
else ``~/.cache/vendorfake/fidelity``, keyed by the anchor and the pin.

Implemented by piece C of the #56 build; this skeleton fixes the seam.
"""

from __future__ import annotations

from vendorfake.fidelity.types import Extract, FidelityDeclaration

__all__ = ["cached_extract"]


def cached_extract(anchor: str, declaration: FidelityDeclaration) -> Extract:
    """The extract for a non-vendored vendor: from the cache when it matches
    the pin, otherwise fetched, cut, verified against the pin and cached."""
    raise NotImplementedError(f"{anchor}: the fetch-never-commit path is not implemented yet (konyklabs/roadmap#56)")
