"""The loyalty wire vocabulary: program, account and event projections, and
the three request shapes.

Shapes from
https://developer.squareup.com/reference/square/objects/LoyaltyProgram,
https://developer.squareup.com/reference/square/objects/LoyaltyProgramAccrualRule,
https://developer.squareup.com/reference/square/objects/LoyaltyAccount,
https://developer.squareup.com/reference/square/objects/LoyaltyAccountMapping and
https://developer.squareup.com/reference/square/objects/LoyaltyEvent.

INVARIANT: **an absent optional emits no key**, through ``compact()``. An
account never accrued against has no ``expiring_point_deadlines``; an event
that was not for an order has no ``order_id`` inside ``accumulate_points``.

Phone numbers are E.164
-----------------------
"phone_number: The phone number of the buyer, in E.164 format. For example,
``+14155551111``." E.164 is a leading ``+``, a non-zero first digit and at
most fifteen digits in all; :data:`E164` is that rule and nothing narrower. A
number with spaces, dashes or a national prefix is refused naming the field,
because a fake that normalised it would teach a consumer that Square does.

SHRINK (prototype): ``expiration_policy``, ``expiring_point_deadlines``, the
``VISIT`` / ``ITEM_VARIATION`` / ``CATEGORY`` accrual types, rewards and
promotions are not modelled; the projection emits what a single-SPEND-rule
program carries and no more.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.square.entities import LoyaltyAccountEntity, LoyaltyEventEntity, LoyaltyProgramEntity

__all__ = [
    "ACCUMULATE_POINTS",
    "E164",
    "MAIN_PROGRAM",
    "SEARCH_DEFAULT_LIMIT",
    "SEARCH_MAX_LIMIT",
    "AccumulateLoyaltyPointsRequest",
    "AccumulatePointsRequest",
    "CreateLoyaltyAccountRequest",
    "LoyaltyAccountMappingRequest",
    "LoyaltyAccountSpecRequest",
    "LoyaltyAccountsSearchQueryRequest",
    "SearchLoyaltyAccountsRequest",
    "project_loyalty_account",
    "project_loyalty_event",
    "project_loyalty_program",
]

MAIN_PROGRAM = "main"
""""The ID of the loyalty program or the keyword `main`. Either value can be
used to retrieve the single loyalty program that belongs to the seller."
https://developer.squareup.com/reference/square/loyalty-api/retrieve-loyalty-program
"""

ACCUMULATE_POINTS = "ACCUMULATE_POINTS"
"""The one ``LoyaltyEventType`` this unit mints.
https://developer.squareup.com/reference/square/enums/LoyaltyEventType"""

E164 = re.compile(r"^\+[1-9]\d{1,14}$")
"""E.164, as the mapping documents it. See the module docstring."""

SEARCH_DEFAULT_LIMIT = 30
SEARCH_MAX_LIMIT = 200
"""SearchLoyaltyAccounts ``limit``: "The maximum number of results to include
in the response. The default value is 30." and "Min 1, Max 200".
https://developer.squareup.com/reference/square/loyalty-api/search-loyalty-accounts
"""

_REQUEST = ConfigDict(extra="ignore", frozen=True, strict=True)


# ---------------------------------------------------------------------------
# Projections.
# ---------------------------------------------------------------------------


def project_loyalty_program(entity: Mapping[str, Any]) -> dict[str, Any]:
    """A stored program as Square's ``LoyaltyProgram``, in the documented order."""
    program = LoyaltyProgramEntity.from_entity(entity)
    return compact(
        {
            "id": program.id,
            "status": program.status,
            "reward_tiers": [dict(tier) for tier in program.reward_tiers],
            "terminology": {"one": program.terminology_one, "other": program.terminology_other},
            "location_ids": list(program.location_ids),
            "created_at": _opt_str(entity.get("created_at")),
            "updated_at": _opt_str(entity.get("updated_at")),
            "accrual_rules": [
                {
                    "accrual_type": "SPEND",
                    "points": program.accrual_points,
                    "spend_data": {
                        "amount_money": program.spend_amount.to_entity(),
                        "excluded_category_ids": [],
                        "excluded_item_variation_ids": [],
                        "tax_mode": program.tax_mode,
                    },
                }
            ],
        }
    )


def project_loyalty_account(entity: Mapping[str, Any]) -> dict[str, Any]:
    """A stored account as Square's ``LoyaltyAccount``."""
    account = LoyaltyAccountEntity.from_entity(entity)
    return compact(
        {
            "id": account.id,
            "program_id": account.program_id,
            "balance": account.balance,
            "lifetime_points": account.lifetime_points,
            "customer_id": account.customer_id,
            "enrolled_at": account.enrolled_at or None,
            "created_at": _opt_str(entity.get("created_at")),
            "updated_at": _opt_str(entity.get("updated_at")),
            "mapping": compact(
                {
                    "id": account.mapping_id,
                    "created_at": account.mapping_created_at or None,
                    "phone_number": account.phone_number,
                }
            ),
        }
    )


def project_loyalty_event(entity: Mapping[str, Any]) -> dict[str, Any]:
    """A stored event as Square's ``LoyaltyEvent``."""
    event = LoyaltyEventEntity.from_entity(entity)
    return compact(
        {
            "id": event.id,
            "type": event.type,
            "created_at": _opt_str(entity.get("created_at")),
            "accumulate_points": compact(
                {
                    "loyalty_program_id": event.program_id,
                    "points": event.points,
                    "order_id": event.order_id,
                }
            ),
            "loyalty_account_id": event.account_id,
            "location_id": event.location_id,
            "source": event.source,
        }
    )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Requests.
# ---------------------------------------------------------------------------


class LoyaltyAccountMappingRequest(BaseModel):
    """``mapping`` -- a phone number, in E.164, checked by the surface."""

    model_config = _REQUEST

    phone_number: str | None = None


class LoyaltyAccountSpecRequest(BaseModel):
    """``loyalty_account`` on CreateLoyaltyAccount: "program_id: The Square-
    assigned ID of the loyalty program" and the mapping to enrol under.
    https://developer.squareup.com/reference/square/loyalty-api/create-loyalty-account
    """

    model_config = _REQUEST

    program_id: str = Field(min_length=1)
    mapping: LoyaltyAccountMappingRequest | None = None
    customer_id: str | None = None


class CreateLoyaltyAccountRequest(BaseModel):
    """``POST /v2/loyalty/accounts``. ``idempotency_key`` is required ("Min
    Length 1, Max Length 128") and read by the kernel."""

    model_config = _REQUEST

    loyalty_account: LoyaltyAccountSpecRequest
    idempotency_key: str | None = Field(default=None, max_length=128)


class LoyaltyAccountsSearchQueryRequest(BaseModel):
    """``query``: "mappings: The set of mappings to use in the loyalty account
    search. This cannot be combined with `customer_ids`." and the mirror
    sentence on ``customer_ids``. Both "Max: 30".
    https://developer.squareup.com/reference/square/objects/SearchLoyaltyAccountsRequestLoyaltyAccountQuery
    """

    model_config = _REQUEST

    mappings: list[LoyaltyAccountMappingRequest] | None = Field(default=None, max_length=30)
    customer_ids: list[str] | None = Field(default=None, max_length=30)


class SearchLoyaltyAccountsRequest(BaseModel):
    """``POST /v2/loyalty/accounts/search``.
    https://developer.squareup.com/reference/square/loyalty-api/search-loyalty-accounts
    """

    model_config = _REQUEST

    query: LoyaltyAccountsSearchQueryRequest | None = None
    limit: int | None = None
    cursor: str | None = None


class AccumulatePointsRequest(BaseModel):
    """``accumulate_points``: "order_id: The ID of the order for which to
    accumulate the points ... points: The number of points to add to the
    account. Specify this field only when the points are not calculated by
    Square from an order." Exactly one of the two.
    https://developer.squareup.com/reference/square/objects/LoyaltyEventAccumulatePoints
    """

    model_config = _REQUEST

    order_id: str | None = None
    points: int | None = None


class AccumulateLoyaltyPointsRequest(BaseModel):
    """``POST /v2/loyalty/accounts/{account_id}/accumulate``. Both
    ``idempotency_key`` ("Min Length 1, Max Length 128") and ``location_id``
    are required.
    https://developer.squareup.com/reference/square/loyalty-api/accumulate-loyalty-points
    """

    model_config = _REQUEST

    accumulate_points: AccumulatePointsRequest
    idempotency_key: str | None = Field(default=None, max_length=128)
    location_id: str = Field(min_length=1)
