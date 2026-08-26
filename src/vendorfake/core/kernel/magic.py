"""In-band fault triggering: magic values in ordinary request fields.

FOR: a consumer who drives the unit through a vendor's own SDK. That consumer
often cannot add a header and cannot reach a control API -- the SDK owns the
transport -- but can always set a reference id. A vendor declares which
ordinary fields are scanned for a magic prefix; a value of ``chaos:rate_limit``
or ``chaos:timeout:delay_ms=250`` in one of them arms that fault for this
request only.

Prior art, and the reason the mechanism is shaped this way rather than
invented here: the reference vendor's sandbox drives card declines from magic
values written into ordinary payment fields, not from a control channel. The
documentation URL for that behaviour lives with the vendor's own
``MagicTriggerSpec`` declaration, which is where a vendor-specific citation
belongs -- nothing in this file may name a vendor, and ``tools/boundary_check``
fails the build over a string constant that does.

INVARIANT: **extraction is PURE.** This module reads; it decides nothing, arms
nothing, counts nothing and logs nothing. It is called from exactly one place
-- ``chaos/selector.py``, and only *after* the ``chaos`` capability gate has
passed -- so that a per-request trigger cannot become a second arming path.
That is not a hypothetical: the losing bake-off entry parsed a per-request
chaos header and merged it over the global config in its dispatcher, with no
capability check anywhere in the path, and it also mutated global counters.
Ported from ``packages/core/src/kernel/magic.ts``; only the call site changed.

Two departures from the reference, both recorded:

*The body is the general body.* The reference feeds extraction from
``safeJson(args)`` -- JSON only -- so a vendor's ``body_paths`` are unreachable
on a form-encoded request. This core has one ``HandlerArgs.body()`` that
answers for both content types, and keeping a second JSON-only reader here
would re-create exactly the drift that unification removes. Nothing observable
changes for the shipped vendor, whose magic paths are not OAuth fields.
``provenance: judgment``.

*The extraction is returned, not written into a per-request scratch.* The
reference sets ``scope.magicFaults`` and ``scope.magicParams`` on a
``RequestScope`` and then never reads either -- they are write-only in the
whole tree. Returning a value keeps the module pure and removes the scratch
object; the selector publishes the same two fields on its result for anything
that later wants them, and publishes them EMPTY when the capability gate is
shut, so a disabled unit does not hand a vendor a fault name it must remember
to ignore.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from vendorfake.core.kernel.types import MagicTriggerSpec, UnitRequest
from vendorfake.core.util.paths import dot_get

__all__ = ["NO_MAGIC", "MagicExtraction", "extract_magic"]


@dataclass(frozen=True, slots=True)
class MagicExtraction:
    """What the request asked for, in the order the vendor declared to look.

    ``faults`` keeps every magic value found, not just the first, because a
    body carrying two of them is a consumer mistake worth reporting rather than
    silently half-honouring. Only ``faults[0]`` is armed -- one fault per
    request, exactly as one rule fires per subject.
    """

    faults: tuple[str, ...] = ()
    params: Mapping[str, str] = field(default_factory=dict)

    @property
    def armed(self) -> bool:
        return bool(self.faults)


NO_MAGIC = MagicExtraction()
"""The result for a vendor that declares no magic spec, and for every request
that carries no magic value. A shared immutable, so the common path allocates
nothing."""


def extract_magic(spec: MagicTriggerSpec | None, req: UnitRequest, parsed_body: object) -> MagicExtraction:
    """Scan the vendor-declared fields for the vendor-declared prefix.

    Candidate order is body paths, then query parameters, then headers, and it
    is contract: a later candidate's parameters overwrite an earlier
    candidate's under the same key, so "the header wins over the body" is a
    statement a test can make. Only ``str`` values are candidates -- a numeric
    ``reference_id`` is not a magic value in any vendor's vocabulary, and
    stringifying it would make ``42`` a candidate for a prefix nobody typed.
    """
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
            # ``chaos:`` alone names no fault. Skipped rather than rejected:
            # this is a value in a field the vendor uses for its own purposes,
            # and a 400 here would make an ordinary reference id a hazard.
            continue
        faults.append(fault)
        for pair in rest:
            separator = pair.find("=")
            # ``> 0`` and not ``>= 0``, ported literally: a leading ``=`` names
            # no key, so ``chaos:timeout:=250`` carries no parameter rather
            # than one called the empty string.
            if separator > 0:
                params[pair[:separator]] = pair[separator + 1 :]

    if not faults:
        return NO_MAGIC
    return MagicExtraction(faults=tuple(faults), params=params)
