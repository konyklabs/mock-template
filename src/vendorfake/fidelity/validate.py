"""Every response a unit gives, checked against the vendor's schema for it.

FOR: turning "the fake answers what the vendor documents" from a claim into a
property the existing test suite enforces for free. The vendor's own tests
already drive every route thousands of times; wrapping the in-process client
once makes each of those calls a schema check against the scoped extract, so a
body that drifts from the published shape fails the test that produced it, at
the line that produced it, naming the JSON pointer that is wrong.

INVARIANT: **the wrapper observes; it never interprets.** ``call()`` returns
the very :class:`InProcessResponse` the plain client would have returned --
same object, same bytes, same headers -- and the only way it can change a test
is by raising. It reads the response *after* the unit has answered, so nothing
here can affect what the unit does, and a test that passed without the wrapper
either still passes or fails with a :class:`FidelityViolation` that says why.

Three things are deliberately *not* validated, and each is counted rather than
dropped so the report can show the gap: a route the declaration excuses, a
control-plane route, and a request nothing routed (the unit's own 404/405). A
route that is none of those and not in the extract is undeclared, and
undeclared raises -- ``types.py`` explains why that kind has no reason field.

Validation is ``openapi_schema_validator``'s OAS 3.0 dialect (``nullable``,
draft-04 ``required``) with a ``referencing`` registry that holds the extract
document under one URI, so every ``#/components/schemas/...`` reference inside
a response schema resolves against the extract and nothing else. Validators
are built once per operation and status and reused for the life of the client.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from jsonschema.exceptions import best_match
from openapi_schema_validator import OAS30ReadValidator, oas30_format_checker
from referencing import Registry
from referencing.jsonschema import DRAFT4

from vendorfake.core.kernel.router import Match, Router
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import TRANSPORT, InProcessClient, InProcessResponse
from vendorfake.fidelity.types import Operation, Surface, route_key

__all__ = [
    "COUNTERS",
    "EXTRACT_URI",
    "Counter",
    "FidelityViolation",
    "Ledger",
    "LedgerRow",
    "UndeclaredRoute",
    "ValidatingClient",
]

EXTRACT_URI = "urn:vendorfake:extract"
"""The one URI the extract document is registered under. Never fetched; a
``$ref`` to it is a pointer into the document the client was built with."""

BODY_EXCERPT_CHARS = 400
"""How much of an offending body a violation quotes. Enough to see the shape,
not enough to drown the pointer list under a paginated listing."""

Counter = Literal[
    "validated", "deviated", "excused", "internal", "undeclared", "undeclared_status", "unmatched", "skipped_non_json"
]
COUNTERS: tuple[Counter, ...] = (
    "validated",
    "deviated",
    "excused",
    "internal",
    "undeclared",
    "undeclared_status",
    "unmatched",
    "skipped_non_json",
)
"""Every outcome one call can have, in the order the report prints them.

``validated`` is a JSON body checked against a schema and found conforming;
``skipped_non_json`` is an operation route whose body had no JSON to check
(empty, or another content type); ``unmatched`` is a request the router
answered with its own 404 or 405, so there is no route to classify."""


class FidelityViolation(AssertionError):
    """A response that does not match the vendor's schema for it.

    An ``AssertionError`` so that pytest renders it as a failed assertion of
    the test that made the call, rather than as an error in fixture code. The
    fields are what the report and a reader need: which route answered, which
    operation the schema belongs to (they differ for an alias), the status, one
    line per error as ``"<json-pointer>: <message>"``, and the head of the body.
    """

    def __init__(
        self,
        route_key: str,
        *,
        operation_key: str | None,
        status: int,
        errors: Sequence[str],
        body_excerpt: str = "",
    ) -> None:
        self.route_key = route_key
        self.operation_key = operation_key
        self.status = status
        self.errors: tuple[str, ...] = tuple(errors)
        self.body_excerpt = body_excerpt
        super().__init__(self._render())

    def _headline(self) -> str:
        against = f" against {self.operation_key}" if self.operation_key else ""
        noun = "error" if len(self.errors) == 1 else "errors"
        return f"{self.route_key} answered {self.status} with a body that fails{against} ({len(self.errors)} {noun}):"

    def _render(self) -> str:
        lines = [self._headline(), *(f"  {line}" for line in self.errors)]
        if self.body_excerpt:
            lines.append(f"  body: {self.body_excerpt}")
        return "\n".join(lines)


class UndeclaredRoute(FidelityViolation):
    """A vendor route that is neither in the extract nor excused.

    Raised on the first call to such a route, not when the client is built,
    so that the failing test is the one that exercises the route -- which is
    also the test whose author is best placed to add the alias or the excuse.
    """

    REASON = "route is not in the extract and the declaration does not excuse it"

    def __init__(self, route_key: str, *, status: int, body_excerpt: str = "") -> None:
        super().__init__(
            route_key,
            operation_key=None,
            status=status,
            errors=(self.REASON,),
            body_excerpt=body_excerpt,
        )

    def _headline(self) -> str:
        return f"{self.route_key} is UNDECLARED (answered {self.status}):"


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One route's counts, for the report."""

    key: str
    validated: int = 0
    #: Schema errors excused by a declared deviation, inside validated responses.
    deviated: int = 0
    excused: int = 0
    internal: int = 0
    undeclared: int = 0
    #: A 4xx/5xx the document does not declare for the route, validated against
    #: the vendor's error document by the declaration's ``error_schema``. The
    #: shape was checked; that the status exists there is the unit's judgment.
    undeclared_status: int = 0
    unmatched: int = 0
    skipped_non_json: int = 0

    @property
    def calls(self) -> int:
        return self.validated + self.excused + self.internal + self.undeclared + self.unmatched + self.skipped_non_json


class Ledger:
    """What happened to every call, per route key.

    Shared between clients on purpose: a test session builds one ledger, hands
    it to every :class:`ValidatingClient` it constructs, and prints the summary
    once at the end -- so the number of validated calls is a number about the
    *session*, not about whichever fixture happened to be last.
    """

    __slots__ = ("_absorbed", "_rows")

    def __init__(self) -> None:
        self._rows: dict[str, dict[Counter, int]] = {}
        self._absorbed: dict[str, int] = {}

    def absorb(self, label: str) -> None:
        """One schema error excused by the deviation ``label`` names."""
        self._absorbed[label] = self._absorbed.get(label, 0) + 1

    def absorbed(self) -> tuple[tuple[str, int], ...]:
        """Which deviations carried responses, and how many errors each absorbed."""
        return tuple(sorted(self._absorbed.items()))

    def record(self, key: str, counter: Counter) -> None:
        if counter not in COUNTERS:
            raise ValueError(f"unknown ledger counter {counter!r}; expected one of {', '.join(COUNTERS)}")
        counts = self._rows.setdefault(key, {})
        counts[counter] = counts.get(counter, 0) + 1

    def row(self, key: str) -> LedgerRow:
        """The row for one route key; all zeros if it was never called."""
        return LedgerRow(key, **self._rows.get(key, {}))

    def rows(self) -> tuple[LedgerRow, ...]:
        """Every route key seen, in key order, so two runs print the same table."""
        return tuple(self.row(key) for key in sorted(self._rows))

    def total(self, counter: Counter) -> int:
        return sum(counts.get(counter, 0) for counts in self._rows.values())

    def summary(self) -> str:
        """One line: every counter's total and the number of routes touched."""
        parts = ", ".join(f"{self.total(counter)} {counter.replace('_', ' ')}" for counter in COUNTERS)
        noun = "route" if len(self._rows) == 1 else "routes"
        return f"fidelity: {parts} over {len(self._rows)} {noun}"


class ValidatingClient(InProcessClient):
    """The in-process client, with every answer checked against the surface.

    ``strict_undeclared`` is the switch between the two uses: a vendor's own
    test suite wants an undeclared route to fail the build, and the fidelity
    report wants to count it and print it in capitals instead.
    """

    __slots__ = (
        "_built",
        "_ledger",
        "_registry",
        "_router",
        "_strict_undeclared",
        "_surface",
        "_undeclared_status",
        "_validators",
        "_via_envelope",
    )

    def __init__(
        self,
        unit: Unit,
        surface: Surface,
        ledger: Ledger | None = None,
        *,
        strict_undeclared: bool = True,
    ) -> None:
        super().__init__(unit)
        self._surface = surface
        self._ledger = ledger if ledger is not None else Ledger()
        self._strict_undeclared = strict_undeclared
        # The unit's own table, control plane included, so a call is matched
        # exactly as the unit matched it -- same order, same 404/405 split.
        self._router = Router(unit.routes)
        # Draft-04 is the dialect OAS 3.0 schemas are written in, and the one
        # ``OAS30ReadValidator`` resolves under; registering the extract as such
        # keeps the two in agreement about what a subschema's ``id`` would mean.
        resource = DRAFT4.create_resource(surface.extract.document)
        self._registry: Registry[Any] = Registry().with_resource(EXTRACT_URI, resource)
        # ``None`` is cached too: an operation with no schema for a status is
        # a violation every time, and should cost a lookup, not a search.
        #: Keyed by the UNIT route and status, not the spec operation: an alias
        #: maps two unit routes onto one operation and an override is per route.
        self._validators: dict[tuple[str, int], Any] = {}
        #: Whether the schema for (operation, status) came through the envelope
        #: fallback rather than a declared status -- the case the error member
        #: check exists for.
        self._via_envelope: dict[tuple[str, int], bool] = {}
        #: (route, status) pairs the document never declares, answered through
        #: ``error_schema``: counted, listed by the report, never silent.
        self._undeclared_status: set[tuple[str, int]] = set()
        self._built = 0

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def surface(self) -> Surface:
        return self._surface

    @property
    def built(self) -> int:
        """Validators constructed so far. The cache test reads this; so may a report."""
        return self._built

    def call(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: object = None,
        raw_body: bytes | str | None = None,
        transport: str = TRANSPORT,
        request_id: str | None = None,
    ) -> InProcessResponse:
        response = super().call(
            method=method,
            path=path,
            query=query,
            headers=headers,
            body=body,
            raw_body=raw_body,
            transport=transport,
            request_id=request_id,
        )
        self._check(method, path, response)
        return response

    # -- classification -----------------------------------------------------

    def _check(self, method: str, path: str, response: InProcessResponse) -> None:
        # The kernel splits a query string off the path before routing
        # (``make_request``); match what it matched, not what the caller typed.
        bare = path.partition("?")[0]
        outcome = self._router.match(method, bare)
        if not isinstance(outcome, Match):
            if 200 <= response.status < 300:
                # Nothing routed it and yet the unit answered success: the
                # wrapper and the kernel disagree about routing, which is a
                # defect here, never a fact about the vendor.
                raise RuntimeError(
                    f"{route_key(method, bare)} answered {response.status} but matched no route in the validator"
                )
            self._ledger.record(route_key(method, bare), "unmatched")
            return
        classified = self._surface.classify(outcome.route)
        key = classified.key
        if classified.kind == "internal":
            self._ledger.record(key, "internal")
        elif classified.kind == "excused":
            self._ledger.record(key, "excused")
        elif classified.kind == "undeclared":
            if self._strict_undeclared:
                raise UndeclaredRoute(key, status=response.status, body_excerpt=_excerpt(response))
            self._ledger.record(key, "undeclared")
        elif classified.operation is None:
            # ``Surface.classify`` pairs the kind with the operation; reaching
            # here means the shared types changed under this module.
            raise RuntimeError(f"{key} classified as an operation without one")
        else:
            self._validate(key, classified.operation, response)

    # -- validation ---------------------------------------------------------

    def _validate(self, key: str, operation: Operation, response: InProcessResponse) -> None:
        if not response.body or not _is_json(response.headers):
            self._ledger.record(key, "skipped_non_json")
            return
        try:
            instance = json.loads(response.text)
        except ValueError as exc:
            raise FidelityViolation(
                key,
                operation_key=operation.key,
                status=response.status,
                errors=(f"(root): body is not JSON ({exc})",),
                body_excerpt=_excerpt(response),
            ) from None
        validator = self._validator_for(key, operation, response.status)
        if validator is None:
            raise FidelityViolation(
                key,
                operation_key=operation.key,
                status=response.status,
                errors=(f"no schema for status {response.status}",),
                body_excerpt=_excerpt(response),
            )
        errors: list[str] = []
        absorbed: list[str] = []
        declaration = self._surface.declaration
        for raw_error in validator.iter_errors(instance):
            # A nullable reference is cut as ``anyOf: [ref, enum: [null]]``
            # (see extract.py); the anyOf's own message says only "not valid
            # under any of the given schemas", so report the branch that came
            # closest, which is the reference's own error.
            error = best_match(raw_error.context) if raw_error.context else raw_error
            pointer = _instance_pointer(error.absolute_path)
            excused_by = next(
                (
                    deviation
                    for deviation in declaration.deviations
                    if deviation.matches(
                        keyword=str(error.validator), pointer=pointer, instance=error.instance, route_key=key
                    )
                ),
                None,
            )
            if excused_by is not None:
                absorbed.append(excused_by.label)
                continue
            errors.append(f"{pointer}: {error.message}")
        if response.status >= 400 and self._via_envelope.get((key, response.status)):
            member = declaration.error_member
            carried = instance.get(member) if isinstance(instance, Mapping) and member else None
            if not (isinstance(carried, list) and carried):
                errors.append(
                    f"/{member}: status {response.status} answered through the {declaration.error_envelope} "
                    f"envelope must carry a non-empty {member!r} (the success schema requires nothing)"
                )
        errors.sort()
        if errors:
            raise FidelityViolation(
                key,
                operation_key=operation.key,
                status=response.status,
                errors=errors,
                body_excerpt=_excerpt(response),
            )
        self._ledger.record(key, "validated")
        if (key, response.status) in self._undeclared_status:
            self._ledger.record(key, "undeclared_status")
        for label in absorbed:
            self._ledger.record(key, "deviated")
            self._ledger.absorb(label)

    def _validator_for(self, key: str, operation: Operation, status: int) -> Any:
        cache_key = (key, status)
        if cache_key in self._validators:
            return self._validators[cache_key]
        envelope = self._surface.declaration.error_envelope
        schema = operation.response_schema(status, error_envelope=envelope)
        override = self._surface.declaration.override_for(key, status)
        declared = operation.raw.get("responses", {})
        self._via_envelope[cache_key] = (
            schema is not None
            and envelope is not None
            and not any(k in declared for k in (str(status), f"{status // 100}XX", "default"))
        )
        validator: Any = None
        error_schema = self._surface.declaration.error_schema
        pointer: str | None = None
        if override is not None:
            # The vendor's guide documents a shape its spec does not declare
            # for this one route and status; validate against the named
            # component instead. The report lists every override.
            if override.schema not in self._surface.extract.schemas:
                raise RuntimeError(f"override for {key}: schema {override.schema!r} is not in the extract")
            pointer = f"/components/schemas/{override.schema}"
            schema = self._surface.extract.schemas[override.schema]
        elif schema is None and status >= 400 and error_schema is not None:
            declared = operation.raw.get("responses", {})
            if not any(k in declared for k in (str(status), f"{status // 100}XX")):
                self._undeclared_status.add(cache_key)
            # The document declares the status without a body (or not at
            # all) and the declaration names the vendor's error document:
            # validate against that. Kept as a root of the extract for this.
            if error_schema not in self._surface.extract.schemas:
                raise RuntimeError(f"error_schema {error_schema!r} is not in the extract's components.schemas")
            pointer = f"/components/schemas/{error_schema}"
            schema = self._surface.extract.schemas[error_schema]
        elif schema is not None:
            pointer = _schema_pointer(self._surface.extract.document, operation, schema)
            if pointer is None:
                raise RuntimeError(f"{operation.key}: the schema for status {status} is not in the extract document")
        if pointer is not None:
            # The root schema is a reference *into* the registered document,
            # so the resolver is rebased onto the extract before it reads the
            # first keyword and every nested ``#/components/...`` resolves there.
            validator = OAS30ReadValidator(
                {"$ref": f"{EXTRACT_URI}#{pointer}"}, registry=self._registry, format_checker=oas30_format_checker
            )
            self._built += 1
        self._validators[cache_key] = validator
        return validator


# -- helpers ------------------------------------------------------------------


def _is_json(headers: Mapping[str, str]) -> bool:
    for name, value in headers.items():
        if name.lower() == "content-type":
            return value.split(";")[0].strip().lower() == "application/json"
    return False


def _excerpt(response: InProcessResponse) -> str:
    text = response.text
    return text if len(text) <= BODY_EXCERPT_CHARS else text[:BODY_EXCERPT_CHARS] + "..."


def _escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _instance_pointer(path: Iterable[str | int]) -> str:
    """RFC 6901 pointer to the failing value: ``/order/line_items/0/quantity``;
    ``(root)`` when the whole body is what is wrong."""
    parts = [_escape(str(part)) for part in path]
    return "/" + "/".join(parts) if parts else "(root)"


def _schema_pointer(document: Mapping[str, Any], operation: Operation, schema: Mapping[str, Any]) -> str | None:
    """Where ``schema`` sits in ``document``, as a URI-fragment pointer.

    Found by identity rather than by re-deriving the status precedence, so this
    cannot disagree with :meth:`Operation.response_schema` about which response
    was chosen. Segments are percent-encoded because the pointer travels as a
    URI fragment and the resolver decodes it on the way in.
    """
    paths = document.get("paths")
    item = paths.get(operation.spec_path) if isinstance(paths, Mapping) else None
    if not isinstance(item, Mapping):
        return None
    for method_key, raw in item.items():
        if raw is not operation.raw or not isinstance(raw, Mapping):
            continue
        responses = raw.get("responses")
        if not isinstance(responses, Mapping):
            return None
        for status_key, response in responses.items():
            content = response.get("content") if isinstance(response, Mapping) else None
            if not isinstance(content, Mapping):
                continue
            for media, body in content.items():
                if isinstance(body, Mapping) and body.get("schema") is schema:
                    segments = (
                        "paths",
                        operation.spec_path,
                        method_key,
                        "responses",
                        status_key,
                        "content",
                        media,
                        "schema",
                    )
                    return "/" + "/".join(quote(_escape(str(segment)), safe="~") for segment in segments)
    return None
