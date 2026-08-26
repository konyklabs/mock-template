"""The file-drop binding, which is the boundary claim made falsifiable.

There is no framework anywhere in this file. If the core ever grew an HTTP
assumption -- a status a framework decided, a header a middleware supplied, a
body read through ``Request.form()`` -- these tests are where it would show up
as a failure, because nothing here could have supplied any of it.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from tests.fakes import FakeVendor, make_unit, route
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.reply import json_, no_content, text
from vendorfake.core.transport.filedrop import TRANSPORT, FileDrop, serve_file_drop


def _echo(args: Any) -> Any:
    return json_(
        {
            "method": args.req.method,
            "path": args.req.path,
            "transport": args.req.transport,
            "query": dict(args.req.query),
            "content_type": args.media_type(),
            "fields": dict(args.form()) if args.media_type() == "application/x-www-form-urlencoded" else {},
            "raw_len": len(args.req.raw_body),
            "request_id": args.req.id,
        }
    )


def _unit() -> Any:
    return make_unit(
        [
            route("POST", "/echo", _echo),
            route("GET", "/echo", _echo),
            route("GET", "/plain", lambda args: text("not json at all")),
            route("DELETE", "/gone", lambda args: no_content()),
        ],
        vendor=FakeVendor(),
        control_routes=control_plane_routes,
    )


def _drop(tmp_path: Path) -> FileDrop:
    return serve_file_drop(_unit(), tmp_path)


def _write(drop: FileDrop, name: str, document: object) -> None:
    (drop.in_dir / f"{name}.request.json").write_text(json.dumps(document), encoding="utf-8")


def _read(drop: FileDrop, name: str) -> Any:
    return json.loads((drop.out_dir / f"{name}.response.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# the basic round trip
# ---------------------------------------------------------------------------


def test_a_request_document_is_answered_by_the_same_unit_that_serves_http(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "POST", "path": "/echo", "body": {"a": 1}})
    results = drop.poll()

    assert [r.name for r in results] == ["01"]
    answer = _read(drop, "01")
    assert answer["status"] == 200
    assert answer["body"]["path"] == "/echo"
    assert answer["body"]["transport"] == TRANSPORT


def test_the_binding_creates_its_three_directories(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    assert drop.in_dir.is_dir()
    assert drop.out_dir.is_dir()
    assert drop.done_dir.is_dir()


def test_an_answered_request_is_moved_aside_so_a_second_poll_does_not_repeat_it(tmp_path: Path) -> None:
    """Idempotence of the drop directory. A re-processed document is a
    duplicated side effect, which for a create is a duplicate entity."""
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "POST", "path": "/echo", "body": {}})
    assert len(drop.poll()) == 1
    assert drop.poll() == ()
    assert (drop.done_dir / "01.request.json").is_file()
    assert not (drop.in_dir / "01.request.json").exists()


def test_a_batch_is_answered_in_name_order(tmp_path: Path) -> None:
    """A drop is a sequence: `01.create`, `02.pay` answered the other way round
    means the second fails on state the first was going to make."""
    drop = _drop(tmp_path)
    for name in ("03", "01", "02"):
        _write(drop, name, {"method": "POST", "path": "/echo", "body": {"n": name}})
    assert [r.name for r in drop.poll()] == ["01", "02", "03"]


def test_a_document_may_carry_query_parameters_and_headers(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    _write(
        drop,
        "01",
        {"method": "GET", "path": "/echo", "query": {"limit": "2"}, "headers": {"X-Unit-Request-Id": "corr-9"}},
    )
    drop.poll()
    body = _read(drop, "01")["body"]
    assert body["query"] == {"limit": "2"}
    # Header names are lower-cased by the binding, and the inbound request id
    # is honoured -- without which the same logical call has two identities
    # depending on which binding carried it.
    assert body["request_id"] == "corr-9"


# ---------------------------------------------------------------------------
# bodies
# ---------------------------------------------------------------------------


def test_raw_body_wins_over_body_so_exact_bytes_can_be_stated(tmp_path: Path) -> None:
    """A document testing a form-encoded or a deliberately malformed body has
    to be able to say the exact bytes; webhook signatures cover received bytes
    and a re-serialisation would silently change what is under test."""
    drop = _drop(tmp_path)
    _write(
        drop,
        "01",
        {"method": "POST", "path": "/echo", "body": {"ignored": True}, "raw_body": "a=1&a=2"},
    )
    drop.poll()
    assert _read(drop, "01")["body"]["raw_len"] == len("a=1&a=2")


def test_a_form_encoded_body_reaches_the_handler_with_no_framework_present(tmp_path: Path) -> None:
    """The bake-off trap, over a binding that could not have loaded a
    multipart parser even if one were installed."""
    drop = _drop(tmp_path)
    _write(
        drop,
        "01",
        {
            "method": "POST",
            "path": "/echo",
            "headers": {"content-type": "application/x-www-form-urlencoded"},
            "raw_body": "grant_type=authorization_code&code=abc",
        },
    )
    drop.poll()
    body = _read(drop, "01")["body"]
    assert body["content_type"] == "application/x-www-form-urlencoded"
    assert body["fields"] == {"grant_type": "authorization_code", "code": "abc"}


def test_a_json_body_defaults_its_content_type_the_way_every_binding_does(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "POST", "path": "/echo", "body": {"a": 1}})
    drop.poll()
    assert _read(drop, "01")["body"]["content_type"] == "application/json"


def test_a_document_with_no_body_produces_a_zero_length_request(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "GET", "path": "/echo"})
    drop.poll()
    assert _read(drop, "01")["body"]["raw_len"] == 0


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------


def test_a_json_response_is_embedded_as_json_and_not_as_a_string(tmp_path: Path) -> None:
    """A collector reads these documents with an ordinary JSON parser; a body
    kept as a string would put every field a second parse away."""
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "POST", "path": "/echo", "body": {}})
    drop.poll()
    assert isinstance(_read(drop, "01")["body"], dict)


def test_a_non_json_response_is_kept_as_the_text_it_was(tmp_path: Path) -> None:
    """Rather than dropped. A vendor that answers XML, or a redirect that
    answers nothing, is still a fact about the run."""
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "GET", "path": "/plain"})
    drop.poll()
    assert _read(drop, "01")["body"] == "not json at all"


def test_a_zero_byte_response_body_is_null_rather_than_an_empty_string(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "DELETE", "path": "/gone"})
    drop.poll()
    answer = _read(drop, "01")
    assert answer["status"] == 204
    assert answer["body"] is None


def test_the_response_carries_the_units_headers_untouched(tmp_path: Path) -> None:
    """Including the vendor's own decoration and the request-id echo, because
    a binding that dropped headers would make cross-binding byte comparison
    meaningless."""
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "POST", "path": "/echo", "body": {}})
    drop.poll()
    headers = _read(drop, "01")["headers"]
    assert headers["content-type"] == "application/json"
    assert headers["acme-version"] == "2024-01-01"
    assert "x-unit-request-id" in headers


def test_a_vendor_shaped_error_travels_the_file_drop_unchanged(tmp_path: Path) -> None:
    """`x-unit-error` included: a consumer reading response files must be able
    to tell *why* a call failed without parsing a vendor's prose."""
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "GET", "path": "/nowhere"})
    drop.poll()
    answer = _read(drop, "01")
    assert answer["status"] == 404
    assert answer["body"]["error"]["code"] == "no_route"


def test_the_control_plane_is_reachable_over_the_file_drop(tmp_path: Path) -> None:
    """The binding-independence claim, made concrete: the same `/__unit/*`
    addresses answer with no socket in the picture."""
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "GET", "path": "/__unit/health"})
    drop.poll()
    assert _read(drop, "01")["body"]["status"] == "ok"


# ---------------------------------------------------------------------------
# malformed drops
# ---------------------------------------------------------------------------


def test_an_unparseable_document_is_answered_rather_than_crashing_the_poll(tmp_path: Path) -> None:
    """The reference's `JSON.parse` throws inside the loop and abandons every
    later file in the batch, so one bad document from a partner stops the whole
    drop -- which is the behaviour a file-drop consumer is usually trying to
    test their way out of."""
    drop = _drop(tmp_path)
    (drop.in_dir / "01.request.json").write_text("{not json", encoding="utf-8")
    _write(drop, "02", {"method": "POST", "path": "/echo", "body": {}})

    results = drop.poll()
    assert [r.name for r in results] == ["01", "02"]
    assert _read(drop, "01")["status"] == 400
    assert _read(drop, "01")["headers"]["x-unit-error"] == "invalid_value"
    assert _read(drop, "02")["status"] == 200


def test_a_document_missing_method_or_path_is_a_shaped_four_hundred(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    _write(drop, "01", {"path": "/echo"})
    _write(drop, "02", {"method": "POST"})
    _write(drop, "03", [1, 2, 3])
    drop.poll()
    for name in ("01", "02", "03"):
        assert _read(drop, name)["status"] == 400, name
        assert _read(drop, name)["body"]["error"]["code"] == "invalid_value", name


def test_a_malformed_document_is_still_moved_aside(tmp_path: Path) -> None:
    """Otherwise the next poll re-reads it and the drop never drains."""
    drop = _drop(tmp_path)
    (drop.in_dir / "01.request.json").write_text("{not json", encoding="utf-8")
    drop.poll()
    assert (drop.done_dir / "01.request.json").is_file()
    assert drop.poll() == ()


def test_a_file_that_is_not_a_request_document_is_ignored(tmp_path: Path) -> None:
    """The suffix is the contract: a partner's README or a half-uploaded
    `.tmp` in the drop directory must not be answered."""
    drop = _drop(tmp_path)
    (drop.in_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (drop.in_dir / "01.request.json.part").write_text("{}", encoding="utf-8")
    assert drop.poll() == ()


# ---------------------------------------------------------------------------
# atomicity and the polling thread
# ---------------------------------------------------------------------------


def test_no_partial_response_file_is_ever_visible_to_a_collector(tmp_path: Path) -> None:
    """Written under a temporary name and renamed. The classic defect of every
    file-drop integration is a collector reading a half-written document, and
    it is not one a fake should reproduce by accident."""
    drop = _drop(tmp_path)
    _write(drop, "01", {"method": "POST", "path": "/echo", "body": {}})
    drop.poll()
    assert [p.name for p in drop.out_dir.iterdir()] == ["01.response.json"]


def test_a_started_binding_answers_documents_dropped_after_it_started(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    drop.start(interval_ms=5)
    try:
        _write(drop, "01", {"method": "POST", "path": "/echo", "body": {}})
        deadline = threading.Event()
        for _ in range(200):
            if (drop.out_dir / "01.response.json").is_file():
                break
            deadline.wait(0.01)
    finally:
        drop.stop()
    assert _read(drop, "01")["status"] == 200


def test_stop_waits_for_the_current_pass_rather_than_merely_signalling(tmp_path: Path) -> None:
    """A caller that stops the binding and then deletes its directory must not
    race a write. A fake must not outlive the test that built it."""
    drop = _drop(tmp_path)
    drop.start(interval_ms=5)
    drop.stop()
    assert drop._thread is None
    # Nothing is still polling: a document dropped now stays untouched.
    _write(drop, "01", {"method": "POST", "path": "/echo", "body": {}})
    threading.Event().wait(0.05)
    assert (drop.in_dir / "01.request.json").is_file()


def test_starting_twice_does_not_start_a_second_thread(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    drop.start(interval_ms=5)
    first = drop._thread
    drop.start(interval_ms=5)
    try:
        assert drop._thread is first
    finally:
        drop.stop()


def test_stopping_a_binding_that_never_started_is_harmless(tmp_path: Path) -> None:
    _drop(tmp_path).stop()
