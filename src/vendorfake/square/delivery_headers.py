"""Square's own names for what the core knows neutrally about a delivery.

FOR: turning the seven neutral facts in
:class:`~vendorfake.core.webhooks.models.DeliveryMetadata` into the headers
Square documents on an outbound webhook, so that a consumer's handler reads
``square-retry-number`` rather than something this project invented.

INVARIANT: **this is the only place the core's delivery vocabulary is
translated.** The core sends no headers of its own -- not even a content type
-- so everything on the wire arrives through here or through
:meth:`~vendorfake.square.signer.SquareWebhookSigner.sign`. The reference wrote
the content type and all three ``square-*`` names straight into vendor-neutral
core (``packages/core/src/webhooks/dispatcher.ts`` lines 292-300), and computed
the retry *reason* there too (line 310) as one of three literal strings that
carry no brand name and that a slug-scanning checker therefore cannot see. Both
halves of that leak end here.

WHAT SQUARE DOCUMENTS
---------------------
https://developer.squareup.com/docs/webhooks/overview (fetched 2026-08-25):
"Retried notifications include the ``square-retry-number`` and
``square-retry-reason`` headers." The reason vocabulary is ``http_timeout``,
``http_error``, ``ssl_error`` and ``other_error``.
https://developer.squareup.com/docs/webhooks/build-with-webhooks documents
``square-initial-delivery-timestamp`` and ``square-environment``.

THREE RULES, EACH LOAD-BEARING
------------------------------
* ``square-retry-number`` and ``square-retry-reason`` appear **only on a
  retry** -- the reference's ``if (q.retryNumber > 0)``, named once as
  :attr:`~vendorfake.core.webhooks.models.DeliveryMetadata.is_retry`. A
  consumer distinguishing a first delivery from a redelivery does it by the
  header's absence, so sending ``square-retry-number: 0`` on the first attempt
  would be wrong in a way that looks right.
* ``square-initial-delivery-timestamp`` appears on **every** attempt, carrying
  the same value each time, so a consumer can measure total latency across a
  retry cascade.
* ``square-environment`` also appears on every attempt. The reference returns
  it from its signer's ``sign()``; it is not a signature header, so here it
  comes from the header hook. The bytes on the wire are identical either way --
  the dispatcher merges both mappings -- and stating it here means "every
  non-signature header" is true of one function rather than of two.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.square.retry import (
    CONTENT_TYPE,
    INITIAL_DELIVERY_HEADER,
    RETRY_NUMBER_HEADER,
    RETRY_REASON_HEADER,
    RETRY_REASONS,
)
from vendorfake.square.surface.common import SquareDeps

__all__ = ["ENVIRONMENT_HEADER", "SquareDeliveryHeaders"]

ENVIRONMENT_HEADER = "square-environment"
"""``Production`` or ``Sandbox``.

https://developer.squareup.com/docs/webhooks/build-with-webhooks. The value is
read from the resolved vendor config on every attempt, so switching a profile's
``environment`` takes effect on the next delivery rather than on the next
process.
"""


class SquareDeliveryHeaders:
    """Satisfies ``DeliveryHeaderProvider``. Holds the vendor, not its config.

    Live because the profile's ``vendor`` block resolves in ``hydrate``, which
    runs after this object exists; a captured environment would make
    ``square-environment`` say the opposite of what the operator configured.
    """

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def headers(self, meta: DeliveryMetadata) -> Mapping[str, str]:
        """Every non-signature header for one attempt.

        A missing ``retry_reason`` on a retry emits no reason header rather
        than a plausible default: the core leaves it ``None`` only when it
        genuinely does not know why the previous attempt failed, and inventing
        ``other_error`` there would tell a consumer something untrue.
        """
        out = {
            "content-type": CONTENT_TYPE,
            ENVIRONMENT_HEADER: self._deps.config.environment,
            INITIAL_DELIVERY_HEADER: meta.initial_delivery_at,
        }
        if meta.is_retry:
            out[RETRY_NUMBER_HEADER] = str(meta.retry_number)
            if meta.retry_reason is not None:
                out[RETRY_REASON_HEADER] = RETRY_REASONS[meta.retry_reason]
        return out
