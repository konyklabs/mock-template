"""vendorfake.agent.setup -- writing ``agent-setup``'s files into a consumer's repo.

FOR: ``vendorfake agent-setup``, and nothing else. Two writes, both
idempotent-safe by refusal rather than by silently overwriting: the rules
file, always; one ``.mcp.json`` entry, only with ``--mcp``, and only actually
written with ``--allow-future`` besides -- ``vendorfake mcp`` does not exist
until 0.4 (see ``rules_template``'s reference to ``docs/for-agents.md`` for
where that is explained to a consumer).

INVARIANT: **nothing here runs on install.** This module is reached only from
the ``agent-setup`` subcommand body in :mod:`vendorfake.cli`, exactly as
:mod:`vendorfake.asgi` is reached only from ``serve``: importing
``vendorfake`` costs nothing extra until a consumer actually types the
command.

INVARIANT: **a refused run writes nothing.** The rules-file existence check
happens before either file is touched, so a rerun without ``--force`` against
an already-configured repo is a clean refusal, never a half-written ``.mcp.json``
next to an untouched rules file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from vendorfake.agent.rules_template import DEFAULT_TESTS_GLOB, render_rules_file

__all__ = ["MCP_FUTURE_NOTICE", "AgentSetupResult", "write_agent_setup"]

#: Relative to the consumer's ``--dir``.
RULES_PATH = Path(".claude/rules/vendorfake.md")
MCP_PATH = Path(".mcp.json")
MCP_SERVER_NAME = "vendorfake"

MCP_FUTURE_NOTICE = (
    "`vendorfake mcp` does not exist yet (ships in 0.4). Pass --allow-future alongside --mcp to write "
    "the .mcp.json entry anyway, ready for when it does."
)


@dataclass(frozen=True, slots=True)
class AgentSetupResult:
    """What :func:`write_agent_setup` did, for the CLI to report.

    ``written`` is every path actually written, in write order; ``notice`` is
    the one-line warning ``--mcp`` prints, or ``None`` when ``--mcp`` was not
    given.
    """

    written: tuple[Path, ...] = ()
    notice: str | None = None


def _mcp_entry() -> dict[str, object]:
    return {"command": "vendorfake", "args": ["mcp"]}


def _merge_mcp(existing: dict[str, object]) -> dict[str, object]:
    """Add or replace the ``vendorfake`` entry under ``mcpServers``, preserving
    every other key and every other server the document already holds."""
    merged = dict(existing)
    servers_raw = merged.get("mcpServers")
    servers = dict(servers_raw) if isinstance(servers_raw, dict) else {}
    servers[MCP_SERVER_NAME] = _mcp_entry()
    merged["mcpServers"] = servers
    return merged


def write_agent_setup(
    *,
    directory: Path,
    tests_glob: str = DEFAULT_TESTS_GLOB,
    mcp: bool = False,
    allow_future: bool = False,
    force: bool = False,
) -> AgentSetupResult:
    """Write the rules file, and merge the ``.mcp.json`` entry if instructed.

    Raises ``FileExistsError`` -- naming the path -- for a rules file that
    already exists without ``force``; nothing is written in that call at all,
    ``.mcp.json`` included, which is what keeps a refused rerun a clean no-op
    rather than a partially-applied one.
    """
    rules_path = directory / RULES_PATH
    if rules_path.exists() and not force:
        raise FileExistsError(f"{rules_path} already exists. Pass --force to overwrite, or delete it and rerun.")

    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(render_rules_file(tests_glob), encoding="utf-8")
    written = [rules_path]
    notice: str | None = None

    if mcp:
        notice = MCP_FUTURE_NOTICE
        if allow_future:
            mcp_path = directory / MCP_PATH
            existing: dict[str, object] = {}
            if mcp_path.exists():
                existing = json.loads(mcp_path.read_text(encoding="utf-8"))
            merged = _merge_mcp(existing)
            mcp_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            written.append(mcp_path)

    return AgentSetupResult(written=tuple(written), notice=notice)
