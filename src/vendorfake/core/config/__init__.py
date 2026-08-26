"""Configuration: the profile document, the environment layer, and the result.

FOR: making "consumer subsets are configuration, never code" true in practice.
A profile is a JSON document naming the capabilities, the seed scenario, the
chaos rules, the subscribers and the clock mode; environment variables layer
over it so ONE image serves every subset without a rebuild.

INVARIANT: **precedence is a single, stated order and nothing resolves twice.**
Built-in defaults, then the caller's defaults (where a vendor's retry schedule
enters), then the profile document, then the environment. Every value in
:class:`~vendorfake.core.config.models.ResolvedConfig` was decided once, here;
no other module re-reads the environment or re-applies a default, which is why
``env`` can be an explicit mapping instead of a global.

This is one of the four modules where Pydantic is permitted inside the core,
and only ``models.py`` actually imports it -- ``profile.py`` gets validation
through that module rather than importing Pydantic itself, so the allow-list in
``tools/boundary.toml`` stays one entry wide.
"""
