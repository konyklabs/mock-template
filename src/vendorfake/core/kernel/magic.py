"""In-band fault triggering: magic values in ordinary request fields.

A vendor declares which ordinary fields are scanned for a magic prefix; a value of ``chaos:rate_limit`` or
``chaos:timeout:delay_ms=250`` in one of them arms that fault for this request only, reaching a consumer that can
set a reference id but cannot add a header. INVARIANT: **extraction is pure** -- it decides, arms, counts and logs
nothing, and is called only from ``chaos/selector.py`` after the ``chaos`` capability gate has passed, so a
per-request trigger cannot become a second arming path. Body paths are read through the content-type-general
``HandlerArgs.body()`` rather than a JSON-only reader, so a vendor's declared paths are reachable on a
form-encoded request; ``provenance: judgment``. Nothing here may name a vendor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from vendorfake.core.kernel.types import MagicTriggerSpec, UnitRequest
from vendorfake.core.util.paths import dot_get

__all__ = ["NO_MAGIC", "MagicExtraction", "extract_magic"]


@dataclass(frozen=True, slots=True)
class MagicExtraction:
    """Every magic value found, in the vendor's declared order; only
    ``faults[0]`` is armed, one fault per request."""

    faults: tuple[str, ...] = ()
    params: Mapping[str, str] = field(default_factory=dict)

    @property
    def armed(self) -> bool:
        return bool(self.faults)


NO_MAGIC = MagicExtraction()
"""Shared immutable result: no spec, or no magic value in the request."""


def extract_magic(spec: MagicTriggerSpec | None, req: UnitRequest, parsed_body: object) -> MagicExtraction:
    """Scan the vendor-declared fields for the vendor-declared prefix. Candidate order is contract -- body paths, query
    parameters, headers, a later candidate's parameters overwriting an earlier one's under the same key -- and only
    ``str`` values are candidates."""
    if spec is None:
        return NO_MAGIC

    candidates: list[str] = []
    for path in spec.body_paths:
        value = dot_get(parsed_body, path)
        if isinstance(value, str):
            candidates.append(value)
    for name in spec.query_params:
        from_query = req.query.get(name)
        if isinstance(from_query, str):
            candidates.append(from_query)
    for name in spec.headers:
        from_header = req.headers.get(name.lower())
        if isinstance(from_header, str):
            candidates.append(from_header)

    faults: list[str] = []
    params: dict[str, str] = {}
    for raw in candidates:
        if not raw.startswith(spec.prefix):
            continue
        fault, *rest = raw[len(spec.prefix) :].split(":")
        if not fault:
            # ``chaos:`` alone names no fault; skipped, not rejected.
            continue
        faults.append(fault)
        for pair in rest:
            separator = pair.find("=")
            # ``> 0``: a leading ``=`` names no key, so it carries no parameter.
            if separator > 0:
                params[pair[:separator]] = pair[separator + 1 :]

    if not faults:
        return NO_MAGIC
    return MagicExtraction(faults=tuple(faults), params=params)
