"""The error table: exhaustive, provenance-labelled, and two body shapes.

The envelope is a JUDGMENT call this vendor could not avoid -- Lightspeed
publishes none -- so these tests pin what was chosen and, where the vendor DOES
declare a shape, that the declared one is what goes out.
"""

from __future__ import annotations

import pytest

from tests.unit.lightspeed.conftest import fake_ctx
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.lightspeed.errors import (
    CATALOGUE_RETRY_AFTER,
    LIGHTSPEED_ERROR_TABLE,
    ONE_MEMBER_BODY_INFO_KEY,
    RATE_LIMITED_MESSAGE,
    RATE_LIMITED_TITLE,
    RETRY_AFTER_HEADER,
    LightspeedErrorShaper,
    http_date,
)


def test_the_table_covers_every_core_error_kind() -> None:
    assert set(LIGHTSPEED_ERROR_TABLE) == set(UnitErrorKind)


def test_every_row_states_where_its_status_came_from() -> None:
    for kind, mapping in LIGHTSPEED_ERROR_TABLE.items():
        assert mapping.provenance in ("documented", "judgment"), kind
        assert 400 <= mapping.status < 600, kind
        assert mapping.title and mapping.message, kind


def test_the_documented_rows_are_the_ones_the_vendor_really_declares() -> None:
    """404 on the three /webhooks/{id} routes, 409 on POST /webhooks, 429 on the
    rate-limiting page, and 401 as the document's global authentication
    failure. Everything else is this project's choice and says so."""
    documented = {kind for kind, row in LIGHTSPEED_ERROR_TABLE.items() if row.provenance == "documented"}
    assert documented == {
        UnitErrorKind.NOT_FOUND,
        UnitErrorKind.CONFLICT,
        UnitErrorKind.RATE_LIMITED,
        UnitErrorKind.UNAUTHORIZED,
        UnitErrorKind.TOKEN_EXPIRED,
    }


def test_the_generalised_body_has_the_two_documented_members() -> None:
    shaper = LightspeedErrorShaper()
    shaped = shaper.shape(UnitError(UnitErrorKind.NOT_FOUND, detail="Outlet x was not found."), fake_ctx())
    assert shaped.status == 404
    assert shaped.body["error"] == "Not Found"
    assert shaped.body["message"] == "Outlet x was not found."


def test_the_rate_limited_body_is_the_documented_one_verbatim() -> None:
    shaper = LightspeedErrorShaper()
    shaped = shaper.shape(UnitError(UnitErrorKind.RATE_LIMITED), fake_ctx())
    assert shaped.status == 429
    assert shaped.body["error"] == RATE_LIMITED_TITLE == "Too Many Requests"
    assert shaped.body["message"] == RATE_LIMITED_MESSAGE == "Rate limiting enforced"


def test_retry_after_is_an_http_date_not_seconds() -> None:
    """DOCUMENTED: ``Retry-After: Wed, 15 Jul 2020 15:04:05 GMT`` -- an
    absolute instant, not a delta. A consumer parsing an integer fails here."""
    shaper = LightspeedErrorShaper()
    shaped = shaper.shape(UnitError(UnitErrorKind.RATE_LIMITED, info={"retry_after_ms": 120_000}), fake_ctx())
    assert shaped.headers[RETRY_AFTER_HEADER] == "Fri, 04 Sep 2026 12:02:00 GMT"
    assert "Retry-After" not in shaped.headers


def test_the_catalogue_pins_retry_after_so_two_reads_agree() -> None:
    """C10 compares the two bindings byte for byte on ``GET /__unit/errors``; a
    live retry instant would move whenever a read crossed a second."""
    shaper = LightspeedErrorShaper()
    shaped = shaper.shape(UnitError(UnitErrorKind.RATE_LIMITED), fake_ctx(), describing=True)
    assert shaped.headers[RETRY_AFTER_HEADER] == CATALOGUE_RETRY_AFTER


def test_the_switch_turns_retry_after_off() -> None:
    shaper = LightspeedErrorShaper(retry_after_header=False)
    shaped = shaper.shape(UnitError(UnitErrorKind.RATE_LIMITED), fake_ctx())
    assert RETRY_AFTER_HEADER not in shaped.headers


def test_the_webhooks_shape_is_the_one_the_vendor_declares() -> None:
    """``POST /webhooks``' 409 and the three 404s declare ``{"error": <string>}``
    -- one member, not the two the rest of this package sends."""
    shaper = LightspeedErrorShaper()
    shaped = shaper.shape(
        UnitError(
            UnitErrorKind.CONFLICT,
            detail="A webhook with this type and URL already exists.",
            info={ONE_MEMBER_BODY_INFO_KEY: True},
        ),
        fake_ctx(error_sidecar_mode="headers"),
    )
    assert shaped.status == 409
    assert shaped.body == {"error": "A webhook with this type and URL already exists."}


def test_describe_publishes_the_whole_table() -> None:
    described = LightspeedErrorShaper().describe()
    assert set(described) == {kind.value for kind in UnitErrorKind}
    assert described["rate_limited"]["provenance"] == "documented"
    assert described["forbidden_scope"]["provenance"] == "judgment"


def test_not_found_names_the_control_route_that_lists_the_surface() -> None:
    from vendorfake.core.kernel.types import UnitRequest

    request = UnitRequest(
        id="req_1",
        method="GET",
        path="/api/2026-07/nope",
        query={},
        headers={},
        raw_body=b"",
        transport="inprocess",
        received_at="2026-09-04T12:00:00.000Z",
    )
    shaped = LightspeedErrorShaper().not_found(request, fake_ctx())
    assert shaped.status == 404
    assert "GET /__unit/routes" in shaped.body["message"]


@pytest.mark.parametrize(
    ("epoch_ms", "expected"),
    [
        (0, "Thu, 01 Jan 1970 00:00:00 GMT"),
        (1_594_825_445_000, "Wed, 15 Jul 2020 15:04:05 GMT"),
    ],
)
def test_http_date_matches_the_documented_example(epoch_ms: int, expected: str) -> None:
    """The second row is the page's own example value, to the second."""
    assert http_date(epoch_ms) == expected
