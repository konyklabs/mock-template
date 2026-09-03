"""Loading a profile: defaults, then the document, then the environment.

FOR: producing one :class:`~vendorfake.core.config.models.ResolvedConfig` from
a JSON profile on disk, a caller-supplied defaults document, and an explicit
environment mapping -- and for being the only place any of those three are
read.

INVARIANT: **precedence is exactly built-in defaults < caller defaults <
profile document < environment**, ported unchanged from
``packages/core/src/config/profile.ts``. Everything else about that function is
rebuilt -- the unvalidated ``JSON.parse``, the hand-rolled merge and the ``??``
chains are all Pydantic's job now -- but the order in which four layers beat
each other is behaviour a consumer depends on, so it is preserved literally and
pinned by test.

``env`` defaults to ``{}``, never to the process environment
-------------------------------------------------------------
The reference defaults to ``process.env`` and the harness spreads it into every
unit it builds. That makes a stray variable in a shell -- or one test's
``setenv`` leaking into the next -- change the behaviour of code that never
mentioned it, which is a whole class of order-dependent flake for free. Here
the environment is a parameter with an empty default: only the CLI passes the
real one. A test that wants an environment states it.

The names are ``VENDORFAKE_*``; there is no ``UNIT_*`` alias
-------------------------------------------------------------
Environment variables are namespaced by *product* and URL paths by *concept*,
which is why ``/__unit/`` stays and ``UNIT_*`` goes: ``UNIT_PORT`` and
``UNIT_SEED`` are plausible collisions in a shared CI environment, and nothing
is published yet, so there is no consumer to break. :data:`ENV_TABLE` carries
the reference name each variable replaces so the rename is checkable rather
than remembered, and a test asserts the table against the reference's list.

Two divergences from the reference, both deliberate
----------------------------------------------------
``webhooks.retry`` merges field by field, where the reference replaces it
    The reference's one-level merge means a profile that sets
    ``webhooks: {retry: {time_scale: ...}}`` *replaces* the defaults' whole
    retry object, and it survived that because its ``DEFAULT_RETRY`` carried a
    vendor's schedule as a built-in. Here the schedule arrives through the
    caller's defaults -- it is a vendor fact and does not belong in the core --
    so a replace at that level would silently drop it, and the shipped profiles
    that set only ``time_scale`` and ``timeout_ms`` would all end up with an
    empty schedule. Merging one level deeper keeps the stated precedence and
    makes the vendor default reachable.

Malformed environment values are errors, not ``NaN``
    ``Number(env.UNIT_PORT)`` yields ``NaN`` for a typo and the unit starts on
    a nonsense port. Every numeric and enumerated variable here is parsed with
    a check that raises ``invalid_value`` naming the variable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vendorfake.core.capability.registry import apply_capability_delta
from vendorfake.core.config.models import (
    UNMATCHED_POLICIES,
    ProfileDocument,
    ResolvedChaos,
    ResolvedConfig,
    ResolvedWebhooks,
    RetryPolicy,
    SubscriberConfig,
    TransportSection,
    UnmatchedPolicy,
    parse_profile_document,
)
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "ENV_PREFIX",
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

DEFAULT_PROFILE_NAME = "full"
ENV_SUBSCRIBER_ID = "wbhk_env"
DEFAULT_ENV_SIGNATURE_KEY = "unit-signature-key"


@dataclass(frozen=True, slots=True)
class EnvVar:
    """One environment variable, with the reference name it replaces.

    ``replaces`` exists so the rename is a checkable fact: a test asserts this
    table against the reference's sixteen, which is the only way a variable
    silently disappearing in translation shows up as a failure.
    """

    name: str
    #: The reference's name for this variable, or ``None`` for one this build
    #: added and the reference never had. Kept as a distinct value rather than
    #: an empty string so the test asserting "all sixteen survived the rename"
    #: still counts sixteen as the table grows.
    replaces: str | None
    applies_to: str
    summary: str
    #: True for ``VENDORFAKE_VENDOR_``, which is a prefix rather than a name.
    is_prefix: bool = False


ENV_TABLE: tuple[EnvVar, ...] = (
    EnvVar("VENDORFAKE_PROFILE", "UNIT_PROFILE", "profile", "Profile name or path to load when none is passed."),
    EnvVar(
        "VENDORFAKE_CAPABILITIES",
        "UNIT_CAPABILITIES",
        "capabilities",
        "Absolute list, or a +add,-remove delta against the profile's list.",
    ),
    EnvVar("VENDORFAKE_SEED", "UNIT_SEED", "seed_path", "Seed document path, overriding the profile's."),
    EnvVar(
        "VENDORFAKE_WEBHOOK_URL",
        "UNIT_WEBHOOK_URL",
        "webhooks.subscribers",
        "Appends one subscriber so a container can push to a caller with no API call.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_EVENTS",
        "UNIT_WEBHOOK_EVENTS",
        "webhooks.subscribers",
        "Comma-separated event types for that subscriber. Defaults to '*'.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_SIGNATURE_KEY",
        "UNIT_WEBHOOK_SIGNATURE_KEY",
        "webhooks.subscribers",
        "Signing key for that subscriber.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_TIME_SCALE",
        "UNIT_WEBHOOK_TIME_SCALE",
        "webhooks.retry.time_scale",
        "Multiplier on every retry delay.",
    ),
    EnvVar(
        "VENDORFAKE_WEBHOOK_TIMEOUT_MS",
        "UNIT_WEBHOOK_TIMEOUT_MS",
        "webhooks.retry.timeout_ms",
        "Milliseconds before a subscriber is called timed out.",
    ),
    EnvVar("VENDORFAKE_CHAOS_SEED", "UNIT_CHAOS_SEED", "chaos.seed", "Seed for the fault engine's RNG."),
    EnvVar("VENDORFAKE_CLOCK", "UNIT_CLOCK", "clock.mode", "'real' or 'virtual'."),
    EnvVar("VENDORFAKE_TRANSPORT", "UNIT_TRANSPORT", "transport.kind", "Which binding the CLI stands up."),
    EnvVar(
        "VENDORFAKE_TRANSPORT_DIR",
        "UNIT_TRANSPORT_DIR",
        "transport.dir",
        "Directory the file-drop binding watches.",
    ),
    EnvVar("VENDORFAKE_PORT", "UNIT_PORT", "transport.port", "Port for the HTTP binding."),
    EnvVar("VENDORFAKE_HOST", "UNIT_HOST", "transport.host", "Interface for the HTTP binding."),
    EnvVar("VENDORFAKE_LOG_LEVEL", "UNIT_LOG_LEVEL", "log_level", "Minimum level the unit's logger emits."),
    EnvVar(
        ENV_VENDOR_PREFIX,
        "UNIT_VENDOR_",
        "vendor_config",
        "Prefix: the remainder becomes a snake_case vendor-config key. The reference camel-cased it.",
        is_prefix=True,
    ),
    EnvVar(
        "VENDORFAKE_REQUEST_LOG_CAPACITY",
        None,
        "requests.capacity",
        "How many requests the in-memory request log keeps before evicting the oldest.",
    ),
    EnvVar(
        "VENDORFAKE_UNMATCHED",
        None,
        "unmatched.policy",
        "'vendor-404' or 'error': what an in-process binding does with a request no route matched.",
    ),
)
"""Every environment variable this loader reads: the sixteen the reference read,
renamed, plus the ones this build added, which carry ``replaces=None``.

``VENDORFAKE_VENDOR`` (no trailing underscore) is deliberately absent: it
selects which vendor module to load, which happens before a profile exists, and
it belongs to the package registry rather than here."""


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
    """Merge ``over`` onto ``base``: mappings recurse, everything else replaces.

    Sequences replace rather than concatenate. A profile that lists two
    subscribers means two, not two plus whatever the layer below had -- the
    reference's spread behaves the same way and a consumer reading a profile
    expects the list they can see.
    """
    merged = dict(base)
    for key, value in over.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(previous, value)
        else:
            merged[key] = value
    return merged


def merge_documents(base: ProfileDocument, over: ProfileDocument) -> ProfileDocument:
    """Layer ``over`` on top of ``base``, field by field.

    Only the fields each document actually *set* participate, which is what
    ``model_dump(exclude_unset=True)`` reports. That is the piece the reference
    approximated with object spreads: a field left out of a profile falls
    through to the layer beneath rather than overwriting it with the model's
    default.
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


def _env_unmatched(env: Mapping[str, str]) -> UnmatchedPolicy | None:
    """``VENDORFAKE_UNMATCHED``, checked against the two policies.

    A typo here is the worst possible silent failure: ``VENDORFAKE_UNMATCHED=err``
    would fall back to the binding's default, and a CI run configured to fail
    loudly on a mis-targeted request would go on answering 404s. So it is an
    ``invalid_value`` that names the variable and lists what it accepts,
    exactly as ``VENDORFAKE_CLOCK`` is.
    """
    raw = env.get("VENDORFAKE_UNMATCHED")
    if not raw:
        return None
    for policy in UNMATCHED_POLICIES:
        # Compared one at a time rather than with `in`, so the value that comes
        # back is the *literal* and no cast is needed to say so.
        if raw == policy:
            return policy
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"VENDORFAKE_UNMATCHED={raw!r} is not one of {', '.join(UNMATCHED_POLICIES)}.",
        field="VENDORFAKE_UNMATCHED",
    )


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


def resolve_config(
    document: ProfileDocument,
    *,
    name: str,
    env: Mapping[str, str] | None = None,
) -> ResolvedConfig:
    """Apply the environment layer to a merged document. Touches no filesystem.

    Separated from :func:`load_profile` so a test -- and any caller building a
    unit from an in-memory document -- can exercise precedence without writing
    a file.
    """
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
            # snake_case, not the reference's camelCase: the vendor's own
            # config model has snake_case fields, so the mapping is identity.
            vendor_config[key[len(ENV_VENDOR_PREFIX) :].lower()] = value

    chaos_seed = _env_int(environ, "VENDORFAKE_CHAOS_SEED")
    clock_mode = _env_clock_mode(environ)
    port = _env_int(environ, "VENDORFAKE_PORT")
    capacity = _env_int(environ, "VENDORFAKE_REQUEST_LOG_CAPACITY")
    if capacity is not None and capacity < 0:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"VENDORFAKE_REQUEST_LOG_CAPACITY={capacity} must be zero or more (zero switches the log off).",
            field="VENDORFAKE_REQUEST_LOG_CAPACITY",
        )
    unmatched = _env_unmatched(environ)

    return ResolvedConfig(
        profile=document.name or name,
        capabilities=tuple(capabilities),
        seed_path=environ.get("VENDORFAKE_SEED") or document.seed,
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
        clock=document.clock if clock_mode is None else document.clock.model_copy(update={"mode": clock_mode}),
        transport=TransportSection(
            kind=environ.get("VENDORFAKE_TRANSPORT", "http"),
            port=8080 if port is None else port,
            host=environ.get("VENDORFAKE_HOST"),
            dir=environ.get("VENDORFAKE_TRANSPORT_DIR"),
        ),
        requests=(
            document.requests if capacity is None else document.requests.model_copy(update={"capacity": capacity})
        ),
        unmatched=(
            document.unmatched if unmatched is None else document.unmatched.model_copy(update={"policy": unmatched})
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
    """Where ``name`` resolves to.

    An absolute path or a name ending in ``.json`` is taken as a path; anything
    else names a file in ``profile_dir``. Ported from the reference, including
    the ``.json`` heuristic, because it is the difference between
    ``--profile full`` and ``--profile ./my-profile.json`` and both are useful.
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

    ``defaults`` is where a vendor's own document goes -- its retry schedule
    above all -- so the profile document beats it and the environment beats
    both.
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

    return LoadedProfile(config=config, seed=seed, document=merged, source_path=source_path)
