"""What a delivery is, stated without naming a vendor.

**The core sends no delivery headers of its own.** Every outbound header comes back from
:class:`DeliveryHeaderProvider`, which the vendor's signer implements. A vendor's
vocabulary also leaks in strings carrying no slug for ``tools/boundary_check.py`` to
catch, so an outcome is a neutral :class:`DeliveryOutcome` here and the map to wire
strings lives in the vendor package.
"""

from __future__ import annotations

import copy
import ipaddress
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from vendorfake.core.kernel.types import PreparedEvent, UnitError, UnitErrorKind

__all__ = [
    "SUBSCRIPTION_COLLECTION",
    "BodyEncodingSigner",
    "DeliveryHeaderProvider",
    "DeliveryMetadata",
    "DeliveryOutcome",
    "DeliveryRecord",
    "DeliveryStatus",
    "Subscription",
    "check_notification_url",
    "matches_event_type",
    "require_postable_target",
]

_REGEX_METACHARACTERS = frozenset(".+?^${}()|[]\\")
"""Escaped by :func:`matches_event_type`; ``*`` is absent, being the one it keeps."""

SUBSCRIPTION_COLLECTION = "subscriptions"
"""The store collection subscriptions live in; the journal listener ignores it, so
registering a subscriber emits no event."""


class DeliveryOutcome(StrEnum):
    """Why an attempt was not accepted, in core's own words; a vendor maps these onto
    whatever strings its documentation publishes."""

    TIMEOUT = "timeout"
    #: The send failed before a status existed: connection refused, DNS, TLS.
    TRANSPORT_ERROR = "transport_error"
    HTTP_ERROR = "http_error"

    @classmethod
    def of(cls, status: int, *, timed_out: bool) -> DeliveryOutcome:
        """Classify one sink result; ``timed_out`` first, since a timeout is status ``0`` too."""
        if timed_out:
            return cls.TIMEOUT
        if status == 0:
            return cls.TRANSPORT_ERROR
        return cls.HTTP_ERROR


DeliveryStatus = str
"""How one attempt ended: ``delivered``, ``failed``, ``exhausted``, ``skipped``,
``dropped``. An open alias, so a fork adding a chaos fault can add a status."""

DELIVERY_STATUSES: tuple[str, ...] = ("delivered", "failed", "exhausted", "skipped", "dropped")


@dataclass(frozen=True, slots=True)
class Subscription:
    """One subscriber: a typed view over a store entity, so creating one journals."""

    id: str
    notification_url: str
    event_types: tuple[str, ...]
    signature_key: str
    enabled: bool = True
    name: str | None = None
    api_version: str | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> Subscription:
        """Read one entity. ``enabled`` is compared with ``is not False``, never coerced."""
        raw_types = entity.get("event_types", ())
        types = (
            tuple(str(t) for t in raw_types)
            if isinstance(raw_types, Sequence) and not isinstance(raw_types, str)
            else ()
        )
        name = entity.get("name")
        api_version = entity.get("api_version")
        return cls(
            id=str(entity["id"]),
            notification_url=str(entity.get("notification_url", "")),
            event_types=types,
            signature_key=str(entity.get("signature_key", "")),
            enabled=entity.get("enabled", True) is not False,
            name=None if name is None else str(name),
            api_version=None if api_version is None else str(api_version),
        )


@dataclass(frozen=True, slots=True)
class DeliveryMetadata:
    """Everything the core knows about one attempt, for the vendor to turn into headers.
    ``attempt`` and ``retry_number`` differ by one on purpose, rather than being subtracted
    at the vendor."""

    event: PreparedEvent
    subscription_id: str
    notification_url: str
    #: 1 for the first send, 2 for the first retry, and so on.
    attempt: int
    retry_number: int
    #: Why the previous attempt failed. ``None`` on the first send.
    retry_reason: DeliveryOutcome | None
    #: When the first attempt was made; the same on every retry.
    initial_delivery_at: str

    @property
    def is_retry(self) -> bool:
        return self.retry_number > 0


class DeliveryHeaderProvider(Protocol):
    """The vendor hook supplying every outbound delivery header, satisfied structurally by
    ``VendorDefinition.signer``."""

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Non-signature headers for one attempt, including the content type. The core adds
        nothing but ``sign()``'s headers, so an empty mapping means no content type."""
        ...


@runtime_checkable
class BodyEncodingSigner(Protocol):
    """A vendor whose outbound delivery body is not JSON, so its content type cannot lie
    about the bytes under it. Structurally discovered: a signer that does not implement it
    is declaring JSON. The return is the exact bytes signed and sent."""

    def encode_body(self, event: PreparedEvent) -> bytes: ...


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    """One attempt, as ``/__unit/webhooks/deliveries`` publishes it; the last five fields are
    optional and :meth:`as_json` omits rather than nulls them. ``body_is_json`` separates
    "not JSON" from "the JSON document null"."""

    id: str
    event_id: str
    event_type: str
    entity_id: str
    subscription_id: str
    url: str
    attempt: int
    retry_number: int
    at: str
    status: DeliveryStatus
    response_status: int
    body_hash: str
    body_preview: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None
    body_is_json: bool = False
    chaos: tuple[str, ...] = ()
    error: str | None = None
    next_attempt_in_ms: int | None = None

    def copy(self) -> DeliveryRecord:
        return DeliveryRecord(
            id=self.id,
            event_id=self.event_id,
            event_type=self.event_type,
            entity_id=self.entity_id,
            subscription_id=self.subscription_id,
            url=self.url,
            attempt=self.attempt,
            retry_number=self.retry_number,
            at=self.at,
            status=self.status,
            response_status=self.response_status,
            body_hash=self.body_hash,
            body_preview=self.body_preview,
            headers=dict(self.headers),
            body=copy.deepcopy(self.body),
            body_is_json=self.body_is_json,
            chaos=self.chaos,
            error=self.error,
            next_attempt_in_ms=self.next_attempt_in_ms,
        )

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "subscription_id": self.subscription_id,
            "url": self.url,
            "attempt": self.attempt,
            "retry_number": self.retry_number,
            "at": self.at,
            "status": self.status,
            "response_status": self.response_status,
            "body_hash": self.body_hash,
            "body_preview": self.body_preview,
            "headers": dict(self.headers),
        }
        if self.body_is_json:
            out["body"] = copy.deepcopy(self.body)
        if self.chaos:
            out["chaos"] = list(self.chaos)
        if self.error is not None:
            out["error"] = self.error
        if self.next_attempt_in_ms is not None:
            out["next_attempt_in_ms"] = self.next_attempt_in_ms
        return out


def matches_event_type(patterns: Sequence[str], event_type: str) -> bool:
    """Does a subscription's ``event_types`` cover this event? An exact match, the bare
    ``*``, or a glob; metacharacters are escaped by hand because :func:`re.escape` would
    also escape the ``*``."""
    for pattern in patterns:
        if pattern == event_type or pattern == "*":
            return True
        if "*" not in pattern:
            continue
        escaped = "".join("\\" + ch if ch in _REGEX_METACHARACTERS else ch for ch in pattern)
        if re.fullmatch(escaped.replace("*", ".*"), event_type) is not None:
            return True
    return False


_WEBHOOK_URL_SCHEMES = frozenset({"http", "https"})


def check_notification_url(url: str) -> str:
    """A webhook target the unit is willing to post to, or ``ValueError``. The rule is narrow
    because a test's receiver lives on 127.0.0.1: an ``http``/``https`` scheme, a host, and no
    link-local literal, where a cloud instance's metadata service answers. DNS is never
    resolved -- that would make a body's validity depend on the network."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError as exc:
        raise ValueError(f"{url!r} is not a parseable URL") from exc
    if parts.scheme.lower() not in _WEBHOOK_URL_SCHEMES:
        raise ValueError(f"scheme must be http or https, got {parts.scheme or '(none)'!r}")
    if not host:
        raise ValueError("the URL must name a host")
    try:
        address = ipaddress.ip_address(host.partition("%")[0])
    except ValueError:
        return url  # A name, not a literal; not resolved.
    mapped = getattr(address, "ipv4_mapped", None)
    if address.is_link_local or (mapped is not None and mapped.is_link_local):
        raise ValueError(f"{host} is link-local; the instance metadata service lives there")
    return url


def require_postable_target(url: str, *, field: str) -> str:
    """:func:`check_notification_url`, turned into the unit's own refusal. The control plane's
    subscription route and a profile's ``SubscriberConfig`` validate through this same check as a
    pydantic field validator; a vendor route that creates or updates a subscription from a request
    body has no pydantic model to hang that on, so it calls this directly, before anything is
    stored, and gets back the vendor's own ``invalid_value`` shape instead of a raw ``ValueError``."""
    try:
        return check_notification_url(url)
    except ValueError as exc:
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=str(exc), field=field) from exc
