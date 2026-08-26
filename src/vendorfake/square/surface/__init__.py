"""The route tables. One module per capability, each exporting a surface object.

A surface is a plain object holding the vendor's dependencies and returning a
``tuple[Route, ...]``. Routes are data: nothing here is a decorator, nothing
registers itself on import, and nothing imports a web framework -- which is why
this package has nothing to import one *for*.
"""

from __future__ import annotations

__all__: list[str] = []
