"""vendorfake.agent -- the agent-facing surface: ``agent-setup`` and ``explain``.

FOR: the two ``vendorfake`` subcommands whose reader is not a person deciding
whether to adopt this package, but a coding agent already working in a
consumer's repository -- one that needs a compact, load-bearing contract file
rather than the README, and a way to ask "what is this fault/route/header"
without opening the source.

Reached only from ``vendorfake.cli``'s ``agent-setup`` and ``explain``
subcommand bodies, exactly as ``vendorfake.asgi`` is reached only from
``serve``: importing this package costs nothing until a consumer types one of
those two words.

``rules_template`` holds the text ``agent-setup`` writes; ``setup`` holds the
filesystem and ``.mcp.json`` mechanics around it; ``explain`` holds the five
lookups ``explain`` answers and their text rendering.
"""

from __future__ import annotations

__all__: list[str] = []
