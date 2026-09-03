"""The file-drop binding: the same unit, driven by files instead of sockets.

FOR: demonstrating rather than asserting that the kernel makes no HTTP
assumption. Plenty of real integrations are not HTTP servers -- a partner drops
a batch file on a share and collects a response file -- and this binding feeds
the same ``Unit.handle`` from JSON documents on disk.

INVARIANT: **this module is the cheap mechanical proof of the boundary.** If
the core ever grew an HTTP assumption -- a status code decided by a framework,
a header a middleware supplied, a body read through ``Request.form()`` -- this
binding is where it would show up as a failure, because there is no framework
here to supply any of it. That is why it is kept rather than dropped as
demo code: it costs a hundred lines and it makes the constraint falsifiable.

The layout is the reference's (``packages/core/src/transport/filedrop.ts``):

    <dir>/in/<name>.request.json     consumed
    <dir>/out/<name>.response.json   written
    <dir>/processed/<name>.request.json   the request, moved aside

A request document is
``{"method", "path", "query"?, "headers"?, "body"?, "raw_body"?}``. A JSON
object cannot repeat a key, so a repeated query parameter is written on the
path -- ``"path": "/v2/items?id=a&id=b"`` -- and ``make_request`` splits it off.

THREE THINGS THIS PORT DOES THAT THE REFERENCE DOES NOT
------------------------------------------------------
It is synchronous, and its polling loop is a thread
    The core is synchronous, so ``poll()`` is a plain loop. ``start()`` runs it
    on a daemon thread with a stop event rather than a ``setInterval`` whose
    handle is unref'd and whose in-flight callback nobody can wait for --
    ``stop()`` here actually joins, so a test that stops the binding knows
    nothing is still writing into its temporary directory.

It writes the response atomically
    The response file is written under a temporary name and renamed into
    place. A collector polling the ``out`` directory would otherwise be able to
    read a half-written document, which is the classic defect of every
    file-drop integration and not one a fake should reproduce by accident.

It records an unparseable request rather than crashing the poller
    A malformed request document produces a response file describing the
    failure, with the same ``x-unit-error`` header a shaped error carries, and
    the poll continues. The reference's ``JSON.parse`` throws inside the loop,
    which abandons every later file in the batch -- so one bad document from a
    partner stops the whole drop, which is the behaviour a file-drop consumer
    is most often trying to test their way out of.

``raw_body`` wins over ``body``, exactly as in ``make_request``: a document
testing a form-encoded or a deliberately malformed body must be able to state
the exact bytes.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vendorfake.core.kernel.reply import decode_body
from vendorfake.core.kernel.unit import Unit, make_request

__all__ = ["TRANSPORT", "FileDrop", "serve_file_drop"]

TRANSPORT = "filedrop"
"""What lands in ``UnitRequest.transport``. Read by conformance to say which
binding produced a result, and by a vendor that wants to answer differently
off-line -- which no vendor should, and which is therefore worth being able to
observe."""

REQUEST_SUFFIX = ".request.json"
RESPONSE_SUFFIX = ".response.json"
DEFAULT_INTERVAL_MS = 200.0


@dataclass(frozen=True, slots=True)
class FileDropResult:
    """One processed document: which file, and what the unit answered."""

    name: str
    status: int
    request_path: Path
    response_path: Path


class FileDrop:
    """A unit bound to a directory. Poll it, or let a thread poll it."""

    __slots__ = ("_stop", "_thread", "_unit", "done_dir", "in_dir", "out_dir")

    def __init__(self, unit: Unit, directory: Path | str) -> None:
        root = Path(directory)
        self._unit = unit
        self.in_dir = root / "in"
        self.out_dir = root / "out"
        self.done_dir = root / "processed"
        for path in (self.in_dir, self.out_dir, self.done_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- the loop ----------------------------------------------------------

    def poll(self) -> tuple[FileDropResult, ...]:
        """Process every request document waiting right now, in name order.

        Name order, and sorted by code point, because a batch is a sequence: a
        drop containing ``01.create``, ``02.pay`` must be answered in that
        order or the second document fails on state the first was going to
        make. The reference sorts too; this one says why.
        """
        results: list[FileDropResult] = []
        for path in sorted(p for p in self.in_dir.iterdir() if p.name.endswith(REQUEST_SUFFIX)):
            results.append(self._process(path))
        return tuple(results)

    def start(self, interval_ms: float = DEFAULT_INTERVAL_MS) -> None:
        """Poll on an interval until :meth:`stop`. Idempotent."""
        if self._thread is not None:
            return
        self._stop.clear()
        thread = threading.Thread(
            target=self._loop, args=(interval_ms / 1000.0,), name="vendorfake-filedrop", daemon=True
        )
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop polling and **wait** for the current pass to finish.

        Joins rather than merely signalling: a caller that stops the binding
        and then deletes its directory must not race a write. A fake must not
        outlive the test that built it.
        """
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout)

    def _loop(self, interval: float) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(interval)

    # -- one document ------------------------------------------------------

    def _process(self, path: Path) -> FileDropResult:
        name = path.name[: -len(REQUEST_SUFFIX)]
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return self._answer(path, name, _malformed(f"request document is not readable JSON: {exc}"))
        if not isinstance(document, Mapping):
            return self._answer(path, name, _malformed("request document must be a JSON object"))
        method = document.get("method")
        route_path = document.get("path")
        if not isinstance(method, str) or not isinstance(route_path, str):
            return self._answer(path, name, _malformed("request document needs string 'method' and 'path'"))

        headers = {str(k).lower(): str(v) for k, v in dict(document.get("headers") or {}).items()}
        query = {str(k): str(v) for k, v in dict(document.get("query") or {}).items()}
        raw_body = document.get("raw_body")
        body = document.get("body")

        request = make_request(
            method=method,
            path=route_path,
            query=query,
            headers=headers,
            body=body,
            raw_body=raw_body if isinstance(raw_body, str) else None,
            transport=TRANSPORT,
            # A document may carry its own id so a batch can be correlated with
            # whatever produced it; `make_request` also honours the header.
            request_id=str(document["id"]) if isinstance(document.get("id"), str) else None,
        )
        response = self._unit.handle(request)
        if response.delay_ms > 0:
            # The kernel decides *whether* to delay; each binding decides how,
            # in the terms of the caller it is holding. Here the caller is a
            # collector polling the `out` directory, so the delay is the gap
            # before the response document appears -- which is exactly what a
            # partner integration measures.
            #
            # Waited on the stop event rather than with `time.sleep`, so the
            # wait is interruptible: `stop()` joins with a five-second bound,
            # and a bare sleep for a longer delay would make a shutdown look
            # like a hang. Nothing here fires the event, so an uninterrupted
            # wait lasts exactly `delay_ms`.
            self._stop.wait(response.delay_ms / 1000.0)
        return self._answer(
            path,
            name,
            {
                "status": response.status,
                "headers": dict(response.headers),
                "body": _parsed(decode_body(response)),
            },
        )

    def _answer(self, request_path: Path, name: str, payload: dict[str, Any]) -> FileDropResult:
        """Write the response, then move the request aside.

        In that order: a collector that saw the request disappear before the
        response existed would have no way to tell "still running" from
        "answered and lost".
        """
        target = self.out_dir / f"{name}{RESPONSE_SUFFIX}"
        # Written under a temporary name and renamed, so a collector polling
        # `out` can never read a half-written document. `rename` within one
        # directory is atomic on every filesystem this runs on.
        staging = self.out_dir / f".{name}.{uuid.uuid4().hex}.tmp"
        staging.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        staging.replace(target)
        moved = self.done_dir / request_path.name
        request_path.replace(moved)
        status = payload["status"]
        return FileDropResult(
            name=name,
            status=status if isinstance(status, int) else 0,
            request_path=moved,
            response_path=target,
        )


def serve_file_drop(unit: Unit, directory: Path | str) -> FileDrop:
    """Bind ``unit`` to ``directory``, creating the three subdirectories."""
    return FileDrop(unit, directory)


def _parsed(text: str) -> Any:
    """The response body as JSON where it is JSON, as text where it is not.

    A file-drop consumer reads these documents with an ordinary JSON parser, so
    embedding the body as a string would make every field a second parse away.
    A body that is not JSON -- a redirect's zero bytes, a vendor that answers
    XML -- is kept as the text it was rather than dropped.
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _malformed(detail: str) -> dict[str, Any]:
    """The answer for a document the binding could not turn into a request.

    Shaped like every other error the unit produces, ``x-unit-error`` included,
    because a consumer's collector should not need a second code path for
    "the drop itself was wrong".
    """
    return {
        "status": 400,
        "headers": {"content-type": "application/json", "x-unit-error": "invalid_value"},
        "body": {"error": {"code": "invalid_value", "detail": detail}},
    }
