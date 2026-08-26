"""The four JavaScript numeric behaviours this port has to reproduce."""

from __future__ import annotations

import math

import pytest

from vendorfake.core.util.numbers import as_float, as_int, as_str, js_number, js_parse_float, js_round


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2.5, 3),  # Python's round() gives 2
        (3.5, 4),
        (-2.5, -2),  # Math.round rounds half UP, not away from zero
        (-3.5, -3),
        (2.4, 2),
        (-2.6, -3),
        (0.0, 0),
        (-0.5, 0),
    ],
)
def test_js_round_agrees_with_math_round_including_the_negative_halves(value: float, expected: int) -> None:
    assert js_round(value) == expected


def test_round_and_js_round_genuinely_disagree() -> None:
    """Stated so the test is not mistaken for a tautology: this is the case
    that silently moves money."""
    assert round(2.5) == 2
    assert js_round(2.5) == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2", 2.0),
        ("2 pieces", 2.0),
        ("  3.5kg", 3.5),
        (".5", 0.5),
        ("2.", 2.0),
        ("-1.5e2", -150.0),
        ("1e", 1.0),
        ("+7", 7.0),
        ("Infinity", math.inf),
        ("", None),
        ("pieces", None),
        ("e5", None),
        ("NaN", None),
    ],
)
def test_js_parse_float_scans_the_longest_numeric_prefix(text: str, expected: float | None) -> None:
    assert js_parse_float(text) == expected


def test_float_would_have_raised_where_js_parse_float_answers() -> None:
    with pytest.raises(ValueError):
        float("2 pieces")
    assert js_parse_float("2 pieces") == 2.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3, 3.0),
        (3.5, 3.5),
        ("3", 3.0),
        ("3.5", 3.5),
        ("", 0.0),  # Number("") === 0
        ("   ", 0.0),
        (True, 1.0),
        (False, 0.0),
        (None, 99.0),
        ("nonsense", 99.0),
        (float("nan"), 99.0),
        (float("inf"), 99.0),
        ([1], 99.0),
    ],
)
def test_as_float_coerces_or_falls_back(value: object, expected: float) -> None:
    assert as_float(value, 99.0) == expected


def test_as_int_truncates_toward_zero() -> None:
    assert as_int("3.9", 1) == 3
    assert as_int(-3.9, 1) == -3
    assert as_int(None, 1) == 1
    assert as_int("junk", 1) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3, "3"),
        (3.0, "3"),  # str(3.0) is "3.0"; String(3) is "3"
        (3.5, "3.5"),
        ("already", "already"),
        (True, "true"),
        (False, "false"),
        (None, "fallback"),
        ({"a": 1}, "fallback"),
    ],
)
def test_as_str_renders_a_number_the_way_javascript_prints_it(value: object, expected: str) -> None:
    assert as_str(value, "fallback") == expected


def test_the_retry_after_case_that_motivated_as_str() -> None:
    """The reference asserts ``retry-after === '3'``. A float parameter through
    ``str()`` would put ``3.0`` on the wire."""
    assert as_str(float(as_int("3", 1)), "1") == "3"


def test_js_number_trims_an_integral_float_and_leaves_the_rest() -> None:
    assert js_number(100.0) == 100
    assert isinstance(js_number(100.0), int)
    assert js_number(100.5) == 100.5
    assert math.isnan(float(js_number(float("nan"))))
