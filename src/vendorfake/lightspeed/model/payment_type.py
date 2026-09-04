"""The payment-type wire shape, and the register payments summary built on it.

DOCUMENTED (``PaymentType``): ``id``, ``name``, ``type_id``, ``version``,
``disabled`` and ``internal`` are required; ``config`` is a nullable free-form
object ("Shape varies by payment type"); ``gateway``,
``name_changed_by_user``, ``outlet_ids``, ``created_at``, ``deleted_at``,
``is_editable`` and the embedded ``payment_type`` (a ``GlobalPaymentType``) are
optional. The documented example prints only ``id``, ``name``, ``type_id``,
``version`` and, where there is one, ``config`` -- so the projection emits the
required six plus whatever else the entity actually carries, and drops the
rest.

``type_id`` is "The ID of the global payment type. It shouldn't be used to
identify the payment type - there may be multiple payment types with the same
``type_id``". The scenario seeds two types sharing no ``type_id``, but the
warning is the reason nothing here keys on it.

THE SCOPE'S OWN WORDING is what makes ``internal`` load-bearing:
``payment_types:read`` is "Read payment types, **excluding internal payment
types**" (https://x-series-api.lightspeedhq.com/docs/scopes). So the list route
filters them out. JUDGMENT on the mechanism -- the scope page states the
exclusion, no page states how the API expresses it -- and it is stated at the
route.

THE PAYMENTS SUMMARY (``RegisterPaymentsSummaryResponse``) is where the schema
and the example disagree, and the example wins. The ``RegisterPaymentsSummary``
schema declares only ``payments``; the documented example prints
``payments``, ``register_closure_id``, ``register_closure_sequence_number`` and
``register_open_time``. An OpenAPI object permits additional properties unless
it forbids them and this one does not, so emitting the example's four members
satisfies both -- and emitting only the schema's one would answer less than the
vendor's own example shows.
"""

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
