"""The webhook wire vocabulary: the documented payload, and the dashboard
stand-in's request/response shapes.

DOCUMENTED payload shape, verbatim (https://docs.clover.com/dev/docs/webhooks):
``{"appId": ..., "merchants": {"<id>": [{"objectId": ..., "type": "CREATE",
"ts": ...}]}}``. JUDGMENT: Clover has no subscription API (dashboard-
configured), so the register/verify shapes below are this project's stand-in.
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
"""Strict on the way out: a wrong type is this unit's own defect."""

_REQUEST = ConfigDict(extra="ignore", frozen=True)
"""Lax on the way in, per the package convention on ``model/oauth.py``."""

ALL_EVENT_KEYS: tuple[str, ...] = ("O", "I", "C", "P")
"""Documented keys this unit emits, in documented order; ``E``/``M`` exist but
nothing here mutates them, so subscribing to them is refused."""


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
    """``POST /__clover/webhooks/subscriptions``. ``eventKeys`` defaults to
    every key; a subscription to none at all is refused."""

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
        # Documented order, deduplicated.
        return tuple(key for key in ALL_EVENT_KEYS if key in keys)


class VerifySubscriptionRequest(BaseModel):
    """``POST /__clover/webhooks/verify``: the code the callback received."""

    model_config = _REQUEST

    verificationCode: str = Field(min_length=1)


class SubscriptionWire(BaseModel):
    """One subscription as reported. ``authCode`` appears only once verified
    (JUDGMENT: the control plane's introspection endpoint returns it always)."""

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
