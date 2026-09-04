"""Every control-plane request body, as a schema rather than as a hope.

No control route can answer 500 for a syntactically valid JSON body: every body is validated
against a model and :func:`parse_or_raise` turns Pydantic's verdict into the ``UnitError`` the
rest of the core speaks. One of the four core modules where Pydantic is permitted (named in
``tools/boundary.toml``), since these are external documents parsed at a boundary.

Every optional field defaults to ``None``, and a handler distinguishes "not sent" from "sent as
null". ``strict=True`` on booleans and integers rejects a stringly-typed field sent by accident;
floats stay lax since a query-string round trip coerces them anyway.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from vendorfake.core.config.models import unit_error_from_validation
from vendorfake.core.kernel.types import JournalEntry, UnitError, UnitErrorKind
from vendorfake.core.state.store import Entity, IdempotencyRecord, StoreSnapshot
from vendorfake.core.util.json import compact
from vendorfake.core.webhooks.models import check_notification_url

__all__ = [
    "CapabilitiesBody",
    "ChaosResetBody",
    "ChaosRulesBody",
    "ClockAdvanceBody",
    "IdempotencyDocument",
    "JournalEntryDocument",
    "MachineProbeBody",
    "RetryPolicyPatchBody",
    "SinkProgramBody",
    "SnapshotDocument",
    "StatePageBody",
    "StateRestoreBody",
    "StateUpdateBody",
    "SubscriptionCreateBody",
    "WebhookEmitBody",
    "idempotency_as_json",
    "journal_entry_as_json",
    "parse_or_raise",
    "require_finite",
    "snapshot_as_json",
]

_STRICT = ConfigDict(extra="forbid", frozen=True)
"""``extra="forbid"`` on every body a consumer writes by hand: a misspelled key is the control-plane mistake that happens."""

_M = TypeVar("_M", bound=BaseModel)


def parse_or_raise(model: type[_M], data: object, *, source: str | None = None) -> _M:
    """Validate ``data`` against ``model``, or raise a field-naming ``UnitError``; a non-object body fails here, not inside the model."""
    if not isinstance(data, Mapping):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="The request body must be a JSON object.",
            field="body",
        )
    try:
        return model.model_validate(dict(data))
    except ValidationError as exc:
        raise unit_error_from_validation(exc, source=source) from exc


def require_finite(value: float, *, field: str) -> float:
    """Refuse a non-finite number with an ``invalid_value`` naming the field: an infinite ``ms`` is an unbounded loop, not an error."""
    if not math.isfinite(value):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be a finite number.",
            field=field,
        )
    return value


class CapabilitiesBody(BaseModel):
    """``POST /__unit/capabilities``: ``set``, ``delta``, then ``enable``/``disable``, applied in that order. Order is contract."""

    model_config = _STRICT

    set: tuple[str, ...] | None = None
    enable: tuple[str, ...] | None = None
    disable: tuple[str, ...] | None = None
    #: ``+webhooks,-webhooks.chaos``, or a bare comma-separated absolute list.
    delta: str | None = None


class ChaosRulesBody(BaseModel):
    """``POST /__unit/chaos/rules``: a toggle, a replacement, or one added rule; ``extra="allow"`` since the extras are the bare-rule form."""

    model_config = ConfigDict(extra="allow", frozen=True)

    #: Replace the whole rule set.
    rules: tuple[dict[str, Any], ...] | None = None
    #: Turn the engine on or off. ``strict``: ``"false"`` is not ``False``.
    enabled: bool | None = Field(default=None, strict=True)

    def rule_document(self) -> dict[str, Any] | None:
        """The bare-rule form, or ``None`` when the body carried only envelope keys."""
        extra = self.__pydantic_extra__
        return dict(extra) if extra else None


class ChaosResetBody(BaseModel):
    """``POST /__unit/chaos/reset``. ``keep_rules`` resets counters only."""

    model_config = _STRICT

    keep_rules: bool = Field(default=False, strict=True)


class JournalEntryDocument(BaseModel):
    """One journal entry on the wire; modelled, not a raw dict, since a missing ``seq`` would corrupt the store's sequence."""

    model_config = _STRICT

    seq: int
    at: str
    collection: str
    id: str
    op: Literal["insert", "update", "delete"]
    from_version: int | None = None
    to_version: int | None = None
    changed: tuple[str, ...] = ()
    meta: dict[str, Any] | None = None

    def to_entry(self) -> JournalEntry:
        return JournalEntry(
            seq=self.seq,
            at=self.at,
            collection=self.collection,
            id=self.id,
            op=self.op,
            from_version=self.from_version,
            to_version=self.to_version,
            changed=tuple(self.changed),
            meta=None if self.meta is None else dict(self.meta),
        )


class IdempotencyDocument(BaseModel):
    """One stored idempotent response, on the wire; part of a snapshot since dropping it would let a retry double-execute."""

    model_config = _STRICT

    scope: str
    key: str
    request_digest: str
    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    body_b64: str
    stored_at: str

    def to_record(self) -> IdempotencyRecord:
        return IdempotencyRecord(
            scope=self.scope,
            key=self.key,
            request_digest=self.request_digest,
            status=self.status,
            headers=dict(self.headers),
            body_b64=self.body_b64,
            stored_at=self.stored_at,
        )


class SnapshotDocument(BaseModel):
    """The body of ``POST /__unit/state/restore`` and the shape ``GET /__unit/state/snapshot`` publishes -- the same model deliberately."""

    model_config = _STRICT

    collections: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    journal: tuple[JournalEntryDocument, ...] = ()
    idempotency: tuple[IdempotencyDocument, ...] = ()
    seq: int = 0

    def to_snapshot(self) -> StoreSnapshot:
        collections: dict[str, dict[str, Entity]] = {
            name: {entity_id: dict(entity) for entity_id, entity in entities.items()}
            for name, entities in self.collections.items()
        }
        return StoreSnapshot(
            collections=collections,
            journal=[entry.to_entry() for entry in self.journal],
            idempotency=[record.to_record() for record in self.idempotency],
            seq=self.seq,
        )


class StateRestoreBody(BaseModel):
    """``POST /__unit/state/restore``; ``snapshot`` is optional here and required by the handler, which owns the ``missing_field`` message."""

    model_config = _STRICT

    snapshot: SnapshotDocument | None = None


DEFAULT_CONTROL_SUBSCRIBER_NAME = "control-plane subscriber"
DEFAULT_CONTROL_SIGNATURE_KEY = "unit-signature-key"
DEFAULT_CONTROL_EVENT_TYPES: tuple[str, ...] = ("*",)


class SubscriptionCreateBody(BaseModel):
    """``POST /__unit/webhooks/subscriptions``: register a subscriber directly, without a vendor credential or the vendor's subscription API."""

    model_config = _STRICT

    id: str | None = None
    name: str | None = None
    notification_url: str
    event_types: tuple[str, ...] = DEFAULT_CONTROL_EVENT_TYPES
    signature_key: str = DEFAULT_CONTROL_SIGNATURE_KEY
    enabled: bool = Field(default=True, strict=True)
    api_version: str | None = None

    @field_validator("notification_url")
    @classmethod
    def _target_is_postable(cls, value: str) -> str:
        return check_notification_url(value)


class RetryPolicyPatchBody(BaseModel):
    """``POST /__unit/webhooks/retry-policy``: sparse, every key optional, since the three knobs are independent."""

    model_config = _STRICT

    schedule_ms: tuple[int, ...] | None = None
    time_scale: float | None = None
    timeout_ms: int | None = None

    def patch(self) -> dict[str, Any]:
        """Only the keys the body actually set, so an unmentioned knob is left
        alone rather than reset to its default."""
        out: dict[str, Any] = {}
        for name in self.model_fields_set:
            value = getattr(self, name)
            if value is None:
                continue
            out[name] = list(value) if name == "schedule_ms" else value
        return out


class WebhookEmitBody(BaseModel):
    """``POST /__unit/webhooks/emit``: fire a synthetic event. An *emitter*, not a signing oracle: it accepts no secret and returns none."""

    model_config = _STRICT

    #: Vendor event type, matched against subscriptions exactly as usual.
    type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    #: The envelope to deliver. Absent means "the core's own minimal one".
    body: Any = None


class SinkProgramBody(BaseModel):
    """``POST /__unit/webhooks/sink``: program the memory sink's next answers, e.g. ``[500, 200]`` for "fail once, then succeed"."""

    model_config = _STRICT

    #: Consumed in order; once exhausted the sink answers with ``then``.
    statuses: tuple[int, ...] = Field(min_length=1)
    #: What to answer after ``statuses`` runs out. ``0`` means "timed out".
    then: int = 200


class ClockAdvanceBody(BaseModel):
    """``POST /__unit/clock/advance``. Virtual clock only. ``drain`` defaults to true, chasing every timer that sets off; ``false`` fires only ``ms``."""

    model_config = _STRICT

    ms: float = 0.0
    drain: bool = True


class MachineProbeBody(BaseModel):
    """``POST /__unit/machines/probe``: evaluate a transition, mutate nothing; ``to`` absent asks "may this mutate" not "is this move legal"."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=False)

    machine: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1)
    to: str | None = None


class StateUpdateBody(BaseModel):
    """``POST /__unit/state/update``: one mutation under a version; ``version`` absent means "no opinion", not ``version: 0``."""

    model_config = _STRICT

    collection: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: int | None = None
    patch: Mapping[str, Any] = Field(default_factory=dict)


class StatePageBody(BaseModel):
    """``POST /__unit/state/page``: page a collection; ``query`` is the fingerprint input verbatim, so a cursor is refused by another query."""

    model_config = _STRICT

    collection: str = Field(min_length=1)
    query: Any = None
    limit: int | None = None
    cursor: str | None = None


def journal_entry_as_json(entry: JournalEntry) -> dict[str, Any]:
    """One journal entry as ``GET /__unit/journal`` publishes it: absent fields are dropped, not sent as ``null``."""
    return compact(
        {
            "seq": entry.seq,
            "at": entry.at,
            "collection": entry.collection,
            "id": entry.id,
            "op": entry.op,
            "from_version": entry.from_version,
            "to_version": entry.to_version,
            "changed": list(entry.changed),
            "meta": None if entry.meta is None else dict(entry.meta),
        }
    )


def idempotency_as_json(record: IdempotencyRecord) -> dict[str, Any]:
    """One stored idempotent response, all keys always present."""
    return {
        "scope": record.scope,
        "key": record.key,
        "request_digest": record.request_digest,
        "status": record.status,
        "headers": dict(record.headers),
        "body_b64": record.body_b64,
        "stored_at": record.stored_at,
    }


def snapshot_as_json(snapshot: StoreSnapshot) -> dict[str, Any]:
    """A whole snapshot, in the shape :class:`SnapshotDocument` accepts back."""
    return {
        "collections": {
            name: {entity_id: dict(entity) for entity_id, entity in entities.items()}
            for name, entities in snapshot.collections.items()
        },
        "journal": [journal_entry_as_json(entry) for entry in snapshot.journal],
        "idempotency": [idempotency_as_json(record) for record in snapshot.idempotency],
        "seq": snapshot.seq,
    }
