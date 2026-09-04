"""The webhook vocabulary: the subscription's wire shape, its request body,
and the three fields an outbound delivery carries.
DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/webhooks): a webhook
is ``{active, id, retailer_id, type, url}``; ``POST``/``PUT`` require all
three of ``{active, type, url}``; ``POST`` answers 409 on a duplicate
``(type, url)`` pair; a delivery is form-encoded with ``payload`` required.
JUDGMENT: ``environment`` defaults to ``production`` (named but no value
given)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.lightspeed.entities import OBJECT_VERSION, RegisterClosureEntity

__all__ = [
    "DOMAIN_PREFIX_FIELD",
    "ENVIRONMENT_FIELD",
    "PAYLOAD_FIELD",
    "URL_MIN_LENGTH",
    "WebhookRequest",
    "project_register_closure",
    "project_webhook",
]

PAYLOAD_FIELD = "payload"
"""DOCUMENTED and required: "JSON-encoded object with entity details"."""

DOMAIN_PREFIX_FIELD = "domain_prefix"
ENVIRONMENT_FIELD = "environment"
"""DOCUMENTED as optional; this unit sends both."""

URL_MIN_LENGTH = 3
"""``WebhookRequest.url``'s documented ``minLength``."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class WebhookRequest(BaseModel):
    """``{active, type, url}``, all three required. ``type`` is validated
    against the enum by the surface, not a ``Literal`` here, so refusals can
    name the vendor's seven legal values in order."""

    model_config = _REQUEST

    active: bool
    type: str = Field(min_length=1)
    url: str = Field(min_length=URL_MIN_LENGTH)


def project_webhook(entity: Mapping[str, Any], *, retailer_id: str) -> dict[str, Any]:
    """The documented ``Webhook`` document; ``retailer_id`` is passed in since
    a subscription entity carries none."""
    event_types = entity.get("event_types")
    first = str(event_types[0]) if isinstance(event_types, list) and event_types else ""
    return {
        "id": str(entity["id"]),
        "retailer_id": retailer_id,
        "type": first,
        "url": str(entity.get("notification_url", "")),
        "active": entity.get("enabled", True) is not False,
    }


def project_register_closure(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The closure a ``register_closure.create`` delivery carries. JUDGMENT
    throughout -- no REST resource for a register closure exists."""
    closure = RegisterClosureEntity.from_entity(entity)
    return compact(
        {
            "register_closure_id": closure.id,
            "register_closure_sequence_number": closure.sequence_number,
            "register_id": closure.register_id,
            "outlet_id": closure.outlet_id,
            "register_open_time": closure.register_open_time,
            "register_close_time": closure.register_close_time,
            "payments": list(closure.payments),
            "version": entity.get(OBJECT_VERSION, 0),
        }
    )
