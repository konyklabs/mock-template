"""What a delivery is, stated without naming a vendor.

FOR: giving the dispatcher a vocabulary for "who am I sending to", "what am I
sending", "which attempt is this" and "what happened", so that everything the
core knows about a delivery can be handed to a vendor without the core ever
learning a vendor's header names, wire strings or payload shape.

INVARIANT: **the core sends no delivery headers of its own.** Every header on
an outbound delivery -- the content type, the signature, the retry counters,
the initial-delivery timestamp -- comes back from
:class:`DeliveryHeaderProvider`, which the vendor's signer implements. The
reference does the opposite: ``packages/core/src/webhooks/dispatcher.ts`` lines
292-300 write a content type and three brand-prefixed retry headers directly
into vendor-neutral core, naming one vendor in the shared layer four times.
``tools/boundary_check.py`` fails the build when a vendor slug appears as a
literal under ``core/``, so the leak here is not merely discouraged.

THE SECOND HALF OF THE SAME LEAK, which the slug rule cannot see.
``dispatcher.ts:310`` computes the retry reason as one of three literal
strings, chosen by whether the attempt timed out, failed before a status
existed, or came back with an unsuccessful status. Those three strings are one
vendor's documented retry-reason vocabulary, asserted verbatim by that vendor's
own tests -- and not one of them contains a vendor slug, so a checker looking
for slugs would pass a build that still shipped them. So the outcome of an
attempt is a *neutral* enum here, :class:`DeliveryOutcome`, and the map from
outcome to wire string lives in the vendor package beside the header names. One
hook, one direction, and the vendor owns both ends of its own vocabulary.

WHY THE PROVIDER IS THE SIGNER AND NOT A SECOND OBJECT. The signature is a
header too. Two hooks would be two places to register a vendor and two chances
to register only one, and the failure mode of registering only one -- a
delivery that is signed but carries no retry counter, or carries counters but
is unsigned -- is silent at the sink. ``Signer.headers(meta)`` is therefore
part of the same protocol as ``Signer.sign(payload)``; this module states the
narrow structural view the dispatcher actually depends on.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from vendorfake.core.kernel.types import PreparedEvent

__all__ = [
    "SUBSCRIPTION_COLLECTION",
    "BodyEncodingSigner",
    "DeliveryHeaderProvider",
    "DeliveryMetadata",
    "DeliveryOutcome",
    "DeliveryRecord",
    "DeliveryStatus",
    "Subscription",
    "matches_event_type",
]

_REGEX_METACHARACTERS = frozenset(".+?^${}()|[]\\")
"""Every character :func:`matches_event_type` escapes before turning ``*`` into
``.*``. Note that ``*`` is deliberately absent: it is the one metacharacter the
pattern language keeps."""

SUBSCRIPTION_COLLECTION = "subscriptions"
"""The store collection subscriptions live in.

Named here rather than in the dispatcher because two other places need it: the
dispatcher's journal listener, which must ignore mutations to this collection
so that registering a subscriber does not itself emit an event, and the control
plane, which lists it.
"""


class DeliveryOutcome(StrEnum):
    """Why an attempt was not accepted -- in core's own words, not a vendor's.

    Exactly three, because there are exactly three distinguishable failures at
    a transport that returns a status code: nothing came back in time, the
    transport itself failed before any status existed, and a status came back
    that was not a success. A vendor maps these onto whatever strings its
    documentation publishes; the core never learns them.
    """

    TIMEOUT = "timeout"
    """Nothing came back before ``retry.timeout_ms`` elapsed."""

    TRANSPORT_ERROR = "transport_error"
    """The send failed before a status existed: connection refused, DNS, TLS."""

    HTTP_ERROR = "http_error"
    """A status came back and it was not a success."""

    @classmethod
    def of(cls, status: int, *, timed_out: bool) -> DeliveryOutcome:
        """Classify one sink result. Ported from ``dispatcher.ts:310``.

        Order is contract: ``timed_out`` is consulted first, because a sink
        reports a timeout as status ``0`` as well and testing the status first
        would collapse the two.
        """
        if timed_out:
            return cls.TIMEOUT
        if status == 0:
            return cls.TRANSPORT_ERROR
        return cls.HTTP_ERROR


DeliveryStatus = str
"""How one delivery attempt ended, as the delivery log records it.

Five values, ported from ``dispatcher.ts:30``: ``delivered``, ``failed``,
``exhausted``, ``skipped``, ``dropped``. An open alias rather than an enum
because the value is published verbatim at ``/__unit/webhooks/deliveries`` and
a fork that adds a sixth chaos fault should be able to add a sixth status
without editing the core.
"""

DELIVERY_STATUSES: tuple[str, ...] = ("delivered", "failed", "exhausted", "skipped", "dropped")
"""The five statuses the core itself produces. Published so a test can assert
the vocabulary did not grow by accident."""


@dataclass(frozen=True, slots=True)
class Subscription:
    """One subscriber, as the dispatcher reads it.

    A typed view over a store entity rather than a second copy of the truth:
    subscriptions are ordinary entities so that creating one through a vendor's
    own API journals like any other mutation, and so that the control plane can
    list them without a private channel. :meth:`from_entity` is the one place
    the entity's key names are known.
    """

    id: str
    notification_url: str
    event_types: tuple[str, ...]
    signature_key: str
    enabled: bool = True
    name: str | None = None
    api_version: str | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> Subscription:
        """Read one entity. Missing optionals are absent, never ``None``-valued.

        ``enabled`` defaults to ``True`` and is compared with ``is not False``
        rather than coerced: an entity carrying ``"enabled": 0`` is a defect in
        whatever wrote it, and treating it as truthy-or-not would hide that
        behind a plausible answer.
        """
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
    """Everything the core knows about one attempt, offered to the vendor.

    This is the whole of the de-vendoring: the core computes these seven
    neutral facts and a vendor turns them into whatever headers its
    documentation specifies. A vendor that publishes no retry headers ignores
    ``retry_number`` and ``retry_reason``; a vendor that publishes a delivery id
    header derives it from ``event`` and ``attempt``. The core does not care,
    and cannot, because it never sees the result other than as a mapping it
    puts on the wire.

    ``attempt`` and ``retry_number`` are both here and differ by one on
    purpose. ``attempt`` counts from 1 and is what a human reads; the reference
    vendor's retry-number header counts from 0 for the first send, and deriving
    one from the other at the vendor would be a subtraction that the wrong
    vendor gets wrong once.
    """

    event: PreparedEvent
    subscription_id: str
    notification_url: str
    #: 1 for the first send, 2 for the first retry, and so on.
    attempt: int
    #: 0 for the first send. ``attempt - 1``, stated rather than derived.
    retry_number: int
    #: Why the previous attempt failed. ``None`` on the first send.
    retry_reason: DeliveryOutcome | None
    #: When the first attempt for this event and subscriber was made -- the
    #: same value on every retry, so a consumer can measure total latency.
    initial_delivery_at: str

    @property
    def is_retry(self) -> bool:
        """``retry_number > 0``. The condition the reference guards its retry
        headers with, named once so two vendors cannot spell it differently."""
        return self.retry_number > 0


class DeliveryHeaderProvider(Protocol):
    """The vendor hook that supplies every outbound delivery header.

    Structurally satisfied by ``VendorDefinition.signer``; declared narrowly
    here so the dispatcher depends on the one method it calls rather than on
    the whole signing protocol.
    """

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Non-signature headers for one attempt, including the content type.

        The core adds nothing to what comes back except the signature headers
        from ``sign()``, so a provider that returns an empty mapping produces a
        delivery with no content type -- which is a vendor's decision to make
        and not the core's to second-guess.
        """
        ...


@runtime_checkable
class BodyEncodingSigner(Protocol):
    """A vendor whose outbound delivery body is not JSON.

    FOR: the vendors that document a delivery in some other media type. The
    dispatcher's default is ``dump_json(event.body)``, which is what every
    vendor shipped here before this hook needed -- but a delivery body is a
    vendor's documented wire format like any other, and at least one vendor
    documents it as ``application/x-www-form-urlencoded`` with the entity JSON
    inside a named field. Without this hook such a vendor can set the content
    type through :class:`DeliveryHeaderProvider` and then send bytes that
    contradict it, which is the one shape a fake must never ship: a header
    that lies about the body under it.

    A SEPARATE, STRUCTURALLY DISCOVERED PROTOCOL, for exactly the reasons
    :class:`~vendorfake.core.kernel.types.SeedingVendor` is one. Adding the
    method to ``Signer`` would break every existing implementation, in this
    distribution and outside it, to express something absence already
    expresses: a signer that does not implement this is declaring "JSON", and
    is precisely as valid as it was before this protocol existed. The
    dispatcher asks ``isinstance(signer, BodyEncodingSigner)`` and falls back
    to JSON.

    THE RETURN IS THE EXACT BYTES SIGNED AND SENT. There is no second
    encoding step: the dispatcher hands these bytes to ``Signer.sign`` as
    ``SignInput.raw_body`` and to the sink as the request body, so a signature
    scheme that covers the raw body covers what actually went out.
    """

    def encode_body(self, event: PreparedEvent) -> bytes:
        """The delivery body for ``event``, already encoded."""
        ...


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    """One attempt, as ``/__unit/webhooks/deliveries`` publishes it.

    Eighteen fields, ported from ``dispatcher.ts:32-53``: ``id``, ``event_id``,
    ``event_type``, ``entity_id``, ``subscription_id``, ``url``, ``attempt``,
    ``retry_number``, ``at``, ``status``, ``response_status``, ``body_hash``,
    ``body_preview``, ``body``, ``headers``, ``chaos``, ``error`` and
    ``next_attempt_in_ms``. The last five are optional on the wire and
    :meth:`as_json` omits them rather than emitting ``null``, matching the
    reference's ``undefined`` keys -- absence is absence here as everywhere.

    ``body`` is the delivered payload parsed back, and ``body_is_json`` is the
    flag that distinguishes "the payload was not JSON" from "the payload was
    the JSON document ``null``". The reference cannot tell those apart and does
    not need to; Python can, and collapsing them would put ``"body": null`` on
    a record whose vendor sends protobuf.
    """

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
        """A private copy. ``headers`` and ``body`` are the two mutable parts.

        The reference deep-copies the whole record at ``deliveries()``; this
        does the same work at the one place that can hand a caller a reference
        into the log.
        """
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
        """The published shape. Optional keys are omitted, never nulled."""
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
    """Does a subscription's ``event_types`` cover this event?

    Ported from ``dispatcher.ts:369``. Three rules, in this order: an exact
    match, the bare ``*``, and a glob in which ``*`` stands for any run of
    characters and every other regex metacharacter is escaped. A pattern with
    no ``*`` and no exact match returns False without ever building a regex --
    which is the common case and the reference's own short-circuit.

    The escape set is the reference's, character for character, translated to
    the metacharacters Python's ``re`` actually has: the reference escapes
    ``.+?^${}()|[]\\`` and then replaces ``*``. Escaping by hand rather than
    with :func:`re.escape` is deliberate -- :func:`re.escape` would also escape
    the ``*`` this function is about to turn into ``.*``.
    """
    for pattern in patterns:
        if pattern == event_type or pattern == "*":
            return True
        if "*" not in pattern:
            continue
        escaped = "".join("\\" + ch if ch in _REGEX_METACHARACTERS else ch for ch in pattern)
        if re.fullmatch(escaped.replace("*", ".*"), event_type) is not None:
            return True
    return False
