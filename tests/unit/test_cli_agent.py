"""The agent-facing subcommands: ``agent-setup`` and ``explain``.

FOR: the two subcommands nothing in ``tests/unit/test_cli.py`` exercises --
kept as a separate file rather than appended there because both are
self-contained additions with no shared fixture, and a reviewer of one should
not have to read the other. See :mod:`vendorfake.agent` for the modules under
test.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from vendorfake.cli import main


def run(*argv: str) -> tuple[int, str]:
    """Call ``main`` with stdout captured, returning the code and the text.

    Only stdout: the unit's own JSON logger writes to stderr (see
    ``core/logging.py``), so a route/error lookup that builds a unit never
    pollutes what this captures -- the same property ``--json``'s "nothing
    else on stdout" promise depends on.
    """
    buffer = io.StringIO()
    saved = sys.stdout
    sys.stdout = buffer
    try:
        code = main(list(argv))
    finally:
        sys.stdout = saved
    return code, buffer.getvalue()


# ---------------------------------------------------------------------------
# agent-setup
# ---------------------------------------------------------------------------


def test_agent_setup_writes_the_rules_file_to_a_fresh_dir(tmp_path: Path) -> None:
    code, out = run("agent-setup", "--dir", str(tmp_path))
    assert code == 0

    written = tmp_path / ".claude" / "rules" / "vendorfake.md"
    assert written.exists()
    assert str(written) in out
    text = written.read_text(encoding="utf-8")
    assert text.startswith('---\npaths:\n  - "tests/**"\n---\n')
    assert "vendorfake.asgi" in text  # the internal-modules rule made it in
    assert "Vendorfake-Near-Miss" in text
    assert "github.com/konyklabs/vendorfake" in text  # the escape hatch resolves for a consumer
    assert "python -c" not in text  # no site-packages-hunting snippet
    assert "@pytest.mark.vendorfake(" in text  # the marker form, beside unit()
    assert "vendorfake_unit" in text


def test_agent_setup_reflects_a_custom_tests_glob_in_the_frontmatter(tmp_path: Path) -> None:
    code, _out = run("agent-setup", "--dir", str(tmp_path), "--tests-glob", "spec/**/*.py")
    assert code == 0
    text = (tmp_path / ".claude" / "rules" / "vendorfake.md").read_text(encoding="utf-8")
    assert 'paths:\n  - "spec/**/*.py"' in text
    assert "loads only for paths under `spec/**/*.py`" in text


def test_agent_setup_refuses_to_overwrite_an_existing_rules_file_without_force(tmp_path: Path) -> None:
    first_code, _ = run("agent-setup", "--dir", str(tmp_path))
    assert first_code == 0
    marker_path = tmp_path / ".claude" / "rules" / "vendorfake.md"
    marker_path.write_text("hand-edited\n", encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        run("agent-setup", "--dir", str(tmp_path))
    assert "already exists" in str(raised.value)
    assert "--force" in str(raised.value)
    # Refused: the hand-edit survives untouched.
    assert marker_path.read_text(encoding="utf-8") == "hand-edited\n"


def test_agent_setup_force_overwrites_an_existing_rules_file(tmp_path: Path) -> None:
    run("agent-setup", "--dir", str(tmp_path))
    marker_path = tmp_path / ".claude" / "rules" / "vendorfake.md"
    marker_path.write_text("hand-edited\n", encoding="utf-8")

    code, _out = run("agent-setup", "--dir", str(tmp_path), "--force")
    assert code == 0
    assert marker_path.read_text(encoding="utf-8") != "hand-edited\n"
    assert "vendorfake" in marker_path.read_text(encoding="utf-8")


def test_agent_setup_mcp_without_allow_future_prints_a_notice_and_writes_nothing(tmp_path: Path) -> None:
    code, out = run("agent-setup", "--dir", str(tmp_path), "--mcp")
    assert code == 0
    assert "0.4" in out
    assert "--allow-future" in out
    assert not (tmp_path / ".mcp.json").exists()
    # The rules file is unaffected by the --mcp gating.
    assert (tmp_path / ".claude" / "rules" / "vendorfake.md").exists()


def test_agent_setup_mcp_with_allow_future_writes_the_entry(tmp_path: Path) -> None:
    code, out = run("agent-setup", "--dir", str(tmp_path), "--mcp", "--allow-future")
    assert code == 0
    mcp_path = tmp_path / ".mcp.json"
    assert str(mcp_path) in out
    document = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert document["mcpServers"]["vendorfake"] == {"command": "vendorfake", "args": ["mcp"]}


def test_agent_setup_mcp_merge_preserves_an_existing_server(tmp_path: Path) -> None:
    mcp_path = tmp_path / ".mcp.json"
    mcp_path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other-server", "args": ["--flag"]}}}),
        encoding="utf-8",
    )

    code, _out = run("agent-setup", "--dir", str(tmp_path), "--mcp", "--allow-future")
    assert code == 0
    document = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert document["mcpServers"]["other"] == {"command": "other-server", "args": ["--flag"]}
    assert document["mcpServers"]["vendorfake"] == {"command": "vendorfake", "args": ["mcp"]}


def test_agent_setup_mcp_rerun_replaces_only_the_vendorfake_entry(tmp_path: Path) -> None:
    """A second ``--mcp --allow-future`` run (no ``--force`` needed -- the
    rules-file refusal does not gate the mcp merge once it already ran) still
    reports the same, idempotent entry rather than duplicating it."""
    run("agent-setup", "--dir", str(tmp_path), "--mcp", "--allow-future")
    code, _out = run("agent-setup", "--dir", str(tmp_path), "--force", "--mcp", "--allow-future")
    assert code == 0
    document = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert document["mcpServers"] == {"vendorfake": {"command": "vendorfake", "args": ["mcp"]}}


def test_agent_setup_mcp_refuses_a_non_object_document_and_writes_nothing(tmp_path: Path) -> None:
    """A top-level JSON array is not a document ``_merge_mcp`` can add
    ``mcpServers`` to. This must be a named refusal, not a silent
    replacement of the whole file -- and, because validation runs before any
    write, not even the rules file lands."""
    mcp_path = tmp_path / ".mcp.json"
    mcp_path.write_text("[]", encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        run("agent-setup", "--dir", str(tmp_path), "--mcp", "--allow-future")
    assert str(mcp_path) in str(raised.value)
    assert mcp_path.read_text(encoding="utf-8") == "[]"  # untouched
    assert not (tmp_path / ".claude" / "rules" / "vendorfake.md").exists()


def test_agent_setup_mcp_refuses_invalid_json_and_writes_nothing(tmp_path: Path) -> None:
    """Malformed JSON must be a message naming the file, not a bare
    ``json.JSONDecodeError`` traceback -- and, since this is checked before
    either file is written, never a half-applied run (a rules file written,
    then a crash on ``.mcp.json``)."""
    mcp_path = tmp_path / ".mcp.json"
    mcp_path.write_text("not json{", encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        run("agent-setup", "--dir", str(tmp_path), "--mcp", "--allow-future")
    assert str(mcp_path) in str(raised.value)
    assert mcp_path.read_text(encoding="utf-8") == "not json{"  # untouched
    assert not (tmp_path / ".claude" / "rules" / "vendorfake.md").exists()


# ---------------------------------------------------------------------------
# explain: route
# ---------------------------------------------------------------------------


def test_explain_route_text_form() -> None:
    code, out = run("explain", "route", "CreateOrder", "--vendor", "square")
    assert code == 0
    assert "POST /v2/orders" in out
    assert "capability" in out
    assert "auth" in out
    assert "bearer" in out


def test_explain_route_json_form() -> None:
    code, out = run("explain", "route", "CreateOrder", "--vendor", "square", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["method"] == "POST"
    assert data["path"] == "/v2/orders"
    assert data["auth"] == "bearer"
    assert data["operation_id"] == "CreateOrder"


def test_explain_route_unknown_operation_id_lists_valid_ones() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "route", "NoSuchOperation", "--vendor", "square")
    assert "NoSuchOperation" in str(raised.value)
    assert "CreateOrder" in str(raised.value)  # a real operation_id, proving the listing is real


def test_explain_route_unknown_profile_is_a_clean_refusal() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "route", "CreateOrder", "--vendor", "square", "--profile", "nope")
    assert "nope" in str(raised.value)
    assert "square" in str(raised.value)
    assert "full" in str(raised.value)  # a real profile name, proving the listing is real


# ---------------------------------------------------------------------------
# explain: fault
# ---------------------------------------------------------------------------


def test_explain_fault_text_form() -> None:
    code, out = run("explain", "fault", "timeout")
    assert code == 0
    assert "timeout" in out
    assert "provenance" in out
    assert "delay_ms" in out


def test_explain_fault_json_form() -> None:
    code, out = run("explain", "fault", "timeout", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["name"] == "timeout"
    assert data["scope"] == "request"
    assert data["provenance"] == "vendor"
    assert "delay_ms" in data["params"]


def test_explain_fault_reports_transport_provenance_for_a_transport_fault() -> None:
    code, out = run("explain", "fault", "connection_reset", "--json")
    assert code == 0
    assert json.loads(out)["provenance"] == "transport"


def test_explain_fault_unknown_name_lists_valid_ones() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "fault", "no-such-fault")
    assert "no-such-fault" in str(raised.value)
    assert "timeout" in str(raised.value)


# ---------------------------------------------------------------------------
# explain: profile
# ---------------------------------------------------------------------------


def test_explain_profile_text_form() -> None:
    code, out = run("explain", "profile", "full", "--vendor", "square")
    assert code == 0
    assert "square/full" in out
    assert "capabilities" in out
    assert "seed" in out


def test_explain_profile_json_form() -> None:
    code, out = run("explain", "profile", "full", "--vendor", "square", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["vendor"] == "square"
    assert data["name"] == "full"
    assert isinstance(data["capabilities"], list)
    assert data["capabilities"]


def test_explain_profile_unknown_name_lists_valid_ones() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "profile", "no-such-profile", "--vendor", "square")
    assert "no-such-profile" in str(raised.value)
    assert "full" in str(raised.value)


def test_explain_profile_unknown_vendor_lists_valid_vendors() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "profile", "full", "--vendor", "sqaure")
    assert "sqaure" in str(raised.value)
    assert "square" in str(raised.value)


# ---------------------------------------------------------------------------
# explain: error
# ---------------------------------------------------------------------------


def test_explain_error_text_form() -> None:
    code, out = run("explain", "error", "rate_limited", "--vendor", "square")
    assert code == 0
    assert "rate_limited" in out
    assert "status" in out
    assert "429" in out
    assert "provenance" in out


def test_explain_error_json_form() -> None:
    code, out = run("explain", "error", "rate_limited", "--vendor", "square", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["kind"] == "rate_limited"
    assert data["status"] == 429
    assert data["provenance"] in {"documented", "judgment"}
    assert "body" in data
    assert "headers" in data


def test_explain_error_unknown_kind_lists_valid_ones() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "error", "no_such_kind", "--vendor", "square")
    assert "no_such_kind" in str(raised.value)
    assert "rate_limited" in str(raised.value)


def test_explain_error_unknown_profile_is_a_clean_refusal() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "error", "rate_limited", "--vendor", "square", "--profile", "nope")
    assert "nope" in str(raised.value)
    assert "square" in str(raised.value)
    assert "full" in str(raised.value)  # a real profile name, proving the listing is real


# ---------------------------------------------------------------------------
# explain: header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    ["Vendorfake-Near-Miss", "vendorfake-near-miss", "VENDORFAKE-NEAR-MISS"],
)
def test_explain_header_is_case_insensitive(spelling: str) -> None:
    code, out = run("explain", "header", spelling)
    assert code == 0
    assert "Vendorfake-Near-Miss" in out
    assert "closest routes" in out


def test_explain_header_json_form() -> None:
    code, out = run("explain", "header", "Vendorfake-Error-Kind", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["name"] == "Vendorfake-Error-Kind"
    assert "present_when" in data
    assert "summary" in data


def test_explain_header_unknown_name_lists_valid_ones() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain", "header", "X-Not-A-Real-Header")
    assert "X-Not-A-Real-Header" in str(raised.value)
    assert "Vendorfake-Near-Miss" in str(raised.value)


# ---------------------------------------------------------------------------
# explain: dispatch
# ---------------------------------------------------------------------------


def test_explain_without_a_kind_is_a_refusal() -> None:
    with pytest.raises(SystemExit) as raised:
        run("explain")
    assert "kind" in str(raised.value)


def test_explain_json_before_and_after_the_kind_produce_the_identical_document() -> None:
    global_code, global_out = run("--json", "explain", "fault", "timeout")
    trailing_code, trailing_out = run("explain", "fault", "timeout", "--json")
    assert global_code == trailing_code == 0
    assert json.loads(global_out) == json.loads(trailing_out)
