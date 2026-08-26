"""Check registration, and the committed record of what is registered.

FOR: making the set of contracts one list, in one place, with one id space --
so that the standalone runner, the pytest plugin and the report are three
renderings of the same registry rather than three lists kept in step by hand.

INVARIANT: **removing a contract is a reviewable diff.** ``manifest.json``
holds the id-to-name map and the expected-skip matrix as committed data, and a
test asserts the live registry equals it. A check deleted, renamed or quietly
gated out of every profile therefore shows up in a pull request as a changed
file, which is the one property the template-checksum idea from the reference
was reaching for and the only part of it that survives a single-repo topology.

WHAT A CHECK MAY KNOW. Nothing about any vendor. A check declares what it
needs through :class:`~vendorfake.conformance.types.Requires` and DISCOVERS it
at runtime from ``/__unit/routes``, ``/__unit/capabilities`` and
``/__unit/info``. There is no hardcoded path, no hardcoded capability name and
no vendor slug anywhere in this package -- ``tools/boundary_check.py`` fails
the build if one appears, with the vendor list derived from the source tree
rather than hand-written.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from importlib import resources

from vendorfake.conformance.types import CheckFn, CheckSpec, Requires

__all__ = [
    "CHECKS",
    "check",
    "expected_skips",
    "find_check",
    "load_manifest",
    "manifest_of_registry",
]

CHECKS: list[CheckSpec] = []
"""Every registered contract, in registration order -- which is id order,
because the check modules are imported in id order by
:mod:`vendorfake.conformance.checks`."""

NO_PRECONDITION = Requires()
"""The default gate: a contract every unit must answer, on every profile."""

_MANIFEST_FILE = "manifest.json"


def check(*, id: str, name: str, asserts: str, requires: Requires = NO_PRECONDITION) -> Callable[[CheckFn], CheckFn]:
    """Register one contract.

    ``id`` shadows the builtin deliberately: it is the wire name of the thing,
    it appears in reports, test ids and the manifest, and calling the parameter
    anything else here would make every call site read differently from every
    place the value is seen.
    """

    def decorate(fn: CheckFn) -> CheckFn:
        for existing in CHECKS:
            if existing.id == id:
                raise RuntimeError(
                    f"duplicate conformance check id {id!r}: already registered as {existing.name!r}. "
                    f"Ids are permanent -- give the new check the next free number."
                )
        CHECKS.append(CheckSpec(id=id, name=name, asserts=asserts, requires=requires, fn=fn))
        return fn

    return decorate


def find_check(check_id: str) -> CheckSpec:
    """One check by id, or a ``KeyError`` listing the ids that exist."""
    for spec in CHECKS:
        if spec.id == check_id:
            return spec
    raise KeyError(f"no conformance check {check_id!r}. Registered: {', '.join(spec.id for spec in CHECKS)}")


def _manifest_document() -> Mapping[str, object]:
    raw = resources.files(__package__).joinpath(_MANIFEST_FILE).read_text(encoding="utf-8")
    document: Mapping[str, object] = json.loads(raw)
    return document


def load_manifest() -> dict[str, str]:
    """The committed id-to-name map."""
    checks = _manifest_document()["checks"]
    if not isinstance(checks, dict):
        raise RuntimeError(f"{_MANIFEST_FILE}: 'checks' must be an object mapping check id to name")
    return {str(key): str(value) for key, value in checks.items()}


def expected_skips() -> dict[str, frozenset[str]]:
    """Skips that are expected and permanent, as committed data.

    A profile that genuinely lacks the capability a contract needs will skip it
    forever, and treating that as a strict-mode failure would make ``--strict``
    unusable. Recording the pairs here instead means two things fail loudly: an
    *undeclared* skip, and an expected skip that stops happening -- which means
    a profile changed and nobody said so.
    """
    matrix = _manifest_document()["expected_skips"]
    if not isinstance(matrix, dict):
        raise RuntimeError(f"{_MANIFEST_FILE}: 'expected_skips' must be an object mapping check id to profile names")
    out: dict[str, frozenset[str]] = {}
    for check_id, profiles in matrix.items():
        if not isinstance(profiles, list):
            raise RuntimeError(f"{_MANIFEST_FILE}: expected_skips[{check_id!r}] must be a list of profile names")
        out[str(check_id)] = frozenset(str(name) for name in profiles)
    return out


def manifest_of_registry() -> dict[str, str]:
    """What ``manifest.json``'s ``checks`` block would have to say right now."""
    return {spec.id: spec.name for spec in CHECKS}
