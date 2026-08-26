"""The unit's logger: structured lines on stderr, or silence.

FOR: giving the core one place to say what it did, in a form a container's log
collector can parse, without any module in the core deciding *where* the
threshold comes from.

INVARIANT: **the logger never reads the process environment.** The reference
writes ``createLogger(level = process.env.UNIT_LOG_LEVEL ?? 'info')``, which
makes the log level of every unit in a process depend on ambient state that no
caller passed and no report shows. Here the level arrives as an argument, and
the one place it is derived from configuration is unit construction, which
reads :attr:`ResolvedConfig.log_level` -- itself resolved by the profile
loader, which is the single module allowed to look at an environment mapping,
and which is handed ``{}`` unless the CLI passes the real one. Two units in one
test process can therefore log at two different levels, and a test that sets
``VENDORFAKE_LOG_LEVEL`` in the ambient environment changes nothing.

Everything is written through the same encoder the wire uses
(:func:`vendorfake.core.util.json.dump_json`), so a log line and a response
body agree on separators and on non-ASCII text.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, TextIO

from vendorfake.core.util.json import dump_json

__all__ = ["LEVELS", "JsonLogger", "SilentLogger", "level_index"]

LEVELS: Final[tuple[str, ...]] = ("debug", "info", "warn", "error")
"""The four levels, least to most severe. Ported verbatim; the ordering is the
comparison."""


def level_index(level: str) -> int:
    """Where ``level`` sits in :data:`LEVELS`; an unknown name is ``debug``.

    Ported from the reference's ``Math.max(0, order.indexOf(level))``: a typo
    in the level makes the unit *more* talkative, never less. That is the right
    direction for a fake -- a misconfigured threshold that silently swallowed
    errors would be indistinguishable from a unit that had nothing to say --
    and it is kept rather than tightened so the two implementations answer a
    junk level identically.
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
        # Resolved at emit time, not at construction: pytest replaces
        # ``sys.stderr`` per test, and a captured reference would write into a
        # stream the test has already closed.
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

    Exists so that a test which asserts on captured output can prove the
    absence of a line, and so a conformance run over hundreds of requests is
    not measured on the speed of its own logging.
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
