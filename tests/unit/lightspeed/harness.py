"""One started Lightspeed unit, driven in process, for the behaviour suites.

Thin: it knows the seeded credentials and the version prefix, and seeds nothing
itself.

EVERY RESPONSE IS SCHEMA-VALIDATED, as the Toast and Square harnesses do it.
The client below is ``vendorfake.fidelity``'s ``ValidatingClient`` over the
committed extract of ``api-2026-07.yaml``, so each of this suite's several
hundred calls is also a check that the body matches the vendor's published
schema for that operation and status (D-006). Slice L3 of
konyklabs/roadmap#94 added the extract; before it existed this harness used
the plain client, deliberately, so that nothing here claimed a check it was
not making.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from vendorfake import create_unit
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import InProcessClient, InProcessResponse, in_process
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.fidelity import Surface, load_declaration, load_extract
from vendorfake.fidelity.validate import Ledger, ValidatingClient
from vendorfake.lightspeed.entities import COL, TokenEntity
from vendorfake.lightspeed.seed.constants import (
    SEED_ACCESS_TOKEN,
    SEED_CLIENT_ID,
    SEED_CLIENT_SECRET,
    SEED_PERSONAL_ACCESS_TOKEN,
    SEED_READ_ONLY_ACCESS_TOKEN,
    SEED_REFRESH_TOKEN,
)
from vendorfake.lightspeed.surface.common import API_PREFIX, TOKEN_PREFIX

CLIENT_ID = SEED_CLIENT_ID
CLIENT_SECRET = SEED_CLIENT_SECRET
TOKEN_PATH = f"{TOKEN_PREFIX}/token"
FORM = {"content-type": "application/x-www-form-urlencoded"}

SEED_META = {"operation_id": "TestSeed", "seed": True}

FIDELITY_ANCHOR = "vendorfake.lightspeed.fidelity"
SURFACE = Surface(load_declaration(FIDELITY_ANCHOR), load_extract(FIDELITY_ANCHOR))
LEDGER = Ledger()
"""The extract is committed beside the declaration (``vendored: true``: the
specification is published under Apache 2.0), so building this costs one file
read and never the network -- unlike Toast, whose extract is cut from a fetch
into a local cache."""

#: Far enough out that no test's clock reaches it.
NEVER_MS = 2**53


class Silent:
    """A logger that says nothing, so a passing run prints no unit banner."""

    def debug(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def info(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def warn(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...
    def error(self, msg: str, fields: Mapping[str, Any] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class Harness:
    """A started unit, the client that drives it, and the seeded headers."""

    unit: Unit
    api: InProcessClient
    auth: dict[str, str]
    sink: MemorySink

    @property
    def read_auth(self) -> dict[str, str]:
        """The seeded token that cannot write."""
        return {"authorization": f"Bearer {SEED_READ_ONLY_ACCESS_TOKEN}"}

    @property
    def personal_auth(self) -> dict[str, str]:
        """The seeded personal token: full scopes, no expiry."""
        return {"authorization": f"Bearer {SEED_PERSONAL_ACCESS_TOKEN}"}

    @staticmethod
    def path(suffix: str = "") -> str:
        """``/api/2026-07`` plus ``suffix``."""
        return f"{API_PREFIX}{suffix}"

    def get(self, path: str, **kwargs: Any) -> InProcessResponse:
        kwargs.setdefault("headers", self.auth)
        return self.api.get(path, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> InProcessResponse:
        kwargs.setdefault("headers", self.auth)
        return self.api.post(path, body, **kwargs)

    def put(self, path: str, body: Any = None, **kwargs: Any) -> InProcessResponse:
        kwargs.setdefault("headers", self.auth)
        return self.api.put(path, body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> InProcessResponse:
        kwargs.setdefault("headers", self.auth)
        return self.api.delete(path, **kwargs)

    def form(self, path: str, fields: Mapping[str, str]) -> InProcessResponse:
        """A form-encoded POST -- how the documented token endpoint is called."""
        from urllib.parse import urlencode

        return self.api.post(path, urlencode(list(fields.items())), headers=FORM)

    def token_request(self, **fields: str) -> InProcessResponse:
        return self.form(TOKEN_PATH, fields)

    def exchange(self, code: str, **overrides: str) -> InProcessResponse:
        return self.token_request(
            **{
                "grant_type": "authorization_code",
                "code": code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                **overrides,
            }
        )

    def refresh(self, refresh_token: str = SEED_REFRESH_TOKEN, **overrides: str) -> InProcessResponse:
        return self.token_request(
            **{
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                **overrides,
            }
        )

    def restricted_token(self, *scopes: str) -> dict[str, str]:
        """A bearer carrying only ``scopes``, inserted as seed state."""
        entity = TokenEntity(
            id=f"tok_restricted_{'_'.join(s.replace(':', '-') for s in scopes) or 'none'}",
            access_token=f"restricted-{'-'.join(s.replace(':', '_') for s in scopes) or 'none'}",
            client_id=CLIENT_ID,
            scopes=scopes,
            expires_at_ms=NEVER_MS,
        )
        self.unit.context.store.collection(COL.tokens).insert(entity.to_entity(), SEED_META)
        return {"authorization": f"Bearer {entity.access_token}"}

    def journal_len(self) -> int:
        return len(self.api.get("/__unit/journal").json()["entries"])

    def deliveries(self) -> list[Any]:
        """Every delivery the sink took, after the dispatcher has settled."""
        self.unit.webhooks.quiesce()
        return list(self.sink.received)


def unvalidated(h: Harness) -> InProcessClient:
    """The plain client over the same unit, for the ONE call this suite makes
    that the vendor's own schema rejects.

    ``GET /products?include_images=false`` is documented, and ``images`` and
    ``skuImages`` are among ``Product``'s required members: the document
    contradicts itself and a fake that honours the parameter cannot satisfy the
    required list. Every use of this helper is a claim that the contradiction
    is the vendor's, and each one is paired with a test that asserts the
    violation happens -- see
    ``test_include_images_false_answers_a_body_the_vendors_own_schema_rejects``.
    """
    return in_process(h.unit)


def harness(profile: str = "full", **kwargs: Any) -> Iterator[Harness]:
    """Start a unit on ``profile``, yield it with the seeded bearer, stop it
    however the test ends.

    Defaults ``VENDORFAKE_ERROR_SIDECAR=both`` unless the caller's own ``env``
    already names it: this suite reads ``unit_error`` out of the body to assert
    on the *content* of a refusal (which field, which reason), a concern the
    sidecar's wire placement (default ``headers`` since konyklabs/roadmap#71)
    does not change.
    """
    sink = kwargs.pop("sink", None) or MemorySink()
    kwargs["env"] = {"VENDORFAKE_ERROR_SIDECAR": "both", **kwargs.pop("env", {})}
    unit = create_unit(vendor="lightspeed", profile=profile, sink=sink, logger=Silent(), **kwargs)
    try:
        yield Harness(
            unit=unit,
            # Lenient on an undeclared route, as the Toast harness is: a route
            # the extract does not describe is counted and printed in capitals
            # by `fidelity report`, which is where it should fail, rather than
            # reddening every test that happens to touch it.
            api=ValidatingClient(unit, SURFACE, LEDGER, strict_undeclared=False),
            auth={"authorization": f"Bearer {SEED_ACCESS_TOKEN}"},
            sink=sink,
        )
    finally:
        unit.stop()
