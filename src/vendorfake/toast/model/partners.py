"""The partners API vocabulary: the connected-restaurant row and its page envelope.

DOCUMENTED (toast-partners-api.yaml v1.0.2,
https://doc.toasttab.com/doc/devguide/apiPartnersGettingAccessibleRestaurants.html):
the connected-restaurants page
envelope, with ``pageSize`` default 100 and maximum 200; a row's field names,
including the ``iso*`` date pair.

JUDGMENT: the ``iso*`` spellings use the REST date format, since the page
shows field names and no values. Page numbers count from 1 and the tokens
are the core's opaque cursors (a real token's format is undocumented).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.toast.model.dates import rest_date

__all__ = ["DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE", "page_envelope", "project_connected_restaurant"]

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200


def project_connected_restaurant(entity: Mapping[str, Any], scopes: Sequence[str]) -> dict[str, Any]:
    modified = int(entity.get("modifiedDate", 0))
    created = int(entity.get("createdDate", 0))
    return {
        "restaurantGuid": str(entity["id"]),
        "managementGroupGuid": entity.get("managementGroupGuid"),
        "restaurantName": entity.get("restaurantName"),
        "locationName": entity.get("locationName"),
        "createdByEmailAddress": entity.get("createdByEmailAddress"),
        "externalGroupRef": entity.get("externalGroupRef"),
        "externalRestaurantRef": entity.get("externalRestaurantRef"),
        "modifiedDate": modified,
        "createdDate": created,
        "isoModifiedDate": rest_date(modified),
        "isoCreatedDate": rest_date(created),
        "deleted": bool(entity.get("deleted", False)),
        "scopes": list(scopes),
    }


def page_token(page_number: int, page_size: int) -> str:
    """JUDGMENT: base64 of ``p=<page>,s:<size>`` -- the guide's only example
    (``cD0xLHM6MTAw``, first page at size 100) is treated as the format."""
    return base64.b64encode(f"p={page_number},s:{page_size}".encode()).decode()


def parse_page_token(token: str, *, page_size: int) -> int:
    """The page a token names, or ``invalid_cursor`` when it is not one of ours
    or was minted for a different page size."""
    try:
        decoded = base64.b64decode(token.encode(), validate=True).decode()
        page_part, size_part = decoded.split(",", 1)
        page_number = int(page_part.removeprefix("p="))
        size = int(size_part.removeprefix("s:"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnitError(
            UnitErrorKind.INVALID_CURSOR, detail="pageToken is not a token this API issued.", field="pageToken"
        ) from exc
    if page_number < 1 or size != page_size:
        raise UnitError(
            UnitErrorKind.INVALID_CURSOR, detail="pageToken was issued for a different pageSize.", field="pageToken"
        )
    return page_number


def page_envelope(
    results: list[dict[str, Any]],
    *,
    total: int,
    page_size: int,
    page_number: int,
    current_token: str | None,
    next_token: str | None,
) -> dict[str, Any]:
    last_page = max(1, -(-total // page_size))
    if current_token is None:
        current_token = page_token(page_number, page_size)
    return {
        "currentPageNum": page_number,
        "results": results,
        "totalResultCount": len(results),
        "pageSize": page_size,
        "currentPageToken": current_token,
        "nextPageToken": next_token,
        "totalCount": total,
        "nextPageNum": page_number + 1 if next_token is not None else None,
        "lastPageNum": last_page,
        "previousPageNum": page_number - 1 if page_number > 1 else None,
    }
