"""Every response a unit gives, checked against the vendor's schema for it, so the vendor's own test suite enforces "the fake answers what the vendor documents" for free: wrapping the in-process client once turns each existing call into a schema check, and a body that drifts from the published shape fails the test that produced it, naming the JSON pointer that is wrong.

The wrapper observes; it never interprets. ``call()`` returns the very :class:`InProcessResponse` the plain client would have returned, and the only way it can change a test is by raising -- read after the unit has answered, so nothing here can affect what the unit does.

Three things are deliberately not validated, and each is counted rather than dropped: a route the declaration excuses, a control-plane route, and a request nothing routed. A route that is none of those and not in the extract is undeclared, and undeclared raises.

The classification itself lives in :class:`ResponseValidator`, which reads a ``UnitRequest`` and the ``UnitResponse`` the unit gave for it and nothing else, so the same check runs behind the in-process client (:class:`ValidatingClient`) and behind a socket (``vendorfake serve --validate`` hands it to the ASGI binding as a ``ResponseObserver``).

Validation is ``openapi_schema_validator``'s OAS 3.0 dialect with a ``referencing`` registry that holds the extract document under one URI, so every ``#/components/schemas/...`` reference resolves against the extract and nothing else. Validators are built once per operation and status and reused for the life of the validator. Request bodies use the same machinery through the ``Write`` reader of that dialect, which is what OAS 3.0 means by a body being sent rather than received.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from jsonschema.exceptions import best_match
from openapi_schema_validator import OAS30ReadValidator, OAS30WriteValidator, oas30_format_checker
from referencing import Registry
from referencing.jsonschema import DRAFT4

from vendorfake.core.kernel.reply import decode_body
from vendorfake.core.kernel.router import Match, Router
from vendorfake.core.kernel.types import UnitRequest, UnitResponse
from vendorfake.core.kernel.unit import Unit
from vendorfake.core.transport.inprocess import InProcessClient, InProcessResponse
from vendorfake.fidelity.types import Operation, Surface, route_key

__all__ = [
    "COUNTERS",
    "EXTRACT_URI",
    "Counter",
    "FidelityViolation",
    "Ledger",
    "LedgerRow",
    "ResponseValidator",
    "UndeclaredRoute",
    "ValidatingClient",
]

EXTRACT_URI = "urn:vendorfake:extract"
"""The one URI the extract document is registered under. Never fetched; a ``$ref`` to it is a pointer into the document the client was built with."""

BODY_EXCERPT_CHARS = 400
"""How much of an offending body a violation quotes -- enough to see the shape, not enough to drown the pointer list."""

Counter = Literal[
    "validated",
    "deviated",
    "excused",
    "internal",
    "undeclared",
    "undeclared_status",
    "unmatched",
    "skipped_non_json",
    "request_validated",
    "request_deviated",
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
    "request_validated",
    "request_deviated",
)
"""Every outcome one call can have, in the order the report prints them. ``skipped_non_json`` is an operation route whose body had no JSON to check; ``unmatched`` is a request the router answered with its own 404 or 405, so there is no route to classify. The last two are about the *request* and so are counted alongside a call's outcome rather than instead of it."""


Subject = Literal["response body", "request body"]
"""Which half of the exchange a violation is about. Carried in the message, never in a deviation: a deviation names a pointer and a keyword, and those are already unambiguous."""


class FidelityViolation(AssertionError):
    """A response -- or, under ``validate_requests``, an accepted request -- that does not match the vendor's schema for it. An ``AssertionError`` so pytest renders it as a failed assertion of the test that made the call, rather than an error in fixture code."""

    def __init__(
        self,
        route_key: str,
        *,
        operation_key: str | None,
        status: int,
        errors: Sequence[str],
        body_excerpt: str = "",
        subject: Subject = "response body",
    ) -> None:
        self.route_key = route_key
        self.operation_key = operation_key
        self.status = status
        self.errors: tuple[str, ...] = tuple(errors)
        self.body_excerpt = body_excerpt
        self.subject: Subject = subject
        super().__init__(self._render())

    def _headline(self) -> str:
        against = f" against {self.operation_key}" if self.operation_key else ""
        noun = "error" if len(self.errors) == 1 else "errors"
        count = f"({len(self.errors)} {noun}):"
        if self.subject == "request body":
            # The unit accepted it; the vendor's own schema would not have.
            return f"{self.route_key} accepted a request body that fails{against} and answered {self.status} {count}"
        return f"{self.route_key} answered {self.status} with a body that fails{against} {count}"

    def _render(self) -> str:
        lines = [self._headline(), *(f"  {line}" for line in self.errors)]
        if self.body_excerpt:
            lines.append(f"  {self.subject}: {self.body_excerpt}")
        return "\n".join(lines)


class UndeclaredRoute(FidelityViolation):
    """A vendor route that is neither in the extract nor excused. Raised on the first call to such a route, not when the client is built, so the failing test is the one that exercises it."""

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
    #: A 4xx/5xx the document does not declare for the route, validated against the declaration's ``error_schema``.
    undeclared_status: int = 0
    unmatched: int = 0
    skipped_non_json: int = 0
    #: Request bodies checked against the operation's ``requestBody`` schema, under ``validate_requests``.
    request_validated: int = 0
    #: Request-body schema errors a declared deviation absorbed.
    request_deviated: int = 0

    @property
    def calls(self) -> int:
        return self.validated + self.excused + self.internal + self.undeclared + self.unmatched + self.skipped_non_json


class Ledger:
    """What happened to every call, per route key. Shared between clients on purpose, so the count is a number about the whole session, not whichever fixture happened to be last."""

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


class ResponseValidator:
    """The check itself, over one ``UnitRequest`` and the ``UnitResponse`` the unit gave for it -- nothing else, so the same instance serves the in-process client and a socket binding that hands it over as a :data:`~vendorfake.core.kernel.types.ResponseObserver`.

    ``strict_undeclared`` is the switch between the two uses: a vendor's own test suite wants an undeclared route to fail the build, and the fidelity report wants to count it instead. ``validate_requests`` adds the second half: a request the unit *accepted* whose body the vendor's own schema rejects.
    """

    __slots__ = (
        "_built",
        "_ledger",
        "_registry",
        "_request_validators",
        "_router",
        "_strict_undeclared",
        "_surface",
        "_undeclared_status",
        "_validate_requests",
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
        validate_requests: bool = False,
    ) -> None:
        self._surface = surface
        self._ledger = ledger if ledger is not None else Ledger()
        self._strict_undeclared = strict_undeclared
        self._validate_requests = validate_requests
        # The unit's own table, control plane included, so a call is matched exactly as the unit matched it.
        self._router = Router(unit.routes)
        # Draft-04 is the dialect OAS 3.0 schemas are written in and OAS30ReadValidator resolves under.
        resource = DRAFT4.create_resource(surface.extract.document)
        self._registry: Registry[Any] = Registry().with_resource(EXTRACT_URI, resource)
        #: Keyed by the UNIT route and status, not the spec operation: an alias maps two unit routes onto one operation.
        self._validators: dict[tuple[str, int], Any] = {}
        #: Whether the schema for (operation, status) came through the envelope fallback rather than a declared status.
        self._via_envelope: dict[tuple[str, int], bool] = {}
        #: (route, status) pairs the document never declares, answered through ``error_schema``.
        self._undeclared_status: set[tuple[str, int]] = set()
        #: One per unit route: a ``requestBody`` schema does not vary with the status.
        self._request_validators: dict[str, Any] = {}
        self._built = 0

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def surface(self) -> Surface:
        return self._surface

    @property
    def built(self) -> int:
        """Response validators constructed so far. The cache test reads this; so may a report."""
        return self._built

    # -- classification -----------------------------------------------------

    def observe(self, request: UnitRequest, response: UnitResponse) -> None:
        """Classify and check one answered exchange. Read-only: the unit has already answered, so the sole effect this can have is to raise."""
        method = request.method
        # A binding splits the query string off before routing; a hand-built request may not have.
        bare = request.path.partition("?")[0]
        outcome = self._router.match(method, bare)
        if not isinstance(outcome, Match):
            if 200 <= response.status < 300:
                # Nothing routed it and yet the unit answered success: a defect here, never a fact about the vendor.
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
                raise UndeclaredRoute(key, status=response.status, body_excerpt=_excerpt(decode_body(response)))
            self._ledger.record(key, "undeclared")
        elif classified.operation is None:
            # Reaching here means the shared types changed under this module.
            raise RuntimeError(f"{key} classified as an operation without one")
        else:
            # The request first: it is causally prior, and it is checked even where the answer carries no JSON body.
            if self._validate_requests:
                self._validate_request(key, classified.operation, request, response)
            self._validate(key, classified.operation, response)

    # -- validation ---------------------------------------------------------

    def _validate_request(self, key: str, operation: Operation, request: UnitRequest, response: UnitResponse) -> None:
        """The body the unit *accepted*, against the vendor's ``requestBody`` schema. Only a 2xx: a refused request
        is not a fidelity question, while an accepted one the vendor's schema rejects is a fake more permissive than
        the API it stands in for. An absent body has nothing to check, and another media type is another schema."""
        if not (200 <= response.status < 300) or not request.raw_body:
            return
        schema = operation.request_schema()
        if schema is None:
            return
        content_type = _header(request.headers, "content-type")
        if content_type is not None and not _is_json_media(content_type):
            return
        text = request.raw_body.decode("utf-8", errors="replace")
        try:
            instance = json.loads(text)
        except ValueError as exc:
            raise FidelityViolation(
                key,
                operation_key=operation.key,
                status=response.status,
                errors=(f"(root): body is not JSON ({exc})",),
                body_excerpt=_excerpt(text),
                subject="request body",
            ) from None
        validator = self._request_validator_for(key, operation, schema)
        if validator is None:
            raise RuntimeError(f"{operation.key}: the request schema is not in the extract document")
        errors, absorbed = self._collect(validator, instance, key)
        if errors:
            raise FidelityViolation(
                key,
                operation_key=operation.key,
                status=response.status,
                errors=errors,
                body_excerpt=_excerpt(text),
                subject="request body",
            )
        self._ledger.record(key, "request_validated")
        for label in absorbed:
            self._ledger.record(key, "request_deviated")
            self._ledger.absorb(label)

    def _collect(self, validator: Any, instance: Any, key: str) -> tuple[list[str], list[str]]:
        """The schema errors, split into the ones a declared deviation absorbs and the ones that remain."""
        errors: list[str] = []
        absorbed: list[str] = []
        declaration = self._surface.declaration
        for raw_error in validator.iter_errors(instance):
            # A nullable reference is cut as anyOf [ref, enum: [null]]; report the branch that came closest.
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
        return errors, absorbed

    def _validate(self, key: str, operation: Operation, response: UnitResponse) -> None:
        if not response.body or not _is_json(response.headers):
            self._ledger.record(key, "skipped_non_json")
            return
        text = decode_body(response)
        try:
            instance = json.loads(text)
        except ValueError as exc:
            raise FidelityViolation(
                key,
                operation_key=operation.key,
                status=response.status,
                errors=(f"(root): body is not JSON ({exc})",),
                body_excerpt=_excerpt(text),
            ) from None
        validator = self._validator_for(key, operation, response.status)
        if validator is None:
            raise FidelityViolation(
                key,
                operation_key=operation.key,
                status=response.status,
                errors=(f"no schema for status {response.status}",),
                body_excerpt=_excerpt(text),
            )
        errors, absorbed = self._collect(validator, instance, key)
        declaration = self._surface.declaration
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
                body_excerpt=_excerpt(text),
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
            # The vendor's guide documents a shape its spec does not declare here; validate against the named component instead.
            if override.schema not in self._surface.extract.schemas:
                raise RuntimeError(f"override for {key}: schema {override.schema!r} is not in the extract")
            pointer = f"/components/schemas/{override.schema}"
            schema = self._surface.extract.schemas[override.schema]
        elif schema is None and status >= 400 and error_schema is not None:
            declared = operation.raw.get("responses", {})
            if not any(k in declared for k in (str(status), f"{status // 100}XX")):
                self._undeclared_status.add(cache_key)
            # The document declares no body for the status; validate against the declaration's error document instead.
            if error_schema not in self._surface.extract.schemas:
                raise RuntimeError(f"error_schema {error_schema!r} is not in the extract's components.schemas")
            pointer = f"/components/schemas/{error_schema}"
            schema = self._surface.extract.schemas[error_schema]
        elif schema is not None:
            pointer = _schema_pointer(self._surface.extract.document, operation, schema)
            if pointer is None:
                raise RuntimeError(f"{operation.key}: the schema for status {status} is not in the extract document")
        if pointer is not None:
            # The root schema is a reference into the registered document, so every nested #/components/... resolves there.
            validator = OAS30ReadValidator(
                {"$ref": f"{EXTRACT_URI}#{pointer}"}, registry=self._registry, format_checker=oas30_format_checker
            )
            self._built += 1
        self._validators[cache_key] = validator
        return validator

    def _request_validator_for(self, key: str, operation: Operation, schema: Mapping[str, Any]) -> Any:
        """The validator for one route's ``requestBody``, built once. ``OAS30WriteValidator`` and not the read one,
        because OAS 3.0 reads ``readOnly`` and ``writeOnly`` oppositely in the two directions; everything else --
        registry, format checker, ``$ref`` into the registered extract -- is what :meth:`_validator_for` uses."""
        if key in self._request_validators:
            return self._request_validators[key]
        pointer = _request_schema_pointer(self._surface.extract.document, operation, schema)
        validator: Any = None
        if pointer is not None:
            validator = OAS30WriteValidator(
                {"$ref": f"{EXTRACT_URI}#{pointer}"}, registry=self._registry, format_checker=oas30_format_checker
            )
        self._request_validators[key] = validator
        return validator


class ValidatingClient(InProcessClient):
    """The in-process client, with every answer checked against the surface by a :class:`ResponseValidator` it owns. The client is the transport half and nothing more: what counts as a violation is decided in one place, whether the call arrived through this object or over a socket."""

    __slots__ = ("_validator",)

    def __init__(
        self,
        unit: Unit,
        surface: Surface,
        ledger: Ledger | None = None,
        *,
        strict_undeclared: bool = True,
        validate_requests: bool = False,
    ) -> None:
        super().__init__(unit)
        self._validator = ResponseValidator(
            unit,
            surface,
            ledger,
            strict_undeclared=strict_undeclared,
            validate_requests=validate_requests,
        )

    @property
    def validator(self) -> ResponseValidator:
        return self._validator

    @property
    def ledger(self) -> Ledger:
        return self._validator.ledger

    @property
    def surface(self) -> Surface:
        return self._validator.surface

    @property
    def built(self) -> int:
        """Response validators constructed so far. The cache test reads this; so may a report."""
        return self._validator.built

    def dispatch(self, request: UnitRequest) -> InProcessResponse:
        """The base client's seam: the observer is given the very request the unit answered."""
        response = super().dispatch(request)
        self._validator.observe(request, response.raw)
        return response


# -- helpers ------------------------------------------------------------------


def _is_json_media(value: str) -> bool:
    return value.split(";")[0].strip().lower() == "application/json"


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """One header, case-insensitively. Bindings lower-case before the kernel sees them; a hand-built request may not."""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _is_json(headers: Mapping[str, str]) -> bool:
    value = _header(headers, "content-type")
    return value is not None and _is_json_media(value)


def _excerpt(text: str) -> str:
    return text if len(text) <= BODY_EXCERPT_CHARS else text[:BODY_EXCERPT_CHARS] + "..."


def _escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _instance_pointer(path: Iterable[str | int]) -> str:
    """RFC 6901 pointer to the failing value; ``(root)`` when the whole body is what is wrong."""
    parts = [_escape(str(part)) for part in path]
    return "/" + "/".join(parts) if parts else "(root)"


def _schema_pointer(document: Mapping[str, Any], operation: Operation, schema: Mapping[str, Any]) -> str | None:
    """Where ``schema`` sits in ``document``, as a URI-fragment pointer. Found by identity rather than by re-deriving the status precedence, so this cannot disagree with :meth:`Operation.response_schema`."""
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


def _request_schema_pointer(document: Mapping[str, Any], operation: Operation, schema: Mapping[str, Any]) -> str | None:
    """The same lookup as :func:`_schema_pointer`, one level along: where the ``requestBody`` schema sits, found by identity so it cannot disagree with :meth:`Operation.request_schema`."""
    paths = document.get("paths")
    item = paths.get(operation.spec_path) if isinstance(paths, Mapping) else None
    if not isinstance(item, Mapping):
        return None
    for method_key, raw in item.items():
        if raw is not operation.raw or not isinstance(raw, Mapping):
            continue
        body = raw.get("requestBody")
        content = body.get("content") if isinstance(body, Mapping) else None
        if not isinstance(content, Mapping):
            return None
        for media, spec in content.items():
            if isinstance(spec, Mapping) and spec.get("schema") is schema:
                segments = ("paths", operation.spec_path, method_key, "requestBody", "content", media, "schema")
                return "/" + "/".join(quote(_escape(str(segment)), safe="~") for segment in segments)
    return None
