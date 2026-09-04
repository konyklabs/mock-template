"""The YAML loader in ``fidelity.extract`` stays safe.

``_parse`` is fed bytes fetched from a vendor's specification URL -- remote
input -- and loads YAML through ``_StrictSafeLoader``. The ``# nosec B506`` on
that call tells bandit the loader is a ``SafeLoader`` subclass; bandit takes
that on trust, line-scoped, whatever ``Loader=`` actually says. This is the
test that does not: it goes red the moment the loader would instantiate an
arbitrary object (review of konyklabs/roadmap#105).
"""

from __future__ import annotations

import pytest
import yaml

from vendorfake.fidelity.extract import _parse, _StrictSafeLoader
from vendorfake.fidelity.types import SpecSource

SOURCE = SpecSource(kind="openapi3", url="https://example.invalid/openapi.yaml")


def test_the_loader_is_a_safe_loader_and_refuses_a_python_object_tag() -> None:
    assert issubclass(_StrictSafeLoader, yaml.SafeLoader)
    hostile = b'!!python/object/apply:os.system ["true"]\n'
    with pytest.raises(ValueError, match="not a JSON or YAML document"):
        _parse(SOURCE, hostile)


def test_a_plain_yaml_document_still_parses_to_what_json_would_give() -> None:
    document = _parse(SOURCE, b"openapi: '3.0.0'\ninfo: {title: t, version: '1'}\npaths: {}\n")
    assert document == {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {}}
