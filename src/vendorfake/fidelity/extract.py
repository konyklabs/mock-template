"""Cutting a scoped extract from a vendor's published API documents.

FOR: turning one or more upstream documents -- OpenAPI 3 or Swagger 2.0, JSON
or YAML, megabytes of prose and tooling annotations -- into the smallest valid
OpenAPI 3 document that still says everything the validator needs about the
operations a unit models, so the extract can be committed or cached, diffed by
a human, and read by one validator whatever dialect the vendor published in.

INVARIANT: **the cut is a pure function of its inputs.** Same upstream bytes,
same modeled list, same declaration values, same ``fetched`` string --
byte-identical output, every time, on every machine. Nothing here reads a
clock, the network or the file system; ``pin.py`` and ``cache.py`` do the
fetching and writing and hand bytes in. That is what lets ``pin --check``
compare a fresh cut against the pinned one and call any difference drift.

SECOND INVARIANT: **prose is stripped structurally, never by key name alone.**
A schema may have a *property* called ``description`` or ``title`` (Square's
``CatalogItem`` does), and stripping by name would delete the property rather
than the annotation. The stripper knows where in a schema the keys are
annotations and where they are names. The same knowledge is what renames a
mapped vendor extension (``x-nullable`` to ``nullable``) only where it is a
schema keyword.

THIRD INVARIANT: **a Swagger 2.0 source is converted to the OAS 3 shape before
it is cut, and the cutter sees only that shape.** ``definitions`` become
``components.schemas`` with every reference rewritten, a ``body`` parameter
becomes ``requestBody``, a response ``schema`` moves under ``content`` with
the media type ``produces``/``consumes`` name, a non-body parameter's type
keywords move under ``schema``, and ``basePath`` becomes the source's base
path. The validator, the report and every test read one dialect.

What is kept, exactly: per operation ``operationId``, ``deprecated``,
``parameters`` (name, in, required, schema), ``requestBody.required`` and
``requestBody.content.<media>.schema``, and
``responses.<status>.content.<media>.schema``, with each response's required
``description`` set to the status string. ``components.schemas`` holds the
schemas transitively reachable from the kept operations -- and from the
declared error schema, whether or not an operation references it -- through
``#/components/schemas/<name>`` references; a reference to a name no source
defines becomes ``{}`` at the component and is listed under
``x-vendorfake.stubbed`` so the hole is visible rather than silent. Stripped
everywhere: ``description``, ``summary``, ``example``, ``examples``,
``externalDocs``, ``title`` inside schemas, and every ``x-*`` key other than
those the declaration maps and the ``x-vendorfake`` block this module writes.

Several sources: an operation is looked up in declaration order and the first
source that has it wins. Schemas merge by name: identical definitions dedupe;
a name a later source defines *differently* is namespaced ``<label>.<Name>``
-- in that source's closure and in its operations, and in every schema of that
closure whose references moved with it -- and listed under
``x-vendorfake.namespaced``. A name a later source references but only an
earlier one defines resolves to that definition rather than a stub.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import yaml

from vendorfake.fidelity.types import SpecSource, route_key, template_shape


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader without YAML 1.1's ``yes/no/on/off`` booleans.

    A vendor's enum of ``YES``/``NO`` strings is a boolean pair under YAML 1.1,
    which is what PyYAML implements; the extract would then hold ``[false, true]``
    where the document says ``[YES, NO]`` and every conforming response would
    fail the enum. Only ``true``/``false`` (any case) are booleans here, as in
    YAML 1.2 and JSON.
    """


_StrictSafeLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)

__all__ = [
    "EXTRACT_SCHEMA",
    "SCHEMA_REF_PREFIX",
    "STRIPPED_KEYS",
    "cut_extract",
    "render_json",
    "sha256_hex",
]

EXTRACT_SCHEMA = 1
"""The ``x-vendorfake.schema`` value this cutter writes."""

SCHEMA_REF_PREFIX = "#/components/schemas/"

STRIPPED_KEYS = frozenset({"description", "summary", "example", "examples", "externalDocs", "title"})
"""Annotation keys removed wherever they are annotations (see the second invariant)."""

_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

#: Schema keywords whose value is one schema.
_SINGLE_SCHEMA_KEYS = ("items", "additionalProperties", "not")
#: Schema keywords whose value is a list of schemas.
_SCHEMA_LIST_KEYS = ("allOf", "oneOf", "anyOf")
#: Schema keywords whose value maps *names* to schemas; the names are never stripped.
_NAMED_SCHEMA_KEYS = ("properties", "patternProperties")

#: The OAS version a converted Swagger 2.0 document declares.
_CONVERTED_OPENAPI = "3.0.3"
_JSON_MEDIA = "application/json"
_FORM_MEDIA = "application/x-www-form-urlencoded"
#: Swagger 2.0 reusable sections and where OAS 3 keeps them.
_SWAGGER2_REF_PREFIXES = (
    ("#/definitions/", SCHEMA_REF_PREFIX),
    ("#/parameters/", "#/components/parameters/"),
    ("#/responses/", "#/components/responses/"),
)
#: Swagger 2.0 parameter fields that stay on the parameter row in OAS 3.
_PARAMETER_ROW_KEYS = frozenset({"name", "in", "required"})
#: Swagger 2.0 parameter fields with no schema meaning; dropped and ledgered.
_PARAMETER_DROPPED_KEYS = frozenset({"description", "collectionFormat", "allowEmptyValue"})
#: Swagger 2.0 operation fields the conversion consumes rather than carries.
_OPERATION_CONSUMED_KEYS = frozenset({"parameters", "responses", "consumes", "produces"})


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_json(document: Mapping[str, Any]) -> str:
    """The one serialisation every generated fidelity file uses: sorted keys,
    two-space indent, one trailing newline. Byte-identical for equal inputs."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


class _Cut:
    """Working state for one cut: the ledgers and the extension map."""

    __slots__ = ("extension_map", "nullable_refs", "renamed", "stripped", "stubbed")

    def __init__(self, extension_map: Mapping[str, str]) -> None:
        self.extension_map = dict(extension_map)
        self.stripped: set[str] = set()
        self.stubbed: set[str] = set()
        #: ``{"$ref": X, "nullable": true}`` nodes rewritten; see ``clean_schema``.
        self.nullable_refs = 0
        #: Mapped extension keys renamed to their OAS keyword, counted per key.
        self.renamed: dict[str, int] = {}

    def drop(self, key: str) -> None:
        self.stripped.add(key)

    def clean_schema(self, node: Any) -> Any:
        """A schema with its annotations removed, structure intact."""
        if isinstance(node, list):
            return [self.clean_schema(item) for item in node]
        if not isinstance(node, Mapping):
            return copy.deepcopy(node)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in self.extension_map:
                # A vendor extension with a standard meaning becomes the OAS
                # keyword, ahead of the x- strip below. A schema that spells
                # the keyword out as well keeps its own value.
                self.renamed[key] = self.renamed.get(key, 0) + 1
                if self.extension_map[key] not in node:
                    out[self.extension_map[key]] = copy.deepcopy(value)
            elif key in STRIPPED_KEYS or key.startswith("x-"):
                self.drop(key)
            elif key in _NAMED_SCHEMA_KEYS and isinstance(value, Mapping):
                out[key] = {name: self.clean_schema(schema) for name, schema in value.items()}
            elif key in _SINGLE_SCHEMA_KEYS:
                out[key] = self.clean_schema(value)
            elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
                out[key] = [self.clean_schema(schema) for schema in value]
            else:
                out[key] = copy.deepcopy(value)
        if isinstance(out.get("$ref"), str) and out.get("nullable") is True:
            # OAS 3.0's ``nullable`` acts within one schema object and a
            # ``$ref`` sibling is ignored by validators, so a legal null next
            # to a reference would fail. The equivalent the validator honours:
            # either the referenced schema, or exactly null.
            self.nullable_refs += 1
            return {"anyOf": [{"$ref": out["$ref"]}, {"enum": [None]}]}
        return out


def _schema_refs(node: Any, into: set[str]) -> None:
    """Every ``#/components/schemas/<name>`` referenced anywhere under ``node``."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(SCHEMA_REF_PREFIX):
                into.add(value[len(SCHEMA_REF_PREFIX) :])
            else:
                _schema_refs(value, into)
    elif isinstance(node, list):
        for item in node:
            _schema_refs(item, into)


def _rewrite_refs(node: Any, rewrite: Callable[[str], str]) -> Any:
    """A copy of ``node`` with every ``$ref`` string passed through ``rewrite``."""
    if isinstance(node, Mapping):
        return {
            key: rewrite(value) if key == "$ref" and isinstance(value, str) else _rewrite_refs(value, rewrite)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(item, rewrite) for item in node]
    return node


def _resolve_local(document: Mapping[str, Any], node: Any) -> Any:
    """Follow a ``#/components/<kind>/<name>`` reference on a non-schema object
    (parameter, request body, response) so the extract need not ship those
    component sections. Schema references are left alone: they are the
    closure's job."""
    if not isinstance(node, Mapping):
        return node
    ref = node.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/") or ref.startswith(SCHEMA_REF_PREFIX):
        return node
    target: Any = document
    for step in ref[2:].split("/"):
        step = step.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or step not in target:
            raise ValueError(f"upstream document references {ref!r}, which it does not define")
        target = target[step]
    return target


# ---------------------------------------------------------------------------
# Loading: bytes -> one OAS 3-shaped document per source.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Loaded:
    """One source, read into the OAS 3 shape the cutter works on."""

    source: SpecSource
    data: bytes
    document: Mapping[str, Any]
    #: The prefix the unit's paths carry and the document's path keys omit.
    base_path: str

    @property
    def label(self) -> str:
        return self.source.label or self.source.url.rsplit("/", 1)[-1].split(".")[0]


def _jsonable(node: Any) -> Any:
    """A YAML-read tree as JSON would have read it: string keys, ISO dates."""
    if isinstance(node, Mapping):
        return {str(key): _jsonable(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_jsonable(item) for item in node]
    if isinstance(node, datetime.date):
        return node.isoformat()
    return node


def _parse(source: SpecSource, data: bytes) -> Any:
    """JSON when it is JSON, else YAML. YAML 1.1 reads a bare ``200`` as an
    integer key and a bare date as a date, so a YAML-read tree is normalised
    to what JSON would have produced before anything looks at it."""
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        pass
    try:
        return _jsonable(yaml.load(data, Loader=_StrictSafeLoader))
    except (yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"{source.url}: not a JSON or YAML document: {exc}") from exc


def _servers_path(document: Mapping[str, Any]) -> str:
    servers = document.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], Mapping):
        return urlsplit(str(servers[0].get("url", ""))).path
    return ""


def _load(source: SpecSource, data: bytes, cut: _Cut) -> _Loaded:
    if source.kind == "fragments":
        raise NotImplementedError(f"spec source kind 'fragments' ({source.url}) is konyklabs/roadmap#57")
    document = _parse(source, data)
    if not isinstance(document, Mapping):
        raise ValueError(f"{source.url}: expected an object at the top level")
    if source.kind == "swagger2":
        version = str(document.get("swagger", ""))
        if version.split(".")[0] != "2":
            raise ValueError(f"{source.url}: declared kind swagger2 but the document says swagger={version!r}")
        base_path = source.base_path or str(document.get("basePath") or "")
        return _Loaded(source, data, _convert_swagger2(document, cut), base_path.rstrip("/"))
    version = str(document.get("openapi", ""))
    if version.split(".")[0] != "3":
        raise ValueError(f"{source.url}: declared kind openapi3 but the document says openapi={version!r}")
    base_path = source.base_path or _servers_path(document)
    return _Loaded(source, data, document, base_path.rstrip("/"))


# ---------------------------------------------------------------------------
# Swagger 2.0 -> OAS 3 shape (the third invariant).
# ---------------------------------------------------------------------------


def _swagger2_ref(ref: str) -> str:
    for old, new in _SWAGGER2_REF_PREFIXES:
        if ref.startswith(old):
            return new + ref[len(old) :]
    return ref


def _media(candidates: Any, default: str) -> str:
    """The media type a ``consumes``/``produces`` list means to the cutter: its
    JSON entry when it has one (the one the validator reads), else its first,
    else ``default``."""
    names = [str(name) for name in candidates] if isinstance(candidates, list) else []
    for name in names:
        base = name.split(";")[0].strip().lower()
        if base == _JSON_MEDIA or base.endswith("+json"):
            return name
    return names[0] if names else default


def _form_media(candidates: Any) -> str:
    names = [str(name) for name in candidates] if isinstance(candidates, list) else []
    for name in names:
        base = name.split(";")[0].strip().lower()
        if base.startswith("multipart/") or base == _FORM_MEDIA:
            return name
    return _FORM_MEDIA


def _parameter_schema(cut: _Cut, parameter: Mapping[str, Any]) -> dict[str, Any]:
    """A Swagger 2.0 parameter's type keywords, as the ``schema`` OAS 3 wants.
    Vendor extensions ride along; ``clean_schema`` maps or strips them."""
    schema: dict[str, Any] = {}
    for key, value in parameter.items():
        if key in _PARAMETER_ROW_KEYS:
            continue
        if key in _PARAMETER_DROPPED_KEYS:
            cut.drop(key)
            continue
        schema[key] = value
    return schema


def _merged_parameters(reusable: Mapping[str, Any], shared: Any, own: Any) -> list[Mapping[str, Any]]:
    """Path-level then operation-level parameters, references resolved, the
    operation's row replacing a shared one with the same ``name`` and ``in``."""
    merged: dict[tuple[str, str], Mapping[str, Any]] = {}
    for group in (shared, own):
        if not isinstance(group, list):
            continue
        for item in group:
            parameter = _resolve_local(reusable, item)
            if isinstance(parameter, Mapping):
                merged[(str(parameter.get("name")), str(parameter.get("in")))] = parameter
    return list(merged.values())


def _convert_operation(
    cut: _Cut,
    reusable: Mapping[str, Any],
    raw: Mapping[str, Any],
    shared: Any,
    *,
    consumes: Any,
    produces: Any,
) -> dict[str, Any]:
    consumes = raw.get("consumes", consumes)
    produces = raw.get("produces", produces)
    parameters: list[dict[str, Any]] = []
    body: dict[str, Any] | None = None
    form: dict[str, Any] = {}
    form_required: list[str] = []
    for parameter in _merged_parameters(reusable, shared, raw.get("parameters")):
        where = parameter.get("in")
        if where == "body":
            if body is None:  # Swagger 2.0 allows one; a second is the document's error, not ours.
                body = {"content": {_media(consumes, _JSON_MEDIA): {"schema": parameter.get("schema", {})}}}
                if "required" in parameter:
                    body["required"] = parameter["required"]
        elif where == "formData":
            name = str(parameter.get("name"))
            form[name] = _parameter_schema(cut, parameter)
            if parameter.get("required") is True:
                form_required.append(name)
        else:
            row: dict[str, Any] = {"name": parameter.get("name"), "in": where}
            if "required" in parameter:
                row["required"] = parameter["required"]
            row["schema"] = _parameter_schema(cut, parameter)
            parameters.append(row)
    if form and body is None:
        schema: dict[str, Any] = {"type": "object", "properties": form}
        if form_required:
            schema["required"] = form_required
        body = {"required": bool(form_required), "content": {_form_media(consumes): {"schema": schema}}}

    operation: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _OPERATION_CONSUMED_KEYS:
            operation[key] = value
    if parameters:
        operation["parameters"] = parameters
    if body is not None:
        operation["requestBody"] = body
    responses: dict[str, Any] = {}
    raw_responses = raw.get("responses")
    if isinstance(raw_responses, Mapping):
        for status, item in raw_responses.items():
            response = _resolve_local(reusable, item)
            converted: dict[str, Any] = {}
            if isinstance(response, Mapping):
                for key, value in response.items():
                    if key == "schema":
                        converted["content"] = {_media(produces, _JSON_MEDIA): {"schema": value}}
                    else:
                        converted[key] = value
            responses[str(status)] = converted
    operation["responses"] = responses
    return operation


def _convert_swagger2(raw: Mapping[str, Any], cut: _Cut) -> dict[str, Any]:
    """The OAS 3 shape of a Swagger 2.0 document, for the cutter. Only what
    the cutter reads is converted; ``host``, ``schemes`` and
    ``securityDefinitions`` are not carried, and reusable parameters and
    responses are resolved into the operations rather than kept."""
    document = _rewrite_refs(raw, _swagger2_ref)
    reusable = {
        "components": {
            "parameters": document.get("parameters", {}),
            "responses": document.get("responses", {}),
        }
    }
    consumes = document.get("consumes")
    produces = document.get("produces")
    paths: dict[str, Any] = {}
    raw_paths = document.get("paths")
    if isinstance(raw_paths, Mapping):
        for path, item in raw_paths.items():
            if not isinstance(item, Mapping):
                continue
            converted: dict[str, Any] = {}
            for method in _HTTP_METHODS:
                operation = item.get(method)
                if isinstance(operation, Mapping):
                    converted[method] = _convert_operation(
                        cut, reusable, operation, item.get("parameters"), consumes=consumes, produces=produces
                    )
            paths[str(path)] = converted
    definitions = document.get("definitions")
    return {
        "openapi": _CONVERTED_OPENAPI,
        "info": document.get("info", {}),
        "paths": paths,
        "components": {"schemas": definitions if isinstance(definitions, Mapping) else {}},
    }


# ---------------------------------------------------------------------------
# Cutting.
# ---------------------------------------------------------------------------


def _cut_parameters(cut: _Cut, document: Mapping[str, Any], raw: Any) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return kept
    for item in raw:
        parameter = _resolve_local(document, item)
        if not isinstance(parameter, Mapping):
            continue
        row: dict[str, Any] = {}
        for key, value in parameter.items():
            if key == "name" or key == "in" or key == "required":
                row[key] = value
            elif key == "schema":
                row[key] = cut.clean_schema(value)
            else:
                cut.drop(key)
        kept.append(row)
    return kept


def _cut_content(cut: _Cut, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    content: dict[str, Any] = {}
    for media, spec in raw.items():
        body: dict[str, Any] = {}
        if isinstance(spec, Mapping):
            for key, value in spec.items():
                if key == "schema":
                    body[key] = cut.clean_schema(value)
                else:
                    cut.drop(key)
        content[str(media)] = body
    return content


def _cut_operation(cut: _Cut, document: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    operation: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "operationId" or key == "deprecated":
            operation[key] = value
        elif key == "parameters":
            operation[key] = _cut_parameters(cut, document, value)
        elif key == "requestBody":
            body = _resolve_local(document, value)
            kept_body: dict[str, Any] = {}
            if isinstance(body, Mapping):
                for body_key, body_value in body.items():
                    if body_key == "required":
                        kept_body[body_key] = body_value
                    elif body_key == "content":
                        content = _cut_content(cut, body_value)
                        if content is not None:
                            kept_body[body_key] = content
                    else:
                        cut.drop(body_key)
            operation[key] = kept_body
        elif key == "responses":
            operation[key] = _cut_responses(cut, document, value)
        else:
            cut.drop(key)
    return operation


def _cut_responses(cut: _Cut, document: Mapping[str, Any], raw: Any) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    if not isinstance(raw, Mapping):
        return responses
    for status, item in raw.items():
        response = _resolve_local(document, item)
        kept: dict[str, Any] = {"description": str(status)}
        if isinstance(response, Mapping):
            for key, value in response.items():
                if key == "content":
                    content = _cut_content(cut, value)
                    if content is not None:
                        kept[key] = content
                elif key == "description":
                    # Replaced, not stripped: OAS 3 requires one, and the status is the useful one.
                    continue
                else:
                    cut.drop(key)
        responses[str(status)] = kept
    return responses


def _index_paths(document: Mapping[str, Any]) -> dict[tuple[str, str], tuple[str, Mapping[str, Any]]]:
    """``(METHOD, shape) -> (upstream path, operation)`` for every operation upstream.

    Parameter *names* are erased in the key so a unit's ``{id}`` finds the
    spec's ``{order_id}``; the upstream spelling is what the extract keeps,
    because that is the spelling its ``parameters`` name."""
    index: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        return index
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
        for method in _HTTP_METHODS:
            raw = item.get(method)
            if isinstance(raw, Mapping):
                index.setdefault((method.upper(), template_shape(str(path))), (str(path), raw))
    return index


def _info(document: Mapping[str, Any]) -> Mapping[str, Any]:
    info = document.get("info")
    return info if isinstance(info, Mapping) else {}


def _strip_base(base_path: str, spec_path: str) -> str | None:
    """``spec_path`` without the source's prefix, or ``None`` when it does not carry it."""
    if not base_path:
        return spec_path
    if spec_path == base_path:
        return "/"
    if spec_path.startswith(base_path + "/"):
        return spec_path[len(base_path) :]
    return None


# ---------------------------------------------------------------------------
# Schema closure and the cross-source merge.
# ---------------------------------------------------------------------------


def _available(document: Mapping[str, Any]) -> Mapping[str, Any]:
    components = document.get("components")
    schemas = components.get("schemas") if isinstance(components, Mapping) else None
    return schemas if isinstance(schemas, Mapping) else {}


def _closure(cut: _Cut, document: Mapping[str, Any], roots: set[str], *, merged: Mapping[str, Any]) -> dict[str, Any]:
    """Every schema reachable from ``roots`` through ``#/components/schemas/``
    references, cleaned. A name this document does not define but ``merged``
    (an earlier source) does is left to that definition; one nobody defines
    becomes ``{}`` and is recorded on the cut as stubbed."""
    available = _available(document)
    collected: dict[str, Any] = {}
    pending = sorted(roots)
    while pending:
        name = pending.pop()
        if name in collected:
            continue
        if name in available:
            cleaned = cut.clean_schema(available[name])
            collected[name] = cleaned
            found: set[str] = set()
            _schema_refs(cleaned, found)
            pending.extend(sorted(found - collected.keys()))
        elif name in merged:
            continue
        else:
            collected[name] = {}
            cut.stubbed.add(name)
    return collected


def _namespacing(rename: Mapping[str, str]) -> Callable[[str], str]:
    def rewrite(ref: str) -> str:
        if ref.startswith(SCHEMA_REF_PREFIX):
            name = ref[len(SCHEMA_REF_PREFIX) :]
            if name in rename:
                return SCHEMA_REF_PREFIX + rename[name]
        return ref

    return rewrite


def _renamed(closure: Mapping[str, Any], rename: Mapping[str, str]) -> dict[str, Any]:
    rewrite = _namespacing(rename)
    return {rename.get(name, name): _rewrite_refs(schema, rewrite) for name, schema in closure.items()}


def _conflicts(
    closure: Mapping[str, Any], rename: Mapping[str, str], renamed: Mapping[str, Any], merged: Mapping[str, Any]
) -> list[str]:
    """Names of this closure, not yet namespaced, that an earlier source defined differently."""
    return [name for name in sorted(closure) if name not in rename and name in merged and merged[name] != renamed[name]]


def _merge_schemas(
    cut: _Cut, loaded: Sequence[_Loaded], roots: Mapping[int, set[str]], error_schema: str | None
) -> tuple[dict[str, Any], dict[str, str], dict[int, Mapping[str, str]]]:
    """``components.schemas`` across every source, the namespaced ledger, and
    the per-source renames the kept operations must follow.

    Namespacing runs to a fixed point within one source: when ``B`` differs and
    ``A`` references ``B``, then ``A`` -- identical text upstream -- now refers
    to ``<label>.B`` and so differs too, and is namespaced in turn."""
    schemas: dict[str, Any] = {}
    namespaced: dict[str, str] = {}
    renames: dict[int, Mapping[str, str]] = {}
    error_defined = False
    for position, item in enumerate(loaded):
        wanted = set(roots.get(position, ()))
        if error_schema is not None and error_schema in _available(item.document):
            wanted.add(error_schema)
            error_defined = True
        if not wanted:
            continue
        closure = _closure(cut, item.document, wanted, merged=schemas)
        rename: dict[str, str] = {}
        renamed = _renamed(closure, rename)
        conflicts = _conflicts(closure, rename, renamed, schemas)
        while conflicts:
            for name in conflicts:
                rename[name] = f"{item.label}.{name}"
            renamed = _renamed(closure, rename)
            conflicts = _conflicts(closure, rename, renamed, schemas)
        for name, schema in renamed.items():
            if name in schemas and schemas[name] != schema:
                raise ValueError(
                    f"schema {name!r} is defined differently by {item.source.url} and an earlier source, "
                    f"and the namespaced name is taken as well"
                )
            schemas[name] = schema
        for new_name in rename.values():
            namespaced[new_name] = item.label
        renames[position] = rename
    if error_schema is not None and not error_defined:
        raise ValueError(
            f"the declared error schema {error_schema!r} is not defined by any source: "
            + ", ".join(item.source.url for item in loaded)
        )
    return schemas, namespaced, renames


def cut_extract(
    sources: Sequence[tuple[SpecSource, bytes]],
    modeled: Sequence[tuple[str, str]],
    *,
    fetched: str,
    extension_map: Mapping[str, str] | None = None,
    error_schema: str | None = None,
) -> dict[str, Any]:
    """The scoped extract, as a plain document ready for :func:`render_json`.

    ``modeled`` is ``(METHOD, spec_path)`` pairs with any declaration alias
    already applied by the caller; ``spec_path`` is spelled as the unit spells
    it (parameter names free, base path included). ``fetched`` is the ISO
    date the caller obtained the bytes -- passed in, never read from a clock,
    so the function stays pure. ``extension_map`` and ``error_schema`` are the
    declaration's: vendor extension keys to rename to their OAS keyword, and
    the schema kept as a root of the closure whether or not any operation
    references it (an error when no source defines it).
    """
    if not sources:
        raise ValueError("cut_extract needs at least one spec source")
    cut = _Cut(extension_map or {})
    loaded = [_load(source, data, cut) for source, data in sources]
    indexes = [_index_paths(item.document) for item in loaded]

    paths: dict[str, dict[str, Any]] = {}
    origin: dict[tuple[str, str], int] = {}
    kept_keys: list[str] = []
    missing: list[str] = []
    roots: dict[int, set[str]] = {}

    for method, spec_path in modeled:
        method_upper = method.upper()
        for position, item in enumerate(loaded):
            local = _strip_base(item.base_path, spec_path)
            if local is None:
                continue
            hit = indexes[position].get((method_upper, template_shape(local)))
            if hit is None:
                continue
            upstream_path, raw = hit
            operation = _cut_operation(cut, item.document, raw)
            out_path = item.base_path + upstream_path
            paths.setdefault(out_path, {})[method_upper.lower()] = operation
            origin[(out_path, method_upper.lower())] = position
            kept_keys.append(route_key(method_upper, out_path))
            _schema_refs(operation, roots.setdefault(position, set()))
            break
        else:
            missing.append(route_key(method_upper, spec_path))

    schemas, namespaced, renames = _merge_schemas(cut, loaded, roots, error_schema)
    for (out_path, method_lower), position in origin.items():
        rename = renames.get(position)
        if rename:
            paths[out_path][method_lower] = _rewrite_refs(paths[out_path][method_lower], _namespacing(rename))

    first_document = loaded[0].document
    upstream_info = _info(first_document)
    info = {
        "title": f"{upstream_info.get('title', '')} (scoped extract)",
        "version": str(upstream_info.get("version", "")),
    }

    source_rows = []
    for item in loaded:
        doc_info = _info(item.document)
        source_rows.append(
            {
                "url": item.source.url,
                "label": item.label,
                "sha256": sha256_hex(item.data),
                "bytes": len(item.data),
                "version": str(doc_info.get("version", "")),
                "fetched": fetched,
            }
        )

    return {
        "openapi": str(first_document.get("openapi")),
        "info": info,
        "paths": {path: dict(sorted(item.items())) for path, item in sorted(paths.items())},
        "components": {"schemas": dict(sorted(schemas.items()))},
        "x-vendorfake": {
            "schema": EXTRACT_SCHEMA,
            "sources": source_rows,
            "modeled": sorted(kept_keys),
            "missing": sorted(missing),
            "stubbed": sorted(cut.stubbed),
            "namespaced": dict(sorted(namespaced.items())),
            "rewritten": {"nullable_ref": cut.nullable_refs, "extensions": dict(sorted(cut.renamed.items()))},
            "stripped": sorted(cut.stripped),
        },
    }
