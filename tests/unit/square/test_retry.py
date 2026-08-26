"""The documented retry schedule, and the de-vendoring it completes."""

from __future__ import annotations

import ast
from pathlib import Path

from vendorfake.core.webhooks.models import DeliveryOutcome
from vendorfake.square import retry


def test_the_schedule_matches_squares_published_table_row_for_row() -> None:
    """1, 2, 4, 8, 16, 32, 60 minutes then 2, 4, 8, 8 hours -- the "time since
    last attempt" column, eleven retries over twenty-four hours."""
    minute = 60_000
    hour = 60 * minute
    assert (
        1 * minute,
        2 * minute,
        4 * minute,
        8 * minute,
        16 * minute,
        32 * minute,
        60 * minute,
        2 * hour,
        4 * hour,
        8 * hour,
        8 * hour,
    ) == retry.SQUARE_RETRY_SCHEDULE_MS
    assert len(retry.SQUARE_RETRY_SCHEDULE_MS) == 11
    # Square's own cumulative column reads "2 hours" for row 7, where 63 + 60
    # minutes is 2h03m, and "24 hours" at the end. Following the per-attempt
    # column -- which is the correct reading -- therefore totals three minutes
    # more than the prose says. Pinned so nobody "corrects" the table against
    # the cumulative column.
    assert sum(retry.SQUARE_RETRY_SCHEDULE_MS) == 24 * hour + 3 * minute


def test_the_acknowledgement_window_is_the_documented_ten_seconds() -> None:
    assert retry.SQUARE_TIMEOUT_MS == 10_000


def test_the_time_scale_turns_the_first_retry_into_ten_milliseconds() -> None:
    """A mock affordance, derived from the schedule rather than chosen: every
    interval keeps its ratio to every other, so the shape of the backoff is
    what a test observes."""
    assert round(retry.SQUARE_RETRY_SCHEDULE_MS[0] * retry.SQUARE_TIME_SCALE) == 10


def test_every_neutral_outcome_has_a_square_word_for_it() -> None:
    """The core computes a neutral outcome and this vendor names it. A missing
    row would send a delivery with no retry reason and nothing would say so."""
    assert set(retry.RETRY_REASONS) == set(DeliveryOutcome)
    assert retry.RETRY_REASONS[DeliveryOutcome.TIMEOUT] == "http_timeout"
    assert retry.RETRY_REASONS[DeliveryOutcome.TRANSPORT_ERROR] == "other_error"
    assert retry.RETRY_REASONS[DeliveryOutcome.HTTP_ERROR] == "http_error"


def test_the_retry_vocabulary_appears_in_exactly_one_module() -> None:
    """`http_timeout` and `other_error` carry no brand name, so the boundary
    checker's vendor-slug rule cannot see them. This is the check that keeps
    one vendor's retry vocabulary out of every other vendor's delivery path."""
    wanted = {"http_timeout", "other_error", "square-retry-number", "square-retry-reason"}
    home = Path("src/vendorfake/square/retry.py").resolve()
    for path in sorted(Path("src/vendorfake").rglob("*.py")):
        if path.resolve() == home:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in wanted
        }
        assert not literals, f"{path} spells {sorted(literals)}; retry.py is the only home for those"


def test_the_defaults_are_a_fresh_document_each_time() -> None:
    """Read at unit construction; a shared mutable default would couple two
    units in one process."""
    first, second = retry.square_retry_defaults(), retry.square_retry_defaults()
    assert first is not second
    assert first == second
    assert first.webhooks.retry.schedule_ms == retry.SQUARE_RETRY_SCHEDULE_MS
