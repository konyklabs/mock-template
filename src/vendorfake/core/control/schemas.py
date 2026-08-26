"""Every control-plane request body, as a schema rather than as a hope.

FOR: giving each ``/__unit/*`` route that reads a body one declared shape, and
giving a malformed body one answer -- a 400 that names the offending field --
instead of twenty hand-written ``if (!body.x) throw`` chains and a 500 for
everything nobody thought of.

INVARIANT: **no control route can answer 500 for a syntactically valid JSON
body.** The reference reads every body with an unvalidated ``readJson<T>()``
(``control/plane.ts``); the cast is a promise to the compiler that nothing
enforces at run time, so ``{"ms": "soon"}`` reaches ``Number(...)`` and
``{"snapshot": 3}`` reaches ``store.restore``. Here each body is validated
against a model and :func:`parse_or_raise` turns Pydantic's verdict into the
``UnitError`` the rest of the core speaks -- ``missing_field`` when something
required was absent, ``invalid_value`` otherwise, both carrying the dotted
field path. Without that step a ``ValidationError`` would travel to the
kernel's catch-all and become ``internal``/500 where the contract is a shaped
400 with ``x-unit-error``.

This is one of the four core modules where Pydantic is permitted, named in
``tools/boundary.toml`` so that widening the list is a visible diff. The
justification is the same one that lets ``config/models.py`` use it: these are
*external documents*, parsed at a boundary, and Pydantic has no notion of HTTP
-- the framework assumption the boundary rule exists to keep out is
``Form(...)``/``Body(...)``, not schema validation.

THREE MODELLING DECISIONS WORTH THE WORDS
-----------------------------------------
``POST /__unit/chaos/rules`` cannot be one strict model
    Its reference body is ``{rules?, enabled?} & Partial<ChaosRule>``:
    ``{"enabled": false}`` is a valid toggle carrying no rule at all and must
    return 200 -- the reference's own toggle test asserts it -- ``{"rules":
    [...]}`` replaces the set, and a bare rule object adds one. So
    :class:`ChaosRulesBody` models only the two envelope keys and lets
    everything else fall into Pydantic's extras, which are then handed to
    ``chaos/rules.py:parse_rule``. That keeps the rule grammar stated exactly
    once, and keeps ``extra="forbid"`` on the *rule* where a misspelled ``when``
    key is the mistake that actually happens.

Absent is absent, on the way in as well as out
    Every optional field defaults to ``None`` and every handler distinguishes
    "not sent" from "sent as null". ``{"enabled": null}`` on the chaos body is
    therefore not a toggle to off; it is a body that did not ask to toggle.

``strict=True`` on booleans and integers, lax on floats
    ``{"enabled": "false"}`` is a real request a consumer sends by accident and
    JavaScript's truthiness makes it mean *true*. Rejecting it names the
    mistake. Floats stay lax because ``{"time_scale": "0.5"}`` is a query
    string round-trip away from every shell, and the reference coerced it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.config.models import unit_error_from_validation
from vendorfake.core.kernel.types import JournalEntry, UnitError, UnitErrorKind
from vendorfake.core.state.store import Entity, IdempotencyRecord, StoreSnapshot
from vendorfake.core.util.json import compact

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
    "StateRestoreBody",
    "SubscriptionCreateBody",
    "WebhookEmitBody",
    "idempotency_as_json",
    "journal_entry_as_json",
    "parse_or_raise",
    "require_finite",
    "snapshot_as_json",
]

_STRICT = ConfigDict(extra="forbid", frozen=True)
"""``extra="forbid"`` on every body a consumer writes by hand.

A misspelled key is the control-plane mistake that happens; silently ignoring
it produces "I sent ``keepRules`` and it wiped my rules anyway", which is
indistinguishable from a bug in the unit."""

_M = TypeVar("_M", bound=BaseModel)


def parse_or_raise(model: type[_M], data: object, *, source: str | None = None) -> _M:
    """Validate ``data`` against ``model``, or raise a field-naming ``UnitError``.

    The one adapter between Pydantic's vocabulary and the core's. Every
    control-plane handler goes through it, so there is exactly one place where
    the mapping from a validation failure to an error kind is decided, and
    exactly one place to change it.

    A body that is not a JSON object fails here rather than inside the model,
    because Pydantic would report ``loc: ()`` for it and the resulting error
    would name no field at all.
    """
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
    """Refuse a non-finite number with an ``invalid_value`` naming the field.

    JSON has no ``Infinity`` literal, but ``1e400`` parses to ``inf`` in both
    Python and JavaScript, and an infinite ``ms`` handed to a virtual clock is
    an unbounded loop rather than an error. The reference guards the same case
    with ``Number.isFinite``; this is that guard, reusable.
    """
    if not math.isfinite(value):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be a finite number.",
            field=field,
        )
    return value


# ---------------------------------------------------------------------------
# Capabilities.
# ---------------------------------------------------------------------------


class CapabilitiesBody(BaseModel):
    """``POST /__unit/capabilities``.

    Four independent instructions, applied in the reference's order: ``set``
    replaces the enabled list, ``delta`` applies ``+a,-b`` against whatever is
    enabled *after* that, and then ``enable``/``disable`` are applied one name
    at a time. Order is contract because ``{"set": ["oauth"], "enable":
    ["orders"]}`` means something different under any other reading.
    """

    model_config = _STRICT

    set: tuple[str, ...] | None = None
    enable: tuple[str, ...] | None = None
    disable: tuple[str, ...] | None = None
    #: ``+webhooks,-webhooks.chaos``, or a bare comma-separated absolute list.
    delta: str | None = None


# ---------------------------------------------------------------------------
# Chaos.
# ---------------------------------------------------------------------------


class ChaosRulesBody(BaseModel):
    """``POST /__unit/chaos/rules``: a toggle, a replacement, or one added rule.

    ``extra="allow"`` on purpose -- see the module docstring. The extras are
    the bare-rule form, and they are validated by the rule grammar itself
    rather than a second time here.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    #: Replace the whole rule set.
    rules: tuple[dict[str, Any], ...] | None = None
    #: Turn the engine on or off. ``strict``: ``"false"`` is not ``False``.
    enabled: bool | None = Field(default=None, strict=True)

    def rule_document(self) -> dict[str, Any] | None:
        """The bare-rule form, or ``None`` when the body carried only envelope keys.

        A body with extras *and* ``rules`` is the caller asking for two
        different things at once; the handler reports it rather than silently
        honouring one, which is stricter than the reference's ``else if``.
        """
        extra = self.__pydantic_extra__
        return dict(extra) if extra else None


class ChaosResetBody(BaseModel):
    """``POST /__unit/chaos/reset``. ``keep_rules`` resets counters only."""

    model_config = _STRICT

    keep_rules: bool = Field(default=False, strict=True)


# ---------------------------------------------------------------------------
# State.
# ---------------------------------------------------------------------------


class JournalEntryDocument(BaseModel):
    """One journal entry on the wire, in both directions.

    Modelled rather than passed through as a dict because a restored snapshot
    is replayed into ``Store.restore`` and then read by ``/__unit/journal``: a
    journal entry with a missing ``seq`` would corrupt the store's monotonic
    sequence, which is the one property the whole event-sourcing claim rests
    on.
    """

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
    """One stored idempotent response, on the wire.

    Part of a snapshot because a restore that dropped it would let a retried
    request execute a second time against restored state -- the exact double
    charge idempotency exists to prevent.
    """

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
    """The body of ``POST /__unit/state/restore``, and the shape
    ``GET /__unit/state/snapshot`` publishes.

    The two are the same model deliberately: a snapshot that could be read but
    not restored would make ``/__unit/state/snapshot`` decoration rather than a
    capability, and "take a snapshot here, restore it into a second unit" is
    how a consumer pins a scenario.
    """

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
    """``POST /__unit/state/restore``.

    ``snapshot`` is optional in the model and required by the handler, which is
    not a contradiction: the reference answers an absent snapshot with
    ``missing_field`` naming ``snapshot``, and a Pydantic-required field would
    report the same kind but only after refusing the whole body. Keeping it
    optional here lets the handler own the message.
    """

    model_config = _STRICT

    snapshot: SnapshotDocument | None = None


# ---------------------------------------------------------------------------
# Webhooks.
# ---------------------------------------------------------------------------

DEFAULT_CONTROL_SUBSCRIBER_NAME = "control-plane subscriber"
DEFAULT_CONTROL_SIGNATURE_KEY = "unit-signature-key"
DEFAULT_CONTROL_EVENT_TYPES: tuple[str, ...] = ("*",)


class SubscriptionCreateBody(BaseModel):
    """``POST /__unit/webhooks/subscriptions``: register a subscriber directly.

    Exists so that a consumer can point the dispatcher at their own receiver
    without holding a vendor credential or knowing the vendor's own
    subscription API -- which is the difference between "test my webhook
    handler" being one call and being a two-day integration.
    """

    model_config = _STRICT

    id: str | None = None
    name: str | None = None
    notification_url: str
    event_types: tuple[str, ...] = DEFAULT_CONTROL_EVENT_TYPES
    signature_key: str = DEFAULT_CONTROL_SIGNATURE_KEY
    enabled: bool = Field(default=True, strict=True)
    api_version: str | None = None


class RetryPolicyPatchBody(BaseModel):
    """``POST /__unit/webhooks/retry-policy``: sparse, and every key optional.

    Sparse because the three knobs are independent: compressing time without
    touching the schedule is the common request, and a full-replacement body
    would make a consumer restate a vendor's documented eleven intervals to
    change one multiplier.
    """

    model_config = _STRICT

    schedule_ms: tuple[int, ...] | None = None
    time_scale: float | None = None
    timeout_ms: int | None = None

    def patch(self) -> dict[str, Any]:
        """Only the keys the body actually set -- ``model_fields_set``, not
        ``model_dump``, so an unmentioned knob is left alone rather than reset
        to its default."""
        out: dict[str, Any] = {}
        for name in self.model_fields_set:
            value = getattr(self, name)
            if value is None:
                continue
            out[name] = list(value) if name == "schedule_ms" else value
        return out


class WebhookEmitBody(BaseModel):
    """``POST /__unit/webhooks/emit``: fire a synthetic event.

    It exists because a profile with no mutating route -- an OAuth-only one,
    say -- otherwise has no way to make a delivery happen, and a conformance
    check that can only run on profiles with a write surface is a check that
    tests the vendor rather than the unit.

    It is an *emitter*, not a signing oracle: it accepts no secret and returns
    no signature. Everything it produces goes through the same prepare, sign
    and deliver path as a journal-derived event, so what a consumer observes at
    ``/__unit/webhooks/deliveries`` is the real thing.
    """

    model_config = _STRICT

    #: Vendor event type, matched against subscriptions exactly as usual.
    type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    #: The envelope to deliver. Absent means "the core's own minimal one".
    body: Any = None


class SinkProgramBody(BaseModel):
    """``POST /__unit/webhooks/sink``: program the memory sink's next answers.

    ``[500, 200]`` means "fail once, then succeed", which is how a forced retry
    is driven from *outside* the process. Without it, the only way to observe a
    retry is to reach into the sink object, which a language-independent
    conformance suite cannot do.
    """

    model_config = _STRICT

    #: Consumed in order; once exhausted the sink answers with ``then``.
    statuses: tuple[int, ...] = Field(min_length=1)
    #: What to answer after ``statuses`` runs out. ``0`` means "timed out".
    then: int = 200


# ---------------------------------------------------------------------------
# Clock and machines.
# ---------------------------------------------------------------------------


class ClockAdvanceBody(BaseModel):
    """``POST /__unit/clock/advance``. Virtual clock only."""

    model_config = _STRICT

    ms: float = 0.0


class MachineProbeBody(BaseModel):
    """``POST /__unit/machines/probe``: evaluate a transition, mutate nothing.

    ``from`` is a Python keyword, so the field is ``from_`` with ``from`` as
    its alias and ``populate_by_name`` deliberately **off**: the wire name is
    the contract and accepting ``from_`` as well would give the same request
    two spellings, one of which no other language's client would ever produce.

    ``to`` is optional, and its absence is not an omission -- it selects the
    other question the machine answers. With ``to``, the probe asks "is this
    move legal"; without it, it asks "may this entity be mutated at all", which
    is ``assert_mutable`` and is the check a terminal state exists to fail.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=False)

    machine: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1)
    to: str | None = None


# ---------------------------------------------------------------------------
# Outbound projections.
# ---------------------------------------------------------------------------


def journal_entry_as_json(entry: JournalEntry) -> dict[str, Any]:
    """One journal entry, as ``GET /__unit/journal`` publishes it.

    ``from_version``, ``to_version`` and ``meta`` are dropped when absent
    rather than sent as ``null``: an insert has no ``from_version`` and a
    ``"from_version": null`` on the wire is a value where the reference has no
    key. It is also what makes :class:`JournalEntryDocument` able to round-trip
    its own output.
    """
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
