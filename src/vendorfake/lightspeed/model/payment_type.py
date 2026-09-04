"""The payment-type wire shape, and the register payments summary built on it.
DOCUMENTED (``PaymentType``): ``id``, ``name``, ``type_id``, ``version``,
``disabled`` and ``internal`` are required; ``type_id`` is documented as not
unique, so nothing here keys on it. DOCUMENTED: ``payment_types:read``
excludes internal payment types
(https://x-series-api.lightspeedhq.com/docs/scopes), so the list route
filters them out (JUDGMENT on the mechanism). JUDGMENT: the payments summary
also emits ``register_closure_id``, ``register_closure_sequence_number`` and
``register_open_time`` beyond its declared schema, since OpenAPI permits
additional properties unless forbidden."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.core.util.json import compact
from vendorfake.lightspeed.entities import PaymentTypeEntity

__all__ = ["project_payment_type", "project_payments_summary"]


def project_payment_type(entity: Mapping[str, Any]) -> dict[str, Any]:
    """The documented ``PaymentType`` document, required members first."""
    payment_type = PaymentTypeEntity.from_entity(entity)
    return compact(
        {
            "id": payment_type.id,
            "name": payment_type.name,
            "type_id": payment_type.type_id,
            "disabled": payment_type.disabled,
            "internal": payment_type.internal,
            "gateway": payment_type.gateway,
            "name_changed_by_user": payment_type.name_changed_by_user,
            "config": None if payment_type.config is None else dict(payment_type.config),
            "outlet_ids": list(payment_type.outlet_ids) if payment_type.outlet_ids else None,
            "deleted_at": payment_type.deleted_at,
            "version": payment_type.object_version,
        }
    )


def project_payments_summary(
    *,
    payments: Sequence[Mapping[str, Any]],
    register_closure_id: str,
    register_closure_sequence_number: int,
    register_open_time: str | None,
) -> dict[str, Any]:
    """The four members the documented example prints, in its own order."""
    return {
        "payments": [dict(row) for row in payments],
        "register_closure_id": register_closure_id,
        "register_closure_sequence_number": register_closure_sequence_number,
        "register_open_time": register_open_time,
    }
