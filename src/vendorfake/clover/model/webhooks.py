"""The webhook wire vocabulary: the documented payload, and the dashboard
stand-in's request and response shapes.

FOR: stating the documented notification payload once, as models, so that the
event mapper composes a document rather than a nested dictionary literal, and
so that key order -- which is the order the documentation shows and the order
the delivered bytes carry -- is decided in one place.

THE PAYLOAD is DOCUMENTED, verbatim on https://docs.clover.com/dev/docs/webhooks::

    {"appId":"DRKVJT2ZRRRSC",
     "merchants":{"XYZVJT2ZRRRSC":[{"objectId":"O:GHIVJT2ABCRSC","type":"CREATE","ts":1537970958000}]}}

``tests/unit/clover/test_events.py`` reproduces those exact bytes through the
mapper and :func:`~vendorfake.core.util.json.dump_json`.

THE SUBSCRIPTION SHAPES are JUDGMENT, all of them. Clover has no subscription
API -- webhooks are configured in the developer dashboard -- so the request a
consumer sends to register a callback with this fake, and the record it gets
back, are this project's stand-in for that dashboard: a callback URL and the
event keys to subscribe to, camelCase like every Clover field, with the
documented verification and auth codes attached once they exist. Nothing here
is a Clover wire shape and a consumer must not build against it as one; the
``__clover`` path prefix on the routes says the same thing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vendorfake.core.util.json import compact

__all__ = [
    "ALL_EVENT_KEYS",
    "EventWire",
    "PayloadWire",
    "RegisterSubscriptionRequest",
    "SubscriptionWire",
    "VerifySubscriptionRequest",
]

_WIRE = ConfigDict(extra="forbid", frozen=True, strict=True)
"""Strict on the way out: a value this unit produced with the wrong type is a
defect here, and coercing it would hide one."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)
"""Lax on the way in, per the package convention on ``model/oauth.py``."""

ALL_EVENT_KEYS: tuple[str, ...] = ("O", "I", "C", "P")
"""The documented event keys this unit can emit, in the documented order:
orders, inventory, customers, payments. ``E`` (employees) and ``M``
(merchants) are documented keys nothing here mutates, so subscribing to them
is refused rather than accepted and never honoured."""


class EventWire(BaseModel):
    """One event in a merchant's list: ``{objectId, type, ts}``."""

    model_config = _WIRE

    objectId: str
    type: str
    #: Unix milliseconds, documented (``1537970958000``).
    ts: int

    def wire(self) -> dict[str, Any]:
        return {"objectId": self.objectId, "type": self.type, "ts": self.ts}


class PayloadWire(BaseModel):
    """The whole delivered document: ``{appId, merchants: {<mId>: [event]}}``."""

    model_config = _WIRE

    appId: str
    merchants: dict[str, list[EventWire]]

    def wire(self) -> dict[str, Any]:
        return {
            "appId": self.appId,
            "merchants": {merchant: [event.wire() for event in events] for merchant, events in self.merchants.items()},
        }


class RegisterSubscriptionRequest(BaseModel):
    """``POST /__clover/webhooks/subscriptions``: a callback and its keys.

    ``eventKeys`` defaults to every key, which is what an operator who ticks
    nothing in the dashboard would most plausibly expect from a fake; a
    subscription to no keys at all is refused because it could never deliver.
    """

    model_config = _REQUEST

    url: str = Field(min_length=1)
    eventKeys: tuple[str, ...] = ALL_EVENT_KEYS

    @field_validator("eventKeys")
    @classmethod
    def _known_keys(cls, keys: tuple[str, ...]) -> tuple[str, ...]:
        if not keys:
            raise ValueError("must name at least one event key")
        unknown = [key for key in keys if key not in ALL_EVENT_KEYS]
        if unknown:
            raise ValueError(f"unknown event key(s) {unknown}; this unit emits {list(ALL_EVENT_KEYS)}")
        # Documented order, deduplicated, so the record reads the same however
        # the request spelled it.
        return tuple(key for key in ALL_EVENT_KEYS if key in keys)


class VerifySubscriptionRequest(BaseModel):
    """``POST /__clover/webhooks/verify``: the code the callback received."""

    model_config = _REQUEST

    verificationCode: str = Field(min_length=1)


class SubscriptionWire(BaseModel):
    """One subscription as the stand-in reports it.

    ``authCode`` is present only once the callback is verified, because that
    is when the documented flow hands it out. This is a property of the
    stand-in's projection and nothing stronger: the code is the core's
    ``signature_key``, and ``GET /__unit/webhooks/subscriptions`` -- the open
    control plane -- returns that verbatim for every subscriber, pending or
    not. A consumer who wants to skip the handshake can; the stand-in simply
    does not hand them the shortcut in the flow it models.
    """

    model_config = _WIRE

    id: str
    url: str
    eventKeys: list[str]
    verified: bool
    authCode: str | None = None

    def wire(self) -> dict[str, Any]:
        return compact(
            {
                "id": self.id,
                "url": self.url,
                "eventKeys": list(self.eventKeys),
                "verified": self.verified,
                "authCode": self.authCode,
            }
        )
