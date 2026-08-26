"""D-001's fix-list item that the ten synthesis items omitted, made mechanical.

D-001 adopted a fix list with the bake-off's findings, and its fifth entry was
"content-type generality retained in core". That entry does not appear among
the ten items in the synthesis spec, because in the TypeScript world it was
already true and the instruction was "port as-is". In Python it has to be
BUILT, which makes it the item with the strongest measured evidence behind it
and the weakest tracking: the second-vendor exercise forced roughly twenty-line
core patches on the two bake-off entries whose transports assumed JSON, while
the winning entry's kernel shipped a form primitive and needed no core change
at all.

The end-to-end version of this lives in tests/unit/test_oauth_form_body.py,
which drives a vendor's token endpoint. That test is worth having, but it
depends on the Square surface choosing to accept urlencoded, which is a
labelled judgment beyond what Square documents. This one depends on nothing
but the core, so it holds for every vendor that will ever exist here -- and it
passes from the moment the core exists rather than from the moment a vendor
does.

The web framework is blocked outright while it runs, because the property
under test is precisely that nothing in this path needs one.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

BLOCKED = ("fastapi", "starlette", "uvicorn", "python_multipart", "multipart")


class _Blocked:
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError(f"{fullname} must not be reachable from the core")
        return None


@pytest.fixture
def no_web_framework() -> Iterator[None]:
    finder = _Blocked()
    saved = {name: sys.modules.pop(name, None) for name in BLOCKED}
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


def _args(content_type: str, raw_body: bytes) -> object:
    from vendorfake.core.kernel.types import HandlerArgs, UnitRequest

    request = UnitRequest(
        id="req_content_type_generality",
        method="POST",
        path="/probe",
        query={},
        headers={"content-type": content_type},
        raw_body=raw_body,
        transport="test",
        received_at="2026-01-01T00:00:00.000Z",
    )
    return HandlerArgs(req=request, params={}, ctx=None, route=None)  # type: ignore[arg-type]


class TestTheCoreReadsFormBodiesWithNoFrameworkPresent:
    """The exact shape that broke two of three bake-off entries."""

    def test_a_urlencoded_body_reaches_the_handler_as_fields(self, no_web_framework: None) -> None:
        args = _args(
            "application/x-www-form-urlencoded",
            b"grant_type=client_credentials&client_id=abc&client_secret=shh",
        )
        assert args.body() == {  # type: ignore[attr-defined]
            "grant_type": "client_credentials",
            "client_id": "abc",
            "client_secret": "shh",
        }

    def test_a_json_body_reaches_the_handler_as_the_same_shape(self, no_web_framework: None) -> None:
        args = _args("application/json", b'{"grant_type":"client_credentials","client_id":"abc"}')
        assert args.body()["grant_type"] == "client_credentials"  # type: ignore[index]

    def test_a_charset_parameter_does_not_defeat_the_branch(self, no_web_framework: None) -> None:
        args = _args("application/x-www-form-urlencoded; charset=utf-8", b"a=1")
        assert args.body() == {"a": "1"}  # type: ignore[attr-defined]

    def test_none_of_the_forbidden_modules_were_imported_along_the_way(self, no_web_framework: None) -> None:
        _args("application/x-www-form-urlencoded", b"a=1").body()  # type: ignore[attr-defined]
        assert [name for name in BLOCKED if name in sys.modules] == []
