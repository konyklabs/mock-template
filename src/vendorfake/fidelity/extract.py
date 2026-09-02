"""Cutting a scoped extract from a vendor's published OpenAPI document.

FOR: turning a multi-megabyte upstream document into the smallest valid
OpenAPI 3 document that still says everything the validator needs about the
operations a unit models -- so the extract can be committed, diffed by a
human, and shipped in the wheel without carrying a vendor's prose, examples
and tooling annotations along with it.

INVARIANT: **the cut is a pure function of its inputs.** Same upstream bytes,
same modeled list, same ``fetched`` string -- byte-identical output, every
time, on every machine. Nothing here reads a clock, the network or the file
system; ``pin.py`` does the fetching and writing and hands bytes in. That is
what lets ``pin --check`` in CI compare a fresh cut against the committed one
and call any difference drift.

SECOND INVARIANT: **prose is stripped structurally, never by key name alone.**
A schema may have a *property* called ``description`` or ``title`` (Square's
``CatalogItem`` does), and stripping by name would delete the property rather
than the annotation. The stripper knows where in a schema the keys are
annotations and where they are names.

What is kept, exactly: per operation ``operationId``, ``deprecated``,
``parameters`` (name, in, required, schema), ``requestBody.required`` and
``requestBody.content.<media>.schema``, and
``responses.<status>.content.<media>.schema``, with each response's required
``description`` set to the status string. ``components.schemas`` holds the
schemas transitively reachable from the kept operations through
``#/components/schemas/<name>`` references; a reference to a name the upstream
does not define becomes ``{}`` at the component and is listed under
``x-vendorfake.stubbed`` so the hole is visible rather than silent. Stripped
everywhere: ``description``, ``summary``, ``example``, ``examples``,
``externalDocs``, ``title`` inside schemas, and every ``x-*`` key other than
the ``x-vendorfake`` block this module writes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.fidelity.types import SpecSource, route_key, template_shape

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


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_json(document: Mapping[str, Any]) -> str:
    """The one serialisation every generated fidelity file uses: sorted keys,
    two-space indent, one trailing newline. Byte-identical for equal inputs."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


class _Cut:
    """Working state for one cut: the stripped-key ledger and the schema queue."""

    __slots__ = ("nullable_refs", "stripped", "stubbed")

    def __init__(self) -> None:
        self.stripped: set[str] = set()
        self.stubbed: set[str] = set()
        #: ``{"$ref": X, "nullable": true}`` nodes rewritten; see ``clean_schema``.
        self.nullable_refs = 0

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
            if key in STRIPPED_KEYS or key.startswith("x-"):
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


def _load_openapi3(source: SpecSource, data: bytes) -> Mapping[str, Any]:
    if source.kind != "openapi3":
        raise NotImplementedError(
            f"spec source kind {source.kind!r} ({source.url}) is not implemented by this leg: "
            f"swagger2 is konyklabs/roadmap#56 and fragments is konyklabs/roadmap#57"
        )
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{source.url}: not a JSON document: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ValueError(f"{source.url}: expected a JSON object at the top level")
    version = str(document.get("openapi", ""))
    if version.split(".")[0] != "3":
        raise ValueError(f"{source.url}: declared kind openapi3 but the document says openapi={version!r}")
    return document


def _closure(cut: _Cut, document: Mapping[str, Any], roots: set[str]) -> dict[str, Any]:
    """Every schema reachable from ``roots`` through ``#/components/schemas/``
    references, cleaned; a name the upstream does not define becomes ``{}``
    and is recorded on the cut as stubbed."""
    components = document.get("components")
    schemas = components.get("schemas") if isinstance(components, Mapping) else None
    available: Mapping[str, Any] = schemas if isinstance(schemas, Mapping) else {}
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
        else:
            collected[name] = {}
            cut.stubbed.add(name)
    return collected


def cut_extract(
    sources: Sequence[tuple[SpecSource, bytes]],
    modeled: Sequence[tuple[str, str]],
    *,
    fetched: str,
) -> dict[str, Any]:
    """The scoped extract, as a plain document ready for :func:`render_json`.

    ``modeled`` is ``(METHOD, spec_path)`` pairs with any declaration alias
    already applied by the caller; ``spec_path`` is spelled as the unit spells
    it (parameter names free, ``base_path`` included). ``fetched`` is the ISO
    date the caller obtained the bytes -- passed in, never read from a clock,
    so the function stays pure.
    """
    if not sources:
        raise ValueError("cut_extract needs at least one spec source")
    documents = [(source, data, _load_openapi3(source, data)) for source, data in sources]
    cut = _Cut()

    paths: dict[str, dict[str, Any]] = {}
    kept_keys: list[str] = []
    missing: list[str] = []
    roots: dict[int, set[str]] = {}
    indexes = [_index_paths(document) for _, _, document in documents]

    for method, spec_path in modeled:
        method_upper = method.upper()
        for position, (source, _, document) in enumerate(documents):
            local = spec_path
            if source.base_path and local.startswith(source.base_path):
                local = local[len(source.base_path) :]
            hit = indexes[position].get((method_upper, template_shape(local)))
            if hit is None:
                continue
            upstream_path, raw = hit
            operation = _cut_operation(cut, document, raw)
            out_path = source.base_path + upstream_path
            paths.setdefault(out_path, {})[method_upper.lower()] = operation
            kept_keys.append(route_key(method_upper, out_path))
            _schema_refs(operation, roots.setdefault(position, set()))
            break
        else:
            missing.append(route_key(method_upper, spec_path))

    schemas: dict[str, Any] = {}
    for position, (source, _, document) in enumerate(documents):
        wanted = roots.get(position)
        if not wanted:
            continue
        for name, schema in _closure(cut, document, wanted).items():
            if name in schemas and schemas[name] != schema:
                raise ValueError(
                    f"schema {name!r} is defined differently by {source.url} and an earlier source; "
                    f"namespacing across sources is konyklabs/roadmap#57"
                )
            schemas[name] = schema

    first_document = documents[0][2]
    upstream_info = _info(first_document)
    info = {
        "title": f"{upstream_info.get('title', '')} (scoped extract)",
        "version": str(upstream_info.get("version", "")),
    }

    source_rows = []
    for source, data, document in documents:
        doc_info = _info(document)
        source_rows.append(
            {
                "url": source.url,
                "sha256": sha256_hex(data),
                "bytes": len(data),
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
            "rewritten": {"nullable_ref": cut.nullable_refs},
            "stripped": sorted(cut.stripped),
        },
    }
