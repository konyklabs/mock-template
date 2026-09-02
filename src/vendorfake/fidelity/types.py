"""The data a vendor declares, and the two documents cut from it.

FOR: one typed reading of ``declaration.json`` and ``extract.json`` so the
validator, the corpus runner, the report and the pin tool all agree on what a
route *is* in spec terms -- an operation, an excused route, or an undeclared
one -- without any of them re-deriving it.

INVARIANT: **a vendor route is never silently unvalidated.** ``Surface.classify``
returns exactly one of four kinds for every route the unit serves, and the
only kind that carries no schema and no reason is ``undeclared`` -- which the
validator raises on and the report prints in capitals. Excusing a route costs
a sentence in the declaration; that sentence is the audit trail.

The extract is a valid OpenAPI 3.0 document restricted to the operations the
unit models and the schemas reachable from them, with prose stripped; see
``extract.py`` for how it is cut and ``pin.py`` for how it is tied to the
upstream bytes. This module only reads it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Literal

from vendorfake.core.kernel.types import Route

__all__ = [
    "DECLARATION_FILE",
    "DECLARATION_SCHEMA_FILE",
    "EXTRACT_FILE",
    "PIN_FILE",
    "Alias",
    "Classified",
    "Deviation",
    "Excuse",
    "Extract",
    "FidelityDeclaration",
    "Operation",
    "SpecSource",
    "Surface",
    "load_declaration",
    "load_extract",
    "route_key",
    "template_shape",
    "validate_declaration",
]

DECLARATION_FILE = "declaration.json"
EXTRACT_FILE = "extract.json"
PIN_FILE = "pin.json"
CORPUS_DIR = "corpus"

SourceKind = Literal["openapi3", "swagger2", "fragments"]
"""How an upstream document is fetched and read. Only ``openapi3`` is
implemented by the first leg (konyklabs/roadmap#55); ``swagger2`` is #56 and
``fragments`` is #57. Declaring a kind that
is not implemented is an error at extract time, never a silent skip."""


def route_key(method: str, path: str) -> str:
    """``"GET /v2/orders/{order_id}"`` -- the one spelling of a route this package uses."""
    return f"{method.upper()} {path}"


def template_shape(path: str) -> str:
    """A path template with every parameter name erased, so ``/v2/orders/{order_id}``
    and ``/v2/orders/{id}`` compare equal. Parameter *names* differ between a
    unit and a spec freely; parameter *positions* do not."""
    return re.sub(r"\{[^}]+\}", "{}", path)


@dataclass(frozen=True, slots=True)
class SpecSource:
    """One upstream document the extract is cut from."""

    kind: SourceKind
    url: str
    #: A prefix the spec omits and the unit's paths carry. Empty means "take
    #: it from the document" (Swagger 2 ``basePath``, OAS 3 ``servers[0].url``);
    #: set it to override what the document says.
    base_path: str = ""
    #: Short name for this source, used to namespace a schema that two sources
    #: define differently (``<label>.<Name>``). Defaults to the URL's file stem.
    label: str = ""

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> SpecSource:
        kind = row["kind"]
        if kind not in ("openapi3", "swagger2", "fragments"):
            raise ValueError(f"unknown spec source kind {kind!r}; expected openapi3, swagger2 or fragments")
        url = str(row["url"])
        label = str(row.get("label") or url.rsplit("/", 1)[-1].split(".")[0])
        return cls(kind=kind, url=url, base_path=str(row.get("base_path", "")), label=label)


@dataclass(frozen=True, slots=True)
class Alias:
    """A unit route spelled with a literal where the spec has a parameter.

    A unit may serve ``GET /things/me`` as a literal path where the spec has
    ``/things/{thing_id}`` and documents ``me`` as one accepted value. The
    alias says which operation the literal is an instance of. It is *also* a fidelity finding -- the real API
    accepts both spellings -- which is why the reason is mandatory.
    """

    method: str
    path: str
    spec_path: str
    reason: str

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Alias:
        return cls(
            method=str(row["method"]).upper(),
            path=str(row["path"]),
            spec_path=str(row["spec_path"]),
            reason=str(row["reason"]),
        )

    @property
    def key(self) -> str:
        return route_key(self.method, self.path)


@dataclass(frozen=True, slots=True)
class Excuse:
    """A vendor route the spec does not describe, with the reason it is served anyway."""

    method: str
    path: str
    reason: str

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Excuse:
        return cls(method=str(row["method"]).upper(), path=str(row["path"]), reason=str(row["reason"]))

    @property
    def key(self) -> str:
        return route_key(self.method, self.path)


@dataclass(frozen=True, slots=True)
class Deviation:
    """A place where the unit follows the vendor's prose against the vendor's spec.

    Vendors contradict themselves: a code named on a documentation page and
    quoted from real responses, but absent from the published enumeration.
    The unit follows the observed API, and this row says so, in the only
    place it can be audited -- with the page it rests on. A deviation is
    matched narrowly: one schema keyword, one instance pointer (``*`` matches
    a single segment), and optionally one value. It never widens beyond that.
    """

    pointer: str
    keyword: str
    #: The one instance value excused, kept in its JSON type: a vendor that
    #: enumerates numeric codes needs ``402`` to mean the number, not the text.
    value: object
    reason: str
    url: str
    #: Route keys (the unit's spelling) the deviation applies to; empty means
    #: every route. A code the vendor names for one flow is excused for that
    #: flow, not for the whole surface.
    routes: tuple[str, ...] = ()

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Deviation:
        value = row.get("value")
        if value is None or value == "" or isinstance(value, (dict, list)):
            raise ValueError("a deviation must name the one scalar value it excuses")
        pointer = str(row["pointer"])
        if not pointer.startswith("/") or all(segment in ("", "*") for segment in pointer.split("/")):
            raise ValueError(f"deviation pointer {pointer!r} must be absolute and name at least one real segment")
        return cls(
            pointer=pointer,
            keyword=str(row["keyword"]),
            value=value,
            reason=str(row["reason"]),
            url=str(row["url"]),
            routes=tuple(str(key) for key in row.get("routes", ())),
        )

    @property
    def label(self) -> str:
        """How the ledger and the report name this row."""
        return f"{self.keyword} {self.pointer} = {json.dumps(self.value)}"

    def matches(self, *, keyword: str, pointer: str, instance: object, route_key: str | None = None) -> bool:
        if keyword != self.keyword or instance != self.value or type(instance) is not type(self.value):
            return False
        if self.routes and (route_key is None or route_key not in self.routes):
            return False
        want = self.pointer.split("/")
        have = pointer.split("/")
        return len(want) == len(have) and all(w in ("*", h) for w, h in zip(want, have, strict=True))


@dataclass(frozen=True, slots=True)
class FidelityDeclaration:
    """``declaration.json``, read.

    ``error_envelope`` names the status whose response schema also describes
    error bodies when the spec declares none for the error status -- the
    convention of a vendor whose every response schema carries an ``errors[]``
    member and whose document declares only the success status. ``None`` means
    an undeclared status is a violation. ``error_member`` names that member:
    an error status answered through the envelope must carry it, non-empty,
    or the envelope would accept a success payload on a 404 (the success
    schema requires nothing). Declaring the envelope without the member is
    refused, because that is exactly the hole.
    """

    anchor: str
    sources: tuple[SpecSource, ...]
    aliases: tuple[Alias, ...] = ()
    excused: tuple[Excuse, ...] = ()
    deviations: tuple[Deviation, ...] = ()
    error_envelope: str | None = None
    error_member: str | None = None
    #: Schema names the extract may stub to ``{}`` because the upstream
    #: document dangles there. A stub validates everything it types, so a
    #: new one is a red offline check until it is listed here, on purpose.
    stubs_accepted: tuple[str, ...] = ()
    #: Whether the extract is committed beside the declaration. ``False`` is
    #: the fetch-never-commit mode (konyklabs/roadmap#56): only ``pin.json``
    #: (facts about the upstream bytes) ships; the extract is cut at run time
    #: from a fresh fetch into a local cache and no upstream byte enters the
    #: repository. The reason is the vendor's terms, not convenience.
    vendored: bool = True
    #: The component schema an error body is validated against when the
    #: document declares the status without a schema (or not at all). Kept
    #: in the extract as a root even if no operation references it.
    error_schema: str | None = None
    #: Vendor extension keys with a standard meaning, mapped onto the OAS
    #: keyword the validator honours (``{"x-nullable": "nullable"}``). Data,
    #: so this package names no vendor. Unmapped ``x-`` keys are stripped.
    extension_map: Mapping[str, str] = field(default_factory=dict)
    #: Values a corpus case may interpolate as ``${vars.<name>}`` -- seeded ids
    #: the vendor's scenario fixes, so a case can name them without a lookup.
    variables: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, anchor: str, doc: Mapping[str, Any]) -> FidelityDeclaration:
        if int(doc.get("schema", 0)) != 1:
            raise ValueError(f'{anchor}/{DECLARATION_FILE}: expected "schema": 1, got {doc.get("schema")!r}')
        sources = tuple(SpecSource.of(row) for row in doc.get("sources", ()))
        if not sources:
            raise ValueError(f"{anchor}/{DECLARATION_FILE}: at least one spec source is required")
        envelope = doc.get("error_envelope")
        member = doc.get("error_member")
        if envelope is not None and not member:
            raise ValueError(
                f"{anchor}/{DECLARATION_FILE}: error_envelope needs error_member -- the member an error "
                f"body must carry, or the envelope accepts a success payload on any status"
            )
        return cls(
            anchor=anchor,
            sources=sources,
            aliases=tuple(Alias.of(row) for row in doc.get("aliases", ())),
            excused=tuple(Excuse.of(row) for row in doc.get("excused", ())),
            deviations=tuple(Deviation.of(row) for row in doc.get("deviations", ())),
            error_envelope=None if envelope is None else str(envelope),
            error_member=None if member is None else str(member),
            stubs_accepted=tuple(str(n) for n in doc.get("stubs_accepted", ())),
            vendored=bool(doc.get("vendored", True)),
            error_schema=None if doc.get("error_schema") is None else str(doc["error_schema"]),
            extension_map={str(k): str(v) for k, v in dict(doc.get("extension_map", {})).items()},
            variables={str(k): str(v) for k, v in dict(doc.get("variables", {})).items()},
        )

    def alias_for(self, method: str, path: str) -> Alias | None:
        key = route_key(method, path)
        for alias in self.aliases:
            if alias.key == key:
                return alias
        return None

    def excuse_for(self, method: str, path: str) -> Excuse | None:
        key = route_key(method, path)
        for excuse in self.excused:
            if excuse.key == key:
                return excuse
        return None


@dataclass(frozen=True, slots=True)
class Operation:
    """One ``paths[path][method]`` of the extract, located."""

    method: str
    spec_path: str
    raw: Mapping[str, Any]

    @property
    def key(self) -> str:
        return route_key(self.method, self.spec_path)

    def response_schema(self, status: int, *, error_envelope: str | None = None) -> Mapping[str, Any] | None:
        """The JSON schema for ``status``: exact, then the OAS 3 range key
        (``4XX``), then ``default``, then the envelope status the declaration
        names. ``None`` when the operation has
        no JSON schema for that status anywhere -- the caller decides whether
        that is a violation."""
        responses = self.raw.get("responses", {})
        for candidate in (str(status), f"{status // 100}XX", "default", error_envelope):
            if candidate is None:
                continue
            response = responses.get(candidate)
            if not isinstance(response, Mapping):
                continue
            content = response.get("content")
            if not isinstance(content, Mapping):
                continue
            for media, body in content.items():
                if media.split(";")[0].strip().lower() == "application/json" and isinstance(body, Mapping):
                    schema = body.get("schema")
                    if isinstance(schema, Mapping):
                        return schema
        return None

    def request_schema(self) -> Mapping[str, Any] | None:
        body = self.raw.get("requestBody")
        if not isinstance(body, Mapping):
            return None
        content = body.get("content", {})
        for media, spec in content.items():
            if media.split(";")[0].strip().lower() == "application/json" and isinstance(spec, Mapping):
                schema = spec.get("schema")
                if isinstance(schema, Mapping):
                    return schema
        return None


class Extract:
    """``extract.json``, read: a scoped OpenAPI 3.0 document plus its own metadata."""

    __slots__ = ("_by_shape", "document")

    def __init__(self, document: Mapping[str, Any]) -> None:
        if document.get("openapi", "").split(".")[0] != "3":
            raise ValueError(f"an extract is an OpenAPI 3 document; got openapi={document.get('openapi')!r}")
        self.document = document
        self._by_shape: dict[str, dict[str, Operation]] = {}
        for spec_path, item in dict(document.get("paths", {})).items():
            if not isinstance(item, Mapping):
                continue
            shape = template_shape(spec_path)
            for method, raw in item.items():
                if method.lower() in ("get", "put", "post", "delete", "options", "head", "patch", "trace"):
                    self._by_shape.setdefault(shape, {})[method.upper()] = Operation(method.upper(), spec_path, raw)

    @property
    def metadata(self) -> Mapping[str, Any]:
        """The ``x-vendorfake`` block: upstream pins, stubbed refs, stripped keys."""
        meta = self.document.get("x-vendorfake", {})
        return meta if isinstance(meta, Mapping) else {}

    @property
    def schemas(self) -> Mapping[str, Any]:
        components = self.document.get("components", {})
        schemas = components.get("schemas", {}) if isinstance(components, Mapping) else {}
        return schemas if isinstance(schemas, Mapping) else {}

    def operation(self, method: str, spec_path: str) -> Operation | None:
        """The operation for a *spec-shaped* path: parameter names are ignored."""
        return self._by_shape.get(template_shape(spec_path), {}).get(method.upper())

    def operations(self) -> tuple[Operation, ...]:
        return tuple(op for by_method in self._by_shape.values() for op in by_method.values())


ClassifiedKind = Literal["operation", "excused", "internal", "undeclared"]


@dataclass(frozen=True, slots=True)
class Classified:
    """What one unit route is, in the vendor's spec terms."""

    kind: ClassifiedKind
    route: Route
    operation: Operation | None = None
    reason: str | None = None
    alias: Alias | None = None

    @property
    def key(self) -> str:
        return route_key(self.route.method, self.route.path)


class Surface:
    """A declaration and its extract, applied to a unit's route table."""

    __slots__ = ("declaration", "extract")

    def __init__(self, declaration: FidelityDeclaration, extract: Extract) -> None:
        self.declaration = declaration
        self.extract = extract

    def classify(self, route: Route) -> Classified:
        if route.internal or route.path.startswith("/__"):
            return Classified("internal", route, reason="control plane or vendor stand-in, not a vendor route")
        alias = self.declaration.alias_for(route.method, route.path)
        spec_path = alias.spec_path if alias is not None else route.path
        operation = self.extract.operation(route.method, spec_path)
        if operation is not None:
            return Classified("operation", route, operation=operation, alias=alias)
        excuse = self.declaration.excuse_for(route.method, route.path)
        if excuse is not None:
            return Classified("excused", route, reason=excuse.reason)
        return Classified("undeclared", route)

    def classify_all(self, routes: Sequence[Route]) -> tuple[Classified, ...]:
        return tuple(self.classify(route) for route in routes)


def _read_json(anchor: str, name: str) -> Any:
    try:
        text = (resources.files(anchor) / name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise FileNotFoundError(f"no {name} in package {anchor!r}: {exc}") from exc
    return json.loads(text)


DECLARATION_SCHEMA_FILE = "declaration.schema.json"


def validate_declaration(doc: Any, *, where: str) -> None:
    """The declaration against its shipped JSON Schema. A deviation without
    a value, an envelope without a member, an unknown key: refused at load,
    the way corpus cases are, so a widening cannot arrive by typo."""
    import jsonschema

    schema = json.loads((resources.files("vendorfake.fidelity") / DECLARATION_SCHEMA_FILE).read_text("utf-8"))
    problems = sorted(
        f"/{'/'.join(str(p) for p in error.absolute_path)}: {error.message}"
        for error in jsonschema.Draft202012Validator(schema).iter_errors(doc)
    )
    if problems:
        raise ValueError(f"{where}: " + "; ".join(problems))


def load_declaration(anchor: str) -> FidelityDeclaration:
    """Read ``declaration.json`` from the package named by ``anchor``
    (``"vendorfake.<vendor>.fidelity"`` for a built-in vendor). The package is
    *named*, never guessed: this module may not import the registry that
    knows vendors exist."""
    doc = _read_json(anchor, DECLARATION_FILE)
    validate_declaration(doc, where=f"{anchor}/{DECLARATION_FILE}")
    return FidelityDeclaration.of(anchor, doc)


def load_extract(anchor: str) -> Extract:
    """The extract for ``anchor``: read beside the declaration when it is
    vendored, otherwise cut from a fresh fetch into the local cache (see
    ``vendorfake.fidelity.cache``), which is the only place a non-vendored
    vendor's upstream bytes ever land."""
    declaration = load_declaration(anchor)
    if not declaration.vendored:
        from vendorfake.fidelity.cache import cached_extract

        return cached_extract(anchor, declaration)
    return Extract(_read_json(anchor, EXTRACT_FILE))
