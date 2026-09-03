# Public API contract

!!! note "Placeholder"
    This page is owned by stream F3 (konyklabs/roadmap#74) and will be
    replaced with the real contract: what is public (`vendorfake` root
    exports, `vendorfake.testing`, `vendorfake.registry`, `vendorfake.pytest`,
    the per-vendor `paths` and seed types, the control plane routes, the CLI's
    `--json` documents, the profile JSON schema, the `Vendorfake-*` headers),
    what is internal (`vendorfake.asgi`, `vendorfake.core`,
    `vendorfake.conformance` internals, vendor `surface` packages), and the
    deprecation policy. F1 (this docs site) only wires the navigation entry
    so `uv run mkdocs build --strict` has something to link to before F3
    lands; nothing below is durable content.

Until then: [the generated reference](reference/routes-square.md) and
[the concepts pages](concepts/unit.md) describe the same surface in prose and
tables, and `vendorfake.testing.__init__`'s module docstring names what a
consumer is meant to import.
