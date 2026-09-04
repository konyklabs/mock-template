"""One resolver for dotted field paths, and only one: a vendor states a path (``order.reference_id``,
``line_items[0].note``) and the core walks it. An unresolvable path is ``MISSING``, never ``None``.
JUDGMENT: a host property like ``.length`` resolves to ``MISSING`` too.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from vendorfake.core.util.json import MISSING, Missing

__all__ = ["dot_get"]

#: One path segment: a name, then any number of ``[...]`` subscripts.
_SEGMENT = re.compile(r"^([^\[\]]+)((?:\[[^\]]+\])*)$")
_SUBSCRIPT = re.compile(r"\[([^\]]+)\]")


def _index(container: object, key: str) -> object | Missing:
    """Read ``key`` out of ``container``, or report absence; ``Mapping`` (not ``dict``, for ``FormData``) or ``list`` only."""
    if isinstance(container, Mapping):
        if key not in container:
            return MISSING
        value: object = container[key]
        return value
    if isinstance(container, list):
        if not (key.isascii() and key.isdigit()):
            return MISSING
        position = int(key)
        return container[position] if position < len(container) else MISSING
    return MISSING


def dot_get(obj: object, path: str) -> object | Missing:
    """Resolve ``path`` against ``obj``, or :data:`MISSING`; a malformed segment is ``MISSING`` too, not an error."""
    if obj is None:
        return MISSING
    current: object = obj
    for segment in path.split("."):
        matched = _SEGMENT.match(segment)
        if matched is None:
            return MISSING
        current = _index(current, matched.group(1))
        if current is MISSING:
            return MISSING
        for subscript in _SUBSCRIPT.finditer(matched.group(2)):
            current = _index(current, subscript.group(1))
            if current is MISSING:
                return MISSING
    return current
