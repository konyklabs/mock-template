"""Fixtures for the transport-adapter tests: one unit, one app, one tripwire.

The three are built together because the wiring between them is the thing under
test: the tripwire has to exist before the unit so the control plane can close
over it, and the app has to be handed the same object the unit reports.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from typing import Any

import pytest

from tests.fakes import make_unit, route
from vendorfake.asgi import FrameworkTripwire, create_app
from vendorfake.core.control.plane import control_plane_routes
from vendorfake.core.kernel.reply import json_, text


def _show(args: Any) -> Any:
    """A vendor route that reports what the adapter handed the core.

    Everything this returns is a fact the transport had to carry correctly:
    the path parameter proves segmentation, the query proves the last-wins
    collapse, the header proves the join, and the raw length proves the body
    arrived as bytes and was not re-encoded on the way.
    """
    return json_(
        {
            "order_id": args.params.get("order_id"),
            "method": args.req.method,
            "path": args.req.path,
            "query": dict(args.req.query),
            "headers": dict(args.req.headers),
            "raw_len": len(args.req.raw_body),
            "transport": args.req.transport,
        }
    )


def _plain(args: Any) -> Any:
    return text("plain body")


def _stable(args: Any) -> Any:
    """A body that depends on nothing about the request.

    The byte-for-byte comparison between bindings needs a vendor route whose
    answer is identical by construction; ``_show`` deliberately reports the
    transport, so it can never be one.
    """
    return json_({"stable": True, "items": [1, 2, 3], "note": "smørrebrød"})


@pytest.fixture
def tripwire() -> FrameworkTripwire:
    return FrameworkTripwire()


@pytest.fixture
def unit(tripwire: FrameworkTripwire) -> Iterator[Any]:
    built = make_unit(
        [
            route("GET", "/v2/orders/{order_id}", _show),
            route("POST", "/v2/orders/{order_id}", _show),
            route("GET", "/v2/plain", _plain),
            route("GET", "/v2/stable", _stable),
        ],
        control_routes=functools.partial(control_plane_routes, framework_answered=tripwire.get),
    )
    try:
        yield built
    finally:
        built.stop()


@pytest.fixture
def app(unit: Any, tripwire: FrameworkTripwire) -> Any:
    return create_app(unit, tripwire=tripwire)
