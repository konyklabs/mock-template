"""The logger's threshold, its bytes, and where its level does not come from."""

from __future__ import annotations

import io
import json

from vendorfake.core.logging import LEVELS, JsonLogger, SilentLogger, level_index


def _capture(level: str = "info") -> tuple[JsonLogger, io.StringIO]:
    stream = io.StringIO()
    return JsonLogger(level, stream=stream, now=lambda: "2024-01-01T00:00:00.000Z"), stream


def test_a_line_is_one_compact_json_object_with_the_fields_flattened() -> None:
    log, stream = _capture()
    log.info("unit started", {"vendor": "acme", "n": 1})
    assert (
        stream.getvalue()
        == '{"t":"2024-01-01T00:00:00.000Z","level":"info","msg":"unit started","vendor":"acme","n":1}\n'
    )


def test_non_ascii_text_stays_utf8_rather_than_being_escaped() -> None:
    log, stream = _capture()
    log.error("failed", {"name": "café"})
    assert "café" in stream.getvalue()
    assert "\\u00e9" not in stream.getvalue()


def test_the_threshold_drops_quieter_levels_and_keeps_louder_ones() -> None:
    log, stream = _capture("warn")
    log.debug("d")
    log.info("i")
    log.warn("w")
    log.error("e")
    levels = [json.loads(line)["level"] for line in stream.getvalue().splitlines()]
    assert levels == ["warn", "error"]


def test_an_unknown_level_makes_the_unit_more_talkative_not_less() -> None:
    """Ported from ``Math.max(0, order.indexOf(level))``. A misconfigured
    threshold that silently swallowed errors would be indistinguishable from a
    unit with nothing to say."""
    assert level_index("shouty") == 0
    log, stream = _capture("shouty")
    log.debug("d")
    assert '"level":"debug"' in stream.getvalue()


def test_the_levels_are_ordered_least_to_most_severe() -> None:
    assert LEVELS == ("debug", "info", "warn", "error")
    assert level_index("debug") < level_index("info") < level_index("warn") < level_index("error")


def test_no_fields_means_no_extra_keys() -> None:
    log, stream = _capture()
    log.info("bare")
    assert json.loads(stream.getvalue()) == {"t": "2024-01-01T00:00:00.000Z", "level": "info", "msg": "bare"}


def test_the_level_is_never_read_from_the_environment(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The reference defaulted to ``process.env.UNIT_LOG_LEVEL``, which made
    every unit in a process depend on ambient state no caller passed."""
    monkeypatch.setenv("VENDORFAKE_LOG_LEVEL", "debug")
    monkeypatch.setenv("UNIT_LOG_LEVEL", "debug")
    stream = io.StringIO()
    JsonLogger(stream=stream).debug("should not appear")
    assert stream.getvalue() == ""


def test_two_loggers_in_one_process_keep_two_thresholds() -> None:
    quiet, quiet_out = _capture("error")
    loud, loud_out = _capture("debug")
    quiet.info("x")
    loud.info("x")
    assert quiet_out.getvalue() == ""
    assert loud_out.getvalue() != ""


def test_the_timestamp_is_rfc3339_with_milliseconds_and_a_z() -> None:
    stream = io.StringIO()
    JsonLogger("debug", stream=stream).info("x")
    stamp = json.loads(stream.getvalue())["t"]
    assert stamp.endswith("Z")
    assert len(stamp) == len("2024-01-01T00:00:00.000Z")


def test_the_silent_logger_answers_every_level_and_writes_nothing(capsys) -> None:  # type: ignore[no-untyped-def]
    log = SilentLogger()
    log.debug("a")
    log.info("b")
    log.warn("c")
    log.error("d", {"x": 1})
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
