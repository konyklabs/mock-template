"""The wheel-data verdict, on name lists a test can invent.

Deep-lens D9 (konyklabs/roadmap#56): FORBIDDEN was only ever checked against a
wheel that lacked the file by construction, so nothing proved the guard bites.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_wheel_data", Path(__file__).resolve().parents[2] / "tools" / "check_wheel_data.py"
)
assert _SPEC is not None and _SPEC.loader is not None
check_wheel_data = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_wheel_data)


def test_a_forbidden_file_in_the_wheel_fails_even_when_everything_required_is_there() -> None:
    names = set(check_wheel_data.REQUIRED) | set(check_wheel_data.FORBIDDEN)
    assert check_wheel_data.verify(names, "w.whl") == 1


def test_the_non_vendored_extract_is_forbidden_by_name() -> None:
    assert "vendorfake/toast/fidelity/extract.json" in check_wheel_data.FORBIDDEN
    assert "vendorfake/toast/fidelity/pin.json" in check_wheel_data.REQUIRED
    assert "vendorfake/square/fidelity/extract.json" in check_wheel_data.REQUIRED


def test_a_missing_required_file_fails_and_a_complete_wheel_passes() -> None:
    assert check_wheel_data.verify(set(check_wheel_data.REQUIRED), "w.whl") == 0
    assert check_wheel_data.verify(set(check_wheel_data.REQUIRED) - {check_wheel_data.REQUIRED[0]}, "w.whl") == 1
