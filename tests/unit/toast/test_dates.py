"""The two documented date spellings, the query parser, and the business date."""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.toast.model.dates import business_date, parse_business_date, parse_rest_date, rest_date, webhook_date

NOON_15_JAN_2025_UTC_MS = 1736951400000  # 2025-01-15T14:30:00.000Z


def test_the_rest_spelling_is_the_documented_one() -> None:
    """2025-01-15T14:30:00.000+0000 (apiUnderstandingGuids...)."""
    assert rest_date(NOON_15_JAN_2025_UTC_MS) == "2025-01-15T14:30:00.000+0000"
    assert rest_date(NOON_15_JAN_2025_UTC_MS + 7) == "2025-01-15T14:30:00.007+0000"


def test_the_webhook_spelling_is_the_documented_one() -> None:
    """2024-03-28T15:11:01.050Z (apiMessageDataSchema.html)."""
    assert webhook_date(1711638661050) == "2024-03-28T15:11:01.050Z"


@pytest.mark.parametrize(
    "text",
    [
        "2025-01-15T14:30:00.000+0000",
        "2025-01-15T14:30:00.000+00:00",
        "2025-01-15T14:30:00.000Z",
        "2025-01-15T18:30:00.000+0400",
        "2025-01-15T14:30:00Z",
    ],
)
def test_every_documented_query_spelling_parses_to_the_same_instant(text: str) -> None:
    assert parse_rest_date(text, field="startDate") == NOON_15_JAN_2025_UTC_MS


def test_a_parsed_instant_round_trips_through_the_rest_spelling() -> None:
    assert parse_rest_date(rest_date(NOON_15_JAN_2025_UTC_MS + 123), field="x") == NOON_15_JAN_2025_UTC_MS + 123


@pytest.mark.parametrize(
    "text",
    [
        "2025-02-30T14:30:00.000Z",  # February 30th: the shape passes, the calendar refuses
        "2025-13-01T14:30:00.000Z",  # month 13
        "2025-01-15T25:99:99.000Z",  # an impossible time
        "2025-01-15T14:30:00.000+9999",  # an out-of-range offset
    ],
)
def test_an_impossible_instant_is_the_same_400_not_a_500(text: str) -> None:
    """The regex checks the shape only; these pass it and must still be the
    documented 400 naming the field, never an escaped ValueError
    (vendorfake#30 gate, finding 2)."""
    with pytest.raises(UnitError) as caught:
        parse_rest_date(text, field="startDate")
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "startDate"
    assert "out of range" in str(caught.value)


@pytest.mark.parametrize("text", ["", "2025-01-15", "yesterday", "2025-01-15T14:30:00", "1736944200000"])
def test_a_wrong_spelling_is_a_400_naming_the_field(text: str) -> None:
    with pytest.raises(UnitError) as caught:
        parse_rest_date(text, field="endDate")
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "endDate"


def test_business_date_follows_the_local_day_after_the_closeout_hour() -> None:
    """JUDGMENT arithmetic: 02:00 local on the 16th with closeout at 4 is
    still the 15th's business day; 05:00 is the 16th's."""
    two_am_local_16th = parse_rest_date("2025-01-16T07:00:00.000Z", field="x")  # 02:00 New York (EST)
    five_am_local_16th = parse_rest_date("2025-01-16T10:00:00.000Z", field="x")
    assert business_date(two_am_local_16th, time_zone="America/New_York", closeout_hour=4) == 20250115
    assert business_date(five_am_local_16th, time_zone="America/New_York", closeout_hour=4) == 20250116
    assert business_date(two_am_local_16th, time_zone="America/New_York", closeout_hour=0) == 20250116


def test_an_unknown_zone_falls_back_to_utc_rather_than_raising() -> None:
    assert business_date(NOON_15_JAN_2025_UTC_MS, time_zone="Mars/Olympus", closeout_hour=0) == 20250115


def test_business_date_query_values_are_validated_calendar_dates() -> None:
    assert parse_business_date("20250115", field="businessDate") == 20250115
    for junk in ("2025-01-15", "20251345", "abc", "2025011"):
        with pytest.raises(UnitError) as caught:
            parse_business_date(junk, field="businessDate")
        assert caught.value.field == "businessDate"
