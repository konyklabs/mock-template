"""The partners API vocabulary: the connected-restaurant row and its page envelope.

DOCUMENTED (toast-partners-api.yaml v1.0.2,
https://doc.toasttab.com/doc/devguide/apiPartnersGettingAccessibleRestaurants.html):

* ``GET /partners/v1/connectedRestaurants?lastModified&pageSize&pageToken``
  answers ``{currentPageNum, results[], totalResultCount, pageSize,
  currentPageToken, nextPageToken, totalCount, nextPageNum, lastPageNum,
  previousPageNum}``; ``pageSize`` default 100, maximum 200;
* ``GET /partners/v1/restaurants?lastModified`` answers the bare array;
* a row is ``{restaurantGuid, managementGroupGuid (null if none),
  restaurantName, locationName, createdByEmailAddress, externalGroupRef,
  externalRestaurantRef, modifiedDate (epoch ms), createdDate,
  isoModifiedDate, isoCreatedDate, deleted, scopes}``.

JUDGMENT: the ``iso*`` spellings use the REST date format; the page shows the
field names and no values. Page numbers count from 1 and the tokens are the
core's opaque cursors (a real token's format is undocumented).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
