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

INVARIANT: **a refused run writes nothing.** Every check that can refuse this
call -- the rules-file existence check, and (with ``--mcp --allow-future``)
parsing and validating an existing ``.mcp.json`` -- runs before either file is
touched. A rerun without ``--force`` against an already-configured repo, or a
run against a malformed ``.mcp.json``, is a clean refusal, never a
half-written ``.mcp.json`` next to a rules file that got written anyway, and
never a bare traceback in place of a message naming the file and the problem.
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
    every other key and every other server the document already holds.

    Assumes ``existing["mcpServers"]``, when present, is already a ``dict`` --
    :func:`_load_and_merge_mcp` is this function's only caller, and it refuses
    a non-object ``mcpServers`` before ever reaching here. The
    ``isinstance(servers_raw, dict)`` check below is what makes an *absent*
    ``mcpServers`` key ``{}`` rather than a ``KeyError``; it is not, on its
    own, the validation -- a non-object value must never reach this function
    silently coerced into an empty dict, which is why the check lives one
    layer up, where there is a path to name in the refusal.
    """
    merged = dict(existing)
    servers_raw = merged.get("mcpServers")
    servers = dict(servers_raw) if isinstance(servers_raw, dict) else {}
    servers[MCP_SERVER_NAME] = _mcp_entry()
    merged["mcpServers"] = servers
    return merged


def _load_and_merge_mcp(mcp_path: Path) -> dict[str, object]:
    """Read ``mcp_path`` (an empty document if it does not exist yet), validate
    it, and return the merged document -- without writing anything.

    Called before the rules file is touched, so that a document this function
    refuses never leaves a half-applied run behind. A missing file merges
    against ``{}``. An existing file must parse as JSON, and its top level
    must be a JSON object -- ``.mcp.json``'s own shape, and the only shape
    :func:`_merge_mcp` can add a ``mcpServers`` key to. Anything else (an
    array, a string, a number, or text that is not valid JSON at all) is a
    ``ValueError`` naming the file and the problem, not a silently discarded
    document and not a bare ``json.JSONDecodeError`` traceback.

    A present ``mcpServers`` key is checked too, the same way: it must be a
    JSON object, because that is the only shape a server name can be a key
    of. Anything else (most plausibly an array, from a document authored
    with a different MCP client's schema in mind) is refused by name rather
    than silently replaced with ``{}`` -- which is what unconditionally
    trusting ``isinstance(servers_raw, dict)`` inside :func:`_merge_mcp`
    would do, discarding every server the document already named.
    """
    if not mcp_path.exists():
        return _merge_mcp({})

    raw = mcp_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{mcp_path} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"{mcp_path} must contain a JSON object at the top level (got {type(parsed).__name__}); "
            "refusing to merge into it."
        )
    if "mcpServers" in parsed and not isinstance(parsed["mcpServers"], dict):
        raise ValueError(
            f'{mcp_path}\'s "mcpServers" must be a JSON object '
            f"(got {type(parsed['mcpServers']).__name__}); refusing to merge into it."
        )
    return _merge_mcp(parsed)


def write_agent_setup(
    *,
    directory: Path,
    tests_glob: str = DEFAULT_TESTS_GLOB,
    mcp: bool = False,
    allow_future: bool = False,
    force: bool = False,
) -> AgentSetupResult:
    """Write the rules file, and merge the ``.mcp.json`` entry if instructed.

    Everything that can refuse this call is checked *before* anything is
    written: the rules-file existence check, then -- with ``--mcp
    --allow-future`` -- reading and validating ``.mcp.json`` through
    :func:`_load_and_merge_mcp`. Only once both have succeeded does either
    file actually get written, which is what keeps a refused call a clean
    no-op rather than a rules file written next to a ``.mcp.json`` this call
    never got to (or got to and refused).

    Raises ``FileExistsError`` -- naming the path -- for a rules file that
    already exists without ``force``. Raises ``ValueError`` -- naming the
    path and the problem -- for a ``.mcp.json`` that is not valid JSON, or
    whose top level is not a JSON object, when ``--mcp --allow-future`` asks
    this call to merge into it.
    """
    rules_path = directory / RULES_PATH
    if rules_path.exists() and not force:
        raise FileExistsError(f"{rules_path} already exists. Pass --force to overwrite, or delete it and rerun.")

    mcp_path = directory / MCP_PATH
    merged_mcp: dict[str, object] | None = None
    if mcp and allow_future:
        merged_mcp = _load_and_merge_mcp(mcp_path)

    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(render_rules_file(tests_glob), encoding="utf-8")
    written = [rules_path]
    notice: str | None = None

    if mcp:
        notice = MCP_FUTURE_NOTICE
        if allow_future:
            assert merged_mcp is not None  # computed above, before any write
            mcp_path.write_text(json.dumps(merged_mcp, indent=2) + "\n", encoding="utf-8")
            written.append(mcp_path)

    return AgentSetupResult(written=tuple(written), notice=notice)
