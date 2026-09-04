"""The ``explain`` subcommand: five lookups, text and JSON forms, refusals."""

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


def test_explain_route_accepts_a_path_form_profile() -> None:
    """Every other subcommand accepts ``--profile ./my-profile.json``
    alongside ``--profile full`` (``core/config/profile.py``'s
    ``profile_path`` heuristic: absolute, or ending in ``.json``, is a path).
    ``explain`` -- ``_check_profile``'s job -- used to be the one subcommand
    that refused it, checking the string against the vendor's named profiles
    only (deep lens, F increment, konyklabs/roadmap#74). Points at square's
    own shipped ``full.json`` by absolute path, so the route looked up is
    real.
    """
    from vendorfake.registry import resolve_vendor

    full_json = resolve_vendor("square").profile_dir / "full.json"
    assert full_json.exists(), full_json  # sanity: the path this test hinges on

    code, out = run("explain", "route", "CreateOrder", "--vendor", "square", "--profile", str(full_json))
    assert code == 0
    assert "POST /v2/orders" in out


def test_explain_route_refuses_a_nonexistent_path_form_profile_cleanly() -> None:
    """A bad path used to reach ``_check_profile``'s named-profile guard and
    be refused as if it were a mistyped name; it is now let through
    unchecked and refused by ``create_unit`` -> ``load_profile`` itself, as a
    ``UnitError`` ``_explain`` now catches alongside ``ValueError`` rather
    than a traceback.
    """
    with pytest.raises(SystemExit) as raised:
        run("explain", "route", "CreateOrder", "--vendor", "square", "--profile", "/nonexistent/path/profile.json")
    assert str(raised.value).startswith("vendorfake: "), str(raised.value)


def test_explain_route_refuses_a_malformed_profile_document_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same widened ``except (ValueError, UnitError)`` in ``_explain`` as
    ``profiles``/``info``/``routes``/``serve`` already have (deep lens, F
    increment, konyklabs/roadmap#74): ``_check_profile`` -- the guard
    ``explain route``/``explain error`` run before building a unit -- calls
    ``available_profiles``, which raises ``UnitError`` for a malformed
    document anywhere in the vendor's profile directory, not the
    ``ValueError`` a bad *name* raises. Reverting ``_explain``'s except
    clause back to bare ``ValueError`` leaves this test red.
    """
    import vendorfake.registry as registry_module
    from tests.fakes import FakeVendor

    (tmp_path / "broken.json").write_text('{"name": "broken", "capabilities": "not-a-list"}', encoding="utf-8")
    definition = FakeVendor(name="acme", profile_dir=tmp_path, base_dir=tmp_path)
    monkeypatch.setattr(registry_module, "resolve_vendor", lambda name: definition)

    with pytest.raises(SystemExit) as raised:
        run("explain", "route", "AnyOperation", "--vendor", "acme")

    assert str(raised.value).startswith("vendorfake: "), str(raised.value)


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


def test_explain_header_covers_vendorfake_fault() -> None:
    """Deep lens, F increment (konyklabs/roadmap#74): docs/api-contract.md's
    ``Vendorfake-*`` table names seven headers; ``explain header`` used to
    know only five, silently missing the two chaos ones this same increment
    documented as public.
    """
    code, out = run("explain", "header", "Vendorfake-Fault", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["name"] == "Vendorfake-Fault"
    assert "fault" in data["summary"].lower()


def test_explain_header_covers_vendorfake_rule() -> None:
    code, out = run("explain", "header", "Vendorfake-Rule", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["name"] == "Vendorfake-Rule"
    assert "rule" in data["summary"].lower()


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
