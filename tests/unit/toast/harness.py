"""One started Toast unit, driven in process, for the behaviour suites.

Thin, like the Clover harness: it knows the seeded credentials and the
restaurant guid, and seeds nothing itself.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from vendorfake import create_unit
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import InProcessClient, InProcessResponse
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.fidelity import Surface, load_declaration, load_extract
from vendorfake.fidelity.cache import Unavailable
from vendorfake.fidelity.validate import Ledger, ValidatingClient
from vendorfake.toast.entities import COL, TokenEntity
from vendorfake.toast.seed.constants import (
    SEED_ACCESS_TOKEN,
    SEED_CLIENT_ID,
    SEED_CLIENT_SECRET,
    SEED_PARTNER_GUID,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_RESTAURANT_GUID,
)
from vendorfake.toast.surface.common import RESTAURANT_HEADER

CLIENT_ID = SEED_CLIENT_ID
CLIENT_SECRET = SEED_CLIENT_SECRET
RESTAURANT = SEED_RESTAURANT_GUID

SEED_META = {"operation_id": "TestSeed", "seed": True}


class Silent:
    """A logger that says nothing, so a passing run prints no unit banner."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


FIDELITY_ANCHOR = "vendorfake.toast.fidelity"
LEDGER = Ledger()
"""Every response a Toast test receives through a harness client is validated
against Toast's published schema for that operation and status (D-006). The
extract is never committed (konyklabs/roadmap#56): ``load_extract`` cuts it
from a fresh fetch into the local cache on first use, so a cold cache needs
the network once -- ``vendorfake-fidelity fetch`` in ``tools/self-test.sh``
pays for it before pytest runs."""

FIDELITY_UNAVAILABLE_REASON: str | None = None
"""Set at import time when the cache has no extract and the network cannot
supply one (T1, konyklabs/roadmap#116): :func:`harness` then drives every
test through the plain, unvalidated in-process client instead of failing
collection for the whole suite, and ``conftest.py``'s terminal summary prints
a SKIPPED line in place of the ledger's."""

try:
    SURFACE: Surface | None = Surface(load_declaration(FIDELITY_ANCHOR), load_extract(FIDELITY_ANCHOR))
except Unavailable as exc:
    SURFACE = None
    FIDELITY_UNAVAILABLE_REASON = str(exc)


def validating_client(unit: Unit, *, strict_undeclared: bool = True) -> InProcessClient:
    """The client every Toast test drives a unit with: validating when the extract
    is available, the plain in-process client when it is not (T1)."""
    if SURFACE is None:
        return InProcessClient(unit)
    return ValidatingClient(unit, SURFACE, LEDGER, strict_undeclared=strict_undeclared)


@dataclass(frozen=True, slots=True)
class Harness:
    """A started unit, the client that drives it, and the seeded headers."""

    unit: Unit
    api: InProcessClient
    auth: dict[str, str]

    @property
    def read_auth(self) -> dict[str, str]:
        """The seeded token that cannot write, with the restaurant header."""
        return {"authorization": f"Bearer {SEED_READ_ONLY_ACCESS_TOKEN}", RESTAURANT_HEADER.lower(): RESTAURANT}

    @property
    def bearer_only(self) -> dict[str, str]:
        return {"authorization": f"Bearer {SEED_ACCESS_TOKEN}"}

    def get(self, path: str, **kwargs: Any) -> InProcessResponse:
        return self.api.get(path, headers=self.auth, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> InProcessResponse:
        return self.api.post(path, body, headers=self.auth, **kwargs)

    def put(self, path: str, body: Any = None, **kwargs: Any) -> InProcessResponse:
        return self.api.put(path, body, headers=self.auth, **kwargs)

    def patch(self, path: str, body: Any = None, **kwargs: Any) -> InProcessResponse:
        return self.api.patch(path, body, headers=self.auth, **kwargs)

    def restricted_token(self, *scopes: str) -> dict[str, str]:
        """A bearer carrying only ``scopes``, inserted as seed state, paired
        with the restaurant header."""
        entity = TokenEntity(
            id=f"tok_restricted_{len(scopes)}",
            access_token=f"restricted-{'-'.join(s.replace(':', '_') for s in scopes) or 'none'}",
            client_id=CLIENT_ID,
            partner_guid=SEED_PARTNER_GUID,
            expires_at_ms=2**53,
            scopes=scopes,
        )
        self.unit.context.store.collection(COL.tokens).insert(entity.to_entity(), SEED_META)
        return {"authorization": f"Bearer {entity.access_token}", RESTAURANT_HEADER.lower(): RESTAURANT}

    def journal_len(self) -> int:
        return len(self.api.get("/__unit/journal").json()["entries"])


def harness(profile: str = "full", **kwargs: Any) -> Iterator[Harness]:
    """Start a unit on ``profile``, yield it with the seeded bearer and the
    restaurant header, stop it however the test ends.

    Defaults ``VENDORFAKE_ERROR_SIDECAR=both`` unless the caller's own ``env``
    already names it: this suite reads `unit_error` out of the body to assert
    on the *content* of a refusal (which field, which reason), a concern the
    sidecar's wire placement (konyklabs/roadmap#71; default ``headers`` since)
    does not change. A test of the placement itself builds its own unit --
    with :func:`vendorfake.testing.unit` or ``create_unit`` directly -- rather
    than through this harness.
    """
    kwargs.setdefault("sink", MemorySink())
    kwargs["env"] = {"VENDORFAKE_ERROR_SIDECAR": "both", **kwargs.pop("env", {})}
    unit = create_unit(vendor="toast", profile=profile, logger=Silent(), **kwargs)
    # SURFACE is None when there's no extract in the cache and no network to
    # cut one (T1, konyklabs/roadmap#116): every test then runs unvalidated
    # rather than the whole suite failing to collect for a reason none of
    # them caused. Otherwise, lenient on an undeclared route: the extract is
    # cut from TODAY's documents, so a route Toast renames must not redden
    # every test -- the report step prints it in capitals and fails there
    # instead.
    api: InProcessClient = (
        InProcessClient(unit) if SURFACE is None else ValidatingClient(unit, SURFACE, LEDGER, strict_undeclared=False)
    )
    try:
        yield Harness(
            unit=unit,
            api=api,
            auth={"authorization": f"Bearer {SEED_ACCESS_TOKEN}", RESTAURANT_HEADER.lower(): RESTAURANT},
        )
    finally:
        unit.stop()
