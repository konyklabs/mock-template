"""The unit's logger: structured lines on stderr, or silence.

The logger never reads the process environment: the level is passed as an
argument, resolved at unit construction from :attr:`ResolvedConfig.log_level`,
so two units in one process can log at different levels. Log lines share the
wire's encoder (:func:`vendorfake.core.util.json.dump_json`), so a log line
and a response body agree on separators and on non-ASCII text.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, TextIO

from vendorfake.core.util.json import dump_json

__all__ = ["LEVELS", "JsonLogger", "SilentLogger", "level_index"]

LEVELS: Final[tuple[str, ...]] = ("debug", "info", "warn", "error")
"""The four levels, least to most severe."""


def level_index(level: str) -> int:
    """Where ``level`` sits in :data:`LEVELS`; an unknown name maps to
    ``debug`` so a typo makes the unit more talkative, never silently
    quieter.
    """
    try:
        return LEVELS.index(level)
    except ValueError:
        return 0


class JsonLogger:
    """One JSON object per line, on stderr.

    stderr and not stdout because stdout is where a CLI subcommand writes the
    document a caller asked for (an OpenAPI dump, a conformance report), and a
    log line interleaved into that is a corrupt file rather than noise.
    """

    __slots__ = ("_min", "_now", "_stream", "level")

    def __init__(
        self,
        level: str = "info",
        *,
        stream: TextIO | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.level = level
        self._min = level_index(level)
        # Resolved at emit time: pytest replaces ``sys.stderr`` per test, and
        # a stream captured at construction could already be closed.
        self._stream = stream
        self._now = now

    def _timestamp(self) -> str:
        if self._now is not None:
            return self._now()
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _emit(self, level: str, msg: str, fields: Mapping[str, Any] | None) -> None:
        if level_index(level) < self._min:
            return
        line: dict[str, Any] = {"t": self._timestamp(), "level": level, "msg": msg}
        if fields:
            line.update(fields)
        stream = sys.stderr if self._stream is None else self._stream
        stream.write(dump_json(line).decode("utf-8") + "\n")

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        self._emit("debug", msg, fields)

    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        self._emit("info", msg, fields)

    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        self._emit("warn", msg, fields)

    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        self._emit("error", msg, fields)


class SilentLogger:
    """Discards everything.

    Lets a test assert the absence of a log line, and keeps a conformance
    run's speed independent of its own logging.
    """

    __slots__ = ()

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        return None

    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        return None

    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        return None

    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None:
        return None
