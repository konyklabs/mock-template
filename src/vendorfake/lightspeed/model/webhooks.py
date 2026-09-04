"""The webhook vocabulary: the subscription's wire shape, its request body, and
the three fields an outbound delivery carries.

DOCUMENTED (``Webhook``, ``WebhookRequest`` and
https://x-series-api.lightspeedhq.com/docs/webhooks):

* a webhook is ``{active, id, retailer_id, type, url}``;
* ``POST``/``PUT`` take ``{active, type, url}``, all three REQUIRED, with
  ``url`` carrying ``minLength: 3``;
* ``type`` is the seven-value ``WebhookType`` enum;
* ``POST`` answers 409 "A webhook with this type and URL already exists", so
  the pair is the uniqueness key -- not the URL alone, and not the type alone;
* an outbound delivery is form-encoded with ``payload`` required and
  ``environment`` and ``domain_prefix`` "not guaranteed to be" present.

JUDGMENT, labelled: the ``environment`` value. The page names the field and no
value for it. ``production`` is what a real retailer's deliveries come from and
is the default; it is a config field so a consumer who needs another can set
one, rather than this package inventing a fake-specific string a consumer might
switch on.
"""

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
"""DOCUMENTED as optional -- "may be present but are not guaranteed to be".
This unit sends both."""

URL_MIN_LENGTH = 3
"""``WebhookRequest.url``'s documented ``minLength``."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)


class WebhookRequest(BaseModel):
    """``{active, type, url}``, all three required.

    ``type`` is validated against the enum by the surface rather than by a
    ``Literal`` here, so the refusal can name the seven legal values in the
    vendor's own order instead of Pydantic's rendering of a union.
    """

    model_config = _REQUEST

    active: bool
    type: str = Field(min_length=1)
    url: str = Field(min_length=URL_MIN_LENGTH)


def project_webhook(entity: Mapping[str, Any], *, retailer_id: str) -> dict[str, Any]:
    """The documented ``Webhook`` document.

    The subscription is stored in the CORE's subscription collection, whose
    field names are the core's (``notification_url``, ``enabled``,
    ``event_types``); this is the one place that vocabulary is translated into
    Lightspeed's (``url``, ``active``, ``type``). ``retailer_id`` is passed in
    because a subscription entity has no idea which retailer it belongs to --
    a unit serves exactly one.
    """
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
    """The closure a ``register_closure.create`` delivery carries.

    There is no REST resource for a register closure anywhere in the 135
    documented paths, so there is no documented shape to reproduce and this is
    JUDGMENT throughout. The members are the ones the vendor DOES print for a
    closure, in ``GET /registers/{id}/payments_summary``'s own example --
    ``register_closure_id``, ``register_closure_sequence_number``,
    ``register_open_time`` and the per-payment-type ``payments`` totals --
    plus the ``register_id`` and ``outlet_id`` that say which till closed and
    the ``register_close_time`` that says when, without which a subscriber
    could not act on the event at all.
    """
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
