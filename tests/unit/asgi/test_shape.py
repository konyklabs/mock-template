"""A static pass over ``src/vendorfake/asgi/`` -- the leak no import rule sees.

``tools/boundary_check.py`` bans import *names*, and inside this one package
every framework name is permitted, which is the whole point of the package. So
the dangerous mistakes here match no import ban and no grep for ``Form(``:

* ``await request.form()`` is a method call on a Starlette ``Request``. It is
  exactly the bake-off failure this project exists to prevent, and it is
  invisible to every rule that looks at imports.
* ``JSONResponse(model)``, ``PlainTextResponse``, ``ORJSONResponse`` re-render
  a body that the core already serialised, breaking both the byte-for-byte
  agreement between bindings and the raw-body guarantee the webhook signature
  scheme rests on.
* ``add_middleware`` or a ``Middleware(...)`` entry rewrites headers or bytes
  after the unit is finished with them, with the same two consequences.

This is an AST pass rather than a set of greps because a grep cannot tell a
call from the word in a docstring -- and the docstrings in this package
deliberately name every one of these, so a grep-based rule would fail on the
documentation that explains it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ASGI_DIR = Path(__file__).resolve().parents[3] / "src" / "vendorfake" / "asgi"

#: The one response class this package may construct. Named, not inferred:
#: the rule is "the core's bytes go out untouched", and every other class in
#: ``starlette.responses`` exists precisely to render something.
ALLOWED_RESPONSE_CLASS = "Response"

#: Methods that make the *framework* decide what a body is.
BANNED_REQUEST_METHODS = frozenset({"form", "stream"})


def modules() -> list[Path]:
    found = sorted(p for p in ASGI_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    # An empty list would make every rule below vacuously true, which is the
    # one way this file could pass while checking nothing at all.
    assert found != [], f"no modules under {ASGI_DIR}"
    return found


def trees() -> list[tuple[Path, ast.Module]]:
    return [(path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))) for path in modules()]


def calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_the_only_response_class_constructed_is_the_bare_one() -> None:
    """``JSONResponse`` and friends re-serialise; that is what they are for.

    The core hands over bytes it already decided the exact form of, and a
    conformance check compares those bytes across bindings. Any class that
    renders would keep every assertion in this repository passing while
    changing what a consumer is testing against.
    """
    offenders: list[str] = []
    for path, tree in trees():
        for call in calls(tree):
            name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
            if isinstance(name, str) and name.endswith("Response") and name != ALLOWED_RESPONSE_CLASS:
                offenders.append(f"{path.name}:{call.lineno} {name}(")
    assert offenders == [], f"only {ALLOWED_RESPONSE_CLASS}(...) may be constructed here: {offenders}"


def test_the_framework_never_parses_a_body() -> None:
    """No ``.form()``, no ``.stream()``, and no ``request.json()``.

    ``python-multipart`` is not a dependency, so ``.form()`` would raise at
    request time -- but only on the code path that reached it, possibly in
    production and not in the test that added it. This fails at the moment the
    call is written.
    """
    offenders: list[str] = []
    for path, tree in trees():
        for call in calls(tree):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in BANNED_REQUEST_METHODS:
                offenders.append(f"{path.name}:{call.lineno} .{func.attr}()")
            if func.attr == "json" and isinstance(func.value, ast.Name) and func.value.id == "request":
                offenders.append(f"{path.name}:{call.lineno} request.json()")
    assert offenders == [], f"the core parses bodies, not the adapter: {offenders}"


def test_no_middleware_is_declared_anywhere_in_the_package() -> None:
    """Asserted on the source, not only on a built application.

    ``test_no_middleware_is_installed`` checks one application built one way;
    this catches a middleware added on a branch that a fixture happens not to
    take.
    """
    offenders: list[str] = []
    for path, tree in trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                if name in {"add_middleware", "Middleware"}:
                    offenders.append(f"{path.name}:{node.lineno} {name}(")
            if isinstance(node, ast.keyword) and node.arg == "middleware":
                offenders.append(f"{path.name}:{node.lineno} middleware=")
    assert offenders == [], f"no middleware may sit between the unit and the socket: {offenders}"


def test_no_typed_parameter_declarations_reach_the_route() -> None:
    """``Form(...)``, ``Body(...)``, ``File(...)``, ``Query(...)``, ``Depends(...)``.

    Each one hands the framework a decision the core is supposed to make. The
    catch-all takes a single ``Request`` and nothing else; anything on this
    list appearing at all means that is no longer true.
    """
    banned = {"Form", "Body", "File", "Query", "Header", "Cookie", "Depends", "Path"}
    offenders: list[str] = []
    for path, tree in trees():
        for call in calls(tree):
            if isinstance(call.func, ast.Name) and call.func.id in banned:
                offenders.append(f"{path.name}:{call.lineno} {call.func.id}(")
    assert offenders == [], f"the adapter declares no typed parameters: {offenders}"


def test_there_is_no_module_level_application() -> None:
    """``create_app`` is a factory; a module global would be built on import.

    A module-level ``app = FastAPI()`` needs a unit before anyone asks for one,
    which means a global unit, which means one test's state reaching another's.
    """
    offenders: list[str] = []
    for path, tree in trees():
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if isinstance(value, ast.Call):
                name = value.func.id if isinstance(value.func, ast.Name) else getattr(value.func, "attr", None)
                if name in {"FastAPI", "Starlette"}:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"the application is built by a factory, never at import: {offenders}"


@pytest.mark.parametrize("name", ["adapt.py", "app.py", "serve.py", "__init__.py"])
def test_the_package_is_the_three_modules_it_claims_to_be(name: str) -> None:
    """The rules above are only as good as the set of files they run over.

    A fourth module appearing here is not forbidden -- but it is where the
    framework would next leak, so it should be a deliberate change to this list
    rather than something that arrives unnoticed.
    """
    assert {path.name for path in modules()} == {"adapt.py", "app.py", "serve.py", "__init__.py"}
    assert (ASGI_DIR / name).is_file()


def test_every_module_documents_what_it_is_for_and_what_it_protects() -> None:
    """The repository's own rule, applied where it matters most.

    This package is the one place the invariant can be undone by a change that
    looks reasonable, so the reason has to be in the file rather than in a
    review comment somebody remembers.
    """
    for path, tree in trees():
        doc = ast.get_docstring(tree)
        assert doc, f"{path.name} has no module docstring"
        if path.name != "__init__.py":
            assert "INVARIANT" in doc, f"{path.name}'s docstring does not state what it protects"
