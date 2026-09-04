"""Loading a profile: defaults, then the document, then the environment --
the only place a profile file, caller defaults or the environment are read.
INVARIANT: precedence is exactly built-in defaults < caller defaults <
profile document < environment, pinned by test. ``env`` defaults to ``{}``,
never the process environment, so a stray shell variable cannot change the
behaviour of code that never mentioned it. ``webhooks.retry`` merges field by
field rather than replacing whole, so a profile setting only ``time_scale``
does not drop the vendor's schedule; malformed environment values are
``invalid_value`` errors naming the variable, never a silently wrong default.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from vendorfake.core.capability.registry import apply_capability_delta
from vendorfake.core.config.models import (
    ProfileDocument,
    ResolvedChaos,
    ResolvedConfig,
    ResolvedWebhooks,
    RetryPolicy,
    SubscriberConfig,
    TransportSection,
    parse_profile_document,
)
from vendorfake.core.config.overlay import apply_seed_overlay, seed_overlay_digest
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "ENV_PREFIX",
    "ENV_SEED",
    "ENV_TABLE",
    "ENV_VENDOR_PREFIX",
    "EnvVar",
    "LoadedProfile",
    "env_names",
    "load_profile",
    "merge_documents",
    "resolve_config",
]

ENV_PREFIX = "VENDORFAKE_"
ENV_VENDOR_PREFIX = "VENDORFAKE_VENDOR_"
ENV_SEED = "VENDORFAKE_SEED"
"""The variable that replaces the profile's own seed document. A constant
since ``vendorfake.testing.served()`` must agree on the name too."""

DEFAULT_PROFILE_NAME = "full"
ENV_SUBSCRIBER_ID = "wbhk_env"
DEFAULT_ENV_SIGNATURE_KEY = "unit-signature-key"


@dataclass(frozen=True, slots=True)
class EnvVar:
    """One environment variable ``load_profile`` reads."""

    name: str
    applies_to: str
    summary: str
    #: True for ``VENDORFAKE_VENDOR_``, which is a prefix rather than a name.
    is_prefix: bool = False


ENV_TABLE: tuple[EnvVar, ...] = (
    EnvVar("VENDORFAKE_PROFILE", "profile", "Profile name or path to load when none is passed."),
    EnvVar(
        "VENDORFAKE_CAPABILITIES",
        "capabilities",
        "Absolute list, or a +add,-remove delta against the profile's list.",
    ),
    EnvVar(ENV_SEED, "seed_path", "Seed document path, overriding the profile's."),
    EnvVar(
        "VENDORFAKE_SEED_OVERLAY",
        "seed_overlay",
        "Partial seed document merged over the seed: a JSON file path, or the JSON itself inline.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_URL",
        "webhooks.subscribers",
        "Appends one subscriber so a container can push to a caller with no API call.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_EVENTS",
        "webhooks.subscribers",
        "Comma-separated event types for that subscriber. Defaults to '*'.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_SIGNATURE_KEY",
        "webhooks.subscribers",
        "Signing key for that subscriber.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_TIME_SCALE",
        "webhooks.retry.time_scale",
        "Multiplier on every retry delay.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_TIMEOUT_MS",
        "webhooks.retry.timeout_ms",
        "Milliseconds before a subscriber is called timed out.",
    ),
    EnvVar("VENDORFAKE_CHAOS_SEED", "chaos.seed", "Seed for the fault engine's RNG."),
    EnvVar("VENDORFAKE_CLOCK", "clock.mode", "'real' or 'virtual'."),
    EnvVar(
        "VENDORFAKE_CLOCK_START",
        "clock.start",
        "RFC 3339 instant the virtual clock starts at. Requires clock.mode='virtual'.",
    ),
    EnvVar(
        "VENDORFAKE_ERROR_SIDECAR",
        "errors.sidecar",
        "Where the 'unit_error' sidecar is emitted: 'headers' (default), 'body' or 'both'.",
    ),
    EnvVar("VENDORFAKE_PORT", "transport.port", "Port for the HTTP binding."),
    EnvVar("VENDORFAKE_HOST", "transport.host", "Interface for the HTTP binding."),
    EnvVar("VENDORFAKE_LOG_LEVEL", "log_level", "Minimum level the unit's logger emits."),
    EnvVar(
        ENV_VENDOR_PREFIX,
        "vendor_config",
        "Prefix: the remainder becomes a snake_case vendor-config key.",
        is_prefix=True,
    ),
    EnvVar(
        "VENDORFAKE_REQUEST_LOG_CAPACITY",
        "requests.capacity",
        "How many requests the in-memory request log keeps before evicting the oldest.",
    ),
)
"""Every environment variable this loader reads; one entry is a prefix.
``VENDORFAKE_VENDOR`` is absent -- it selects the vendor module, before a
profile exists, and belongs to the package registry instead."""


def env_names() -> tuple[str, ...]:
    """The variable names, prefixes included, in table order."""
    return tuple(var.name for var in ENV_TABLE)


@dataclass(frozen=True, slots=True)
class LoadedProfile:
    """A profile after loading: what was resolved, and what it was resolved from."""

    config: ResolvedConfig
    #: The decoded seed document, or ``None`` when the profile names no seed.
    seed: object | None
    #: The merged document, before the environment layer.
    document: ProfileDocument
    source_path: Path


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Merge ``over`` onto ``base``: mappings recurse, everything else
    replaces -- a profile listing two subscribers means two, not two plus
    whatever the layer below had."""
    merged = dict(base)
    for key, value in over.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(previous, value)
        else:
            merged[key] = value
    return merged


def merge_documents(base: ProfileDocument, over: ProfileDocument) -> ProfileDocument:
    """Layer ``over`` on top of ``base``, field by field. Only the fields each
    document actually *set* participate (``model_dump(exclude_unset=True)``),
    so an omitted field falls through rather than overwriting with a default.
    """
    return ProfileDocument.model_validate(
        _deep_merge(base.model_dump(exclude_unset=True), over.model_dump(exclude_unset=True))
    )


# ---------------------------------------------------------------------------
# The environment layer
# ---------------------------------------------------------------------------


def _env_int(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name)
    if not raw:
        return None
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{name}={raw!r} is not an integer.",
            field=name,
        ) from exc


def _env_float(env: Mapping[str, str], name: str) -> float | None:
    raw = env.get(name)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{name}={raw!r} is not a number.",
            field=name,
        ) from exc
    if value != value or value in (float("inf"), float("-inf")):  # NaN or infinity
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{name}={raw!r} is not a finite number.",
            field=name,
        )
    return value


def _env_clock_mode(env: Mapping[str, str]) -> str | None:
    raw = env.get("VENDORFAKE_CLOCK")
    if not raw:
        return None
    if raw not in ("real", "virtual"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"VENDORFAKE_CLOCK={raw!r} is not 'real' or 'virtual'.",
            field="VENDORFAKE_CLOCK",
        )
    return raw


def _env_clock_start(env: Mapping[str, str]) -> str | None:
    """``VENDORFAKE_CLOCK_START``, validated as an RFC 3339 instant
    (konyklabs/roadmap#71). A naive value is refused, not merely parsed --
    ``datetime.fromisoformat`` accepts a bare date with no timezone, which
    names no instant at all.
    """
    raw = env.get("VENDORFAKE_CLOCK_START")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"VENDORFAKE_CLOCK_START={raw!r} is not an RFC 3339 instant, e.g. '2026-01-01T00:00:00Z'.",
            field="VENDORFAKE_CLOCK_START",
        ) from exc
    if parsed.tzinfo is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"VENDORFAKE_CLOCK_START={raw!r} is not an RFC 3339 instant, e.g. '2026-01-01T00:00:00Z'.",
            field="VENDORFAKE_CLOCK_START",
        )
    return raw


def _env_error_sidecar(env: Mapping[str, str]) -> str | None:
    raw = env.get("VENDORFAKE_ERROR_SIDECAR")
    if not raw:
        return None
    if raw not in ("headers", "body", "both"):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"VENDORFAKE_ERROR_SIDECAR={raw!r} is not 'headers', 'body' or 'both'.",
            field="VENDORFAKE_ERROR_SIDECAR",
        )
    return raw


def resolve_config(
    document: ProfileDocument,
    *,
    name: str,
    env: Mapping[str, str] | None = None,
) -> ResolvedConfig:
    """Apply the environment layer to a merged document. Touches no
    filesystem, so a test can exercise precedence without writing a file."""
    environ: Mapping[str, str] = {} if env is None else env

    capabilities = list(document.capabilities)
    delta = environ.get("VENDORFAKE_CAPABILITIES")
    if delta:
        capabilities = apply_capability_delta(capabilities, delta)

    subscribers = list(document.webhooks.subscribers)
    webhook_url = environ.get("VENDORFAKE_WEBHOOK_URL")
    if webhook_url:
        subscribers.append(
            SubscriberConfig(
                id=ENV_SUBSCRIBER_ID,
                name="VENDORFAKE_WEBHOOK_URL",
                notification_url=webhook_url,
                event_types=tuple(
                    part.strip() for part in environ.get("VENDORFAKE_WEBHOOK_EVENTS", "*").split(",") if part.strip()
                ),
                signature_key=environ.get("VENDORFAKE_WEBHOOK_SIGNATURE_KEY", DEFAULT_ENV_SIGNATURE_KEY),
                enabled=True,
            )
        )

    retry_overrides: dict[str, Any] = {}
    time_scale = _env_float(environ, "VENDORFAKE_WEBHOOK_TIME_SCALE")
    if time_scale is not None:
        retry_overrides["time_scale"] = time_scale
    timeout_ms = _env_int(environ, "VENDORFAKE_WEBHOOK_TIMEOUT_MS")
    if timeout_ms is not None:
        retry_overrides["timeout_ms"] = timeout_ms
    retry: RetryPolicy = document.webhooks.retry.model_copy(update=retry_overrides)

    vendor_config: dict[str, Any] = dict(document.vendor)
    for key, value in environ.items():
        if key.startswith(ENV_VENDOR_PREFIX) and len(key) > len(ENV_VENDOR_PREFIX):
            vendor_config[key[len(ENV_VENDOR_PREFIX) :].lower()] = value

    chaos_seed = _env_int(environ, "VENDORFAKE_CHAOS_SEED")
    clock_mode = _env_clock_mode(environ)
    clock_start = _env_clock_start(environ)
    if clock_start is not None and (clock_mode or document.clock.mode) != "virtual":
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                "VENDORFAKE_CLOCK_START requires a virtual clock; set VENDORFAKE_CLOCK=virtual "
                '(or env={"VENDORFAKE_CLOCK": "virtual"}) rather than silently switching modes.'
            ),
            field="VENDORFAKE_CLOCK_START",
        )
    clock_updates: dict[str, Any] = {}
    if clock_mode is not None:
        clock_updates["mode"] = clock_mode
    if clock_start is not None:
        clock_updates["start"] = clock_start
    error_sidecar = _env_error_sidecar(environ)
    port = _env_int(environ, "VENDORFAKE_PORT")
    capacity = _env_int(environ, "VENDORFAKE_REQUEST_LOG_CAPACITY")
    if capacity is not None and capacity < 0:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"VENDORFAKE_REQUEST_LOG_CAPACITY={capacity} must be zero or more (zero switches the log off).",
            field="VENDORFAKE_REQUEST_LOG_CAPACITY",
        )

    return ResolvedConfig(
        profile=document.name or name,
        capabilities=tuple(capabilities),
        seed_path=environ.get(ENV_SEED) or document.seed,
        # A locator, not a document -- `load_profile` reads it, since this
        # function touches no filesystem.
        seed_overlay=environ.get("VENDORFAKE_SEED_OVERLAY") or None,
        vendor_config=vendor_config,
        webhooks=ResolvedWebhooks(
            retry=retry,
            subscribers=tuple(subscribers),
            disable_delivery=document.webhooks.disable_delivery,
        ),
        chaos=ResolvedChaos(
            seed=document.chaos.seed if chaos_seed is None else chaos_seed,
            rules=document.chaos.rules,
            strict_rules=document.chaos.strict_rules,
        ),
        clock=document.clock if not clock_updates else document.clock.model_copy(update=clock_updates),
        errors=document.errors
        if error_sidecar is None
        else document.errors.model_copy(update={"sidecar": error_sidecar}),
        transport=TransportSection(
            port=8080 if port is None else port,
            host=environ.get("VENDORFAKE_HOST"),
        ),
        requests=(
            document.requests if capacity is None else document.requests.model_copy(update={"capacity": capacity})
        ),
        log_level=environ.get("VENDORFAKE_LOG_LEVEL", "info"),
    )


# ---------------------------------------------------------------------------
# Loading from disk
# ---------------------------------------------------------------------------


def _read_json(path: Path, *, what: str, field: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"No {what} at {path}.",
            field=field,
            info={"path": str(path)},
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnitError(
            UnitErrorKind.INVALID_JSON,
            detail=f"{path} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}.",
            field=field,
            info={"path": str(path)},
        ) from exc


def profile_path(profile_dir: Path, name: str) -> Path:
    """Where ``name`` resolves to: an absolute path or one ending in ``.json``
    is taken as a path; anything else names a file in ``profile_dir`` --
    the difference between ``--profile full`` and ``--profile ./my.json``.
    """
    candidate = Path(name)
    if candidate.is_absolute() or name.endswith(".json"):
        return candidate
    return profile_dir / f"{name}.json"


def load_profile(
    *,
    profile_dir: Path,
    name: str | None = None,
    base_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    defaults: ProfileDocument | None = None,
) -> LoadedProfile:
    """Read a profile, layer defaults under it and the environment over it.
    ``defaults`` is where a vendor's own document (its retry schedule above
    all) goes, so the profile document beats it and the environment beats both.
    """
    environ: Mapping[str, str] = {} if env is None else env
    resolved_name = name or environ.get("VENDORFAKE_PROFILE") or DEFAULT_PROFILE_NAME
    source_path = profile_path(profile_dir, resolved_name)

    if not source_path.exists():
        available = sorted(candidate.stem for candidate in profile_dir.glob("*.json"))
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"No profile at {source_path}."
                + (f" Available in {profile_dir}: {', '.join(available)}." if available else "")
            ),
            field="profile",
            info={"path": str(source_path), "available": available},
        )

    document = parse_profile_document(_read_json(source_path, what="profile", field="profile"), source=str(source_path))
    merged = document if defaults is None else merge_documents(defaults, document)
    config = resolve_config(merged, name=resolved_name, env=environ)

    seed: object | None = None
    if config.seed_path:
        seed_root = base_dir if base_dir is not None else source_path.parent
        seed_file = Path(config.seed_path)
        if not seed_file.is_absolute():
            seed_file = seed_root / seed_file
        seed = _read_json(seed_file, what="seed document", field="seed")

    if config.seed_overlay is not None:
        overlay = _read_overlay(config.seed_overlay)
        seed = apply_seed_overlay(seed, overlay, profile=config.profile)
        config = config.model_copy(
            update={
                "seed_overlay_digest": seed_overlay_digest(overlay),
                "seed_overlay_collections": tuple(sorted(overlay)),
            }
        )

    return LoadedProfile(config=config, seed=seed, document=merged, source_path=source_path)


def _read_overlay(locator: str) -> Mapping[str, Any]:
    """``VENDORFAKE_SEED_OVERLAY`` as a document: inline JSON, or a file. A
    value starting with ``{`` is the document itself; anything else is a path,
    relative to the process's working directory -- NOT to the profile, since
    an overlay is a file the caller wrote, unlike ``seed_path``.
    """
    text = locator.lstrip()
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UnitError(
                UnitErrorKind.INVALID_JSON,
                detail=(
                    f"VENDORFAKE_SEED_OVERLAY starts with '{{' and so is read as inline JSON, but it is not "
                    f"valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}."
                ),
                field="seed_overlay",
            ) from exc
    else:
        decoded = _read_json(Path(locator), what="seed overlay document", field="seed_overlay")
    if not isinstance(decoded, Mapping):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"a seed overlay must be a JSON object whose keys name the seed's top-level collections; "
                f"this one decoded to {type(decoded).__name__}."
            ),
            field="seed_overlay",
        )
    return {str(key): value for key, value in decoded.items()}
