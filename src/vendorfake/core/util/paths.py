"""One resolver for dotted field paths, and only one.

FOR: reading a field out of a decoded request body along a path that a *vendor
declared as data* -- ``order.reference_id`` for in-band fault triggering,
``idempotency_key`` for the idempotency spec, ``line_items[0].note`` for
anything that comes next. A vendor states the path; the core walks it.

INVARIANT: **an unresolvable path is ``MISSING``, never ``None``.** A caller
cannot tell "the key was absent" from "the key was explicitly null" if both
arrive as ``None``, and both of the first two consumers care about exactly that
distinction: a magic value only arms a fault when the field holds a *string*,
and an idempotency key that is present-but-null is a client error rather than
an absent key. ``MISSING`` is imported from ``util/json.py`` rather than
redeclared here, so there is one absence marker in the core and ``is`` compares
true across modules.

Second invariant, and the reason this is a module rather than three loops: the
grammar is stated once. The reference already had two path walkers -- the
general ``dotGet`` in ``util/json.ts`` and a private ``split('.').reduce(...)``
inside ``unit.ts``'s idempotency step that understands no brackets at all -- so
``idempotency.keyPath: 'order.reference_id[0]'`` resolved one way for chaos and
another way for idempotency. Two grammars for one concept is a drift generator;
this file is the only one.

Ported from ``packages/core/src/util/json.ts:dotGet``, with the JavaScript
host-object behaviour deliberately dropped: ``dot_get(["a"], "length")`` is
``MISSING`` here where JS returns ``1``, because ``length`` is a property of
the JS array object and not of the JSON document anybody wrote. Recorded as
``provenance: judgment``. No vendor path uses it.

Container types are matched structurally: a ``Mapping`` is indexed by key and a
``list`` by non-negative decimal index. ``Mapping`` and not ``dict``, which is
load-bearing -- ``HandlerArgs.body()`` returns a ``FormData`` for a
form-encoded request, and the reference's magic extraction, reading only
``safeJson``, could never see one.
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
    """One step: read ``key`` out of ``container``, or report absence.

    The reference's guard is ``cur === null || typeof cur !== 'object'``, which
    admits objects and arrays and rejects every scalar -- notably strings, so
    ``dotGet('abc', '0')`` is ``undefined`` and not ``'a'``. ``Mapping`` and
    ``list`` are the Python spelling of that pair.

    A list index must be a non-negative ASCII decimal. JavaScript's
    ``arr[Number('-1')]`` is ``undefined`` because ``-1`` is not an element
    index, while Python's ``lst[-1]`` is the last element -- porting the read
    without this guard would silently invent a value at the end of every list.
    """
    if isinstance(container, Mapping):
        if key not in container:
            return MISSING
        # Annotated rather than returned inline: a Mapping read is untyped, and
        # letting the Any escape would silently widen every caller's narrowing.
        value: object = container[key]
        return value
    if isinstance(container, list):
        if not (key.isascii() and key.isdigit()):
            return MISSING
        position = int(key)
        return container[position] if position < len(container) else MISSING
    return MISSING


def dot_get(obj: object, path: str) -> object | Missing:
    """Resolve ``path`` against ``obj``, or return :data:`MISSING`.

    The return is ``object`` rather than ``Any`` on purpose: a JSON document is
    untyped, so every caller has to narrow before it can use the value, and a
    caller that forgets is a type error rather than a runtime one. The two
    narrowings that matter in the core are ``value is MISSING`` and
    ``isinstance(value, str)``.

    A malformed segment (``a..b``, ``a]b``) resolves to ``MISSING`` rather than
    raising, matching the reference: these paths come from vendor declarations
    and profile documents, and the failure a consumer needs to see is "the rule
    never fired", which the rule-validation pass reports, not a 500 from a
    regex.
    """
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
