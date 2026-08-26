"""The framework-free core.

Nothing in this package may import ``fastapi``, ``starlette``, ``uvicorn`` or
``multipart``, directly or transitively, at module level or inside a function.
Two independent checks enforce that: the import-linter contracts in
``pyproject.toml`` (static, catches transitive and function-local imports) and
``tools/boundary_check.py`` (which additionally imports every core module with
those names blocked at ``sys.meta_path``, catching dynamic imports the static
pass cannot see).

The invariant is not stylistic. Two of the three bake-off entries that
preceded this implementation broke because a framework's request-parsing
assumptions had leaked into shared code, so an ordinary form-encoded OAuth
body required surgery on code every vendor inherits.
"""
