"""Profile loading: precedence, the environment table, and what must be loud.

The precedence order is a consumer-visible contract, so it is asserted layer by
layer rather than end to end. The other two weights are the two divergences
this module makes on purpose -- the retry merge going one level deeper than the
reference's, and ``env`` never defaulting to the process environment -- because
both are the kind of change a reviewer should see stated rather than infer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vendorfake.core.config.models import (
    ClockSection,
    ErrorsSection,
    ProfileDocument,
    ResolvedChaos,
    ResolvedConfig,
    ResolvedWebhooks,
    RetryPolicy,
    SubscriberConfig,
    TransportSection,
    WebhooksSection,
    parse_profile_document,
)
from vendorfake.core.config.profile import (
    ENV_TABLE,
    ENV_VENDOR_PREFIX,
    env_names,
    load_profile,
    merge_documents,
    resolve_config,
)
from vendorfake.core.kernel.types import UnitError, UnitErrorKind

# The vendor's own document, as create_unit will supply it: a documented retry
# schedule that has no business being a default inside the core.
VENDOR_DEFAULTS = ProfileDocument(
    webhooks=WebhooksSection(retry=RetryPolicy(schedule_ms=(60_000, 120_000, 240_000), time_scale=1 / 6000))
)


def write_profile(directory: Path, name: str, document: dict[str, object]) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The core carries no vendor defaults.
# ---------------------------------------------------------------------------


def test_the_core_retry_schedule_defaults_empty() -> None:
    """The reference hard-codes one vendor's eleven-retry schedule here. An
    empty default is what makes an unmerged vendor default a startup error
    instead of an instant 'exhausted' on the first attempt."""
    policy = RetryPolicy()
    assert policy.schedule_ms == ()
    assert policy.time_scale == 1.0
    assert policy.timeout_ms == 10_000


# ---------------------------------------------------------------------------
# Precedence: defaults < document < environment.
# ---------------------------------------------------------------------------


def test_the_profile_document_beats_the_caller_defaults() -> None:
    document = ProfileDocument(capabilities=("oauth",), chaos={"seed": 7})  # type: ignore[arg-type]
    defaults = ProfileDocument(capabilities=("oauth", "webhooks"), chaos={"seed": 1})  # type: ignore[arg-type]
    merged = merge_documents(defaults, document)
    assert merged.capabilities == ("oauth",)
    assert merged.chaos.seed == 7


def test_the_environment_beats_the_profile_document() -> None:
    document = ProfileDocument(capabilities=("oauth", "webhooks"), chaos={"seed": 7})  # type: ignore[arg-type]
    config = resolve_config(
        document,
        name="custom",
        env={"VENDORFAKE_CAPABILITIES": "-webhooks", "VENDORFAKE_CHAOS_SEED": "99"},
    )
    assert config.capabilities == ("oauth",)
    assert config.chaos.seed == 99


def test_a_field_the_document_leaves_out_falls_through_to_the_defaults() -> None:
    """`model_fields_set` is what makes this expressible; the reference had to
    approximate it with object spreads."""
    merged = merge_documents(VENDOR_DEFAULTS, ProfileDocument(capabilities=("webhooks",)))
    assert merged.webhooks.retry.schedule_ms == (60_000, 120_000, 240_000)


# ---------------------------------------------------------------------------
# The one merge divergence, stated and pinned.
# ---------------------------------------------------------------------------


def test_a_profile_that_sets_only_time_scale_keeps_the_vendor_schedule() -> None:
    """The reference replaces the whole retry object at this level and got away
    with it because its DEFAULT_RETRY held a vendor's schedule. Here the
    schedule arrives from the vendor's defaults, so a replace would silently
    empty it -- and every shipped profile sets exactly time_scale and
    timeout_ms."""
    document = parse_profile_document({"webhooks": {"retry": {"time_scale": 0.000167, "timeout_ms": 2000}}})
    merged = merge_documents(VENDOR_DEFAULTS, document)
    assert merged.webhooks.retry.schedule_ms == (60_000, 120_000, 240_000)
    assert merged.webhooks.retry.time_scale == 0.000167
    assert merged.webhooks.retry.timeout_ms == 2000


def test_the_literal_time_scale_is_carried_through_untouched() -> None:
    """0.000167 is not tidied to 1/6000: a test downstream asserts the 10ms it
    produces, and 60000 * (1/6000) is 10.0 while 60000 * 0.000167 is 10.02."""
    config = resolve_config(
        parse_profile_document({"webhooks": {"retry": {"time_scale": 0.000167}}}),
        name="p",
    )
    assert config.webhooks.retry.time_scale == 0.000167


def test_a_subscriber_list_replaces_rather_than_appends() -> None:
    defaults = ProfileDocument(
        webhooks=WebhooksSection(
            subscribers=(SubscriberConfig(notification_url="https://a.test", event_types=("*",), signature_key="k"),)
        )
    )
    document = parse_profile_document(
        {
            "webhooks": {
                "subscribers": [{"notification_url": "https://b.test", "event_types": ["*"], "signature_key": "j"}]
            }
        }
    )
    merged = merge_documents(defaults, document)
    assert [s.notification_url for s in merged.webhooks.subscribers] == ["https://b.test"]


# ---------------------------------------------------------------------------
# env is a parameter, never the process environment.
# ---------------------------------------------------------------------------


def test_the_process_environment_is_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reference defaults to process.env and its harness spreads it into
    every unit; one stray variable in a shell then changes code that never
    mentioned it."""
    monkeypatch.setenv("VENDORFAKE_PROFILE", "hijacked")
    monkeypatch.setenv("VENDORFAKE_CHAOS_SEED", "424242")
    write_profile(tmp_path, "full", {"name": "full", "chaos": {"seed": 3}})

    loaded = load_profile(profile_dir=tmp_path)

    assert loaded.config.profile == "full"
    assert loaded.config.chaos.seed == 3


# ---------------------------------------------------------------------------
# The environment table.
# ---------------------------------------------------------------------------


def test_no_unit_prefixed_alias_is_honoured() -> None:
    config = resolve_config(ProfileDocument(chaos={"seed": 5}), name="p", env={"UNIT_CHAOS_SEED": "9"})  # type: ignore[arg-type]
    assert config.chaos.seed == 5


def test_the_webhook_url_variable_appends_one_subscriber() -> None:
    config = resolve_config(
        ProfileDocument(),
        name="p",
        env={
            "VENDORFAKE_WEBHOOK_URL": "https://consumer.test/hook",
            "VENDORFAKE_WEBHOOK_EVENTS": "order.created, order.updated",
            "VENDORFAKE_WEBHOOK_SIGNATURE_KEY": "shhh",
        },
    )
    assert len(config.webhooks.subscribers) == 1
    subscriber = config.webhooks.subscribers[0]
    assert subscriber.id == "wbhk_env"
    assert subscriber.notification_url == "https://consumer.test/hook"
    assert subscriber.event_types == ("order.created", "order.updated")
    assert subscriber.signature_key == "shhh"
    assert subscriber.enabled is True


def test_vendor_prefixed_variables_become_snake_case_keys() -> None:
    """A deliberate divergence: the reference camel-cased them, and the vendor's
    own config model has snake_case fields, so the mapping is identity."""
    config = resolve_config(
        ProfileDocument(vendor={"environment": "Sandbox"}),
        name="p",
        env={f"{ENV_VENDOR_PREFIX}APPLICATION_ID": "app-1", "VENDORFAKE_VENDOR": "ignored-selector"},
    )
    assert config.vendor_config == {"environment": "Sandbox", "application_id": "app-1"}


def test_every_env_table_row_is_complete() -> None:
    """The generated env reference is built from ENV_TABLE: every row carries a
    VENDORFAKE_ name, what it applies to and a summary, and exactly one is a prefix."""
    assert len(ENV_TABLE) == 18
    assert all(name.startswith("VENDORFAKE_") for name in env_names())
    assert sum(1 for var in ENV_TABLE if var.is_prefix) == 1
    assert all(var.applies_to and var.summary for var in ENV_TABLE)


def test_the_transport_block_is_environment_only() -> None:
    default = resolve_config(ProfileDocument(), name="p")
    assert (default.transport.port, default.transport.host) == (8080, None)
    overridden = resolve_config(
        ProfileDocument(),
        name="p",
        env={"VENDORFAKE_PORT": "9999", "VENDORFAKE_HOST": "127.0.0.1"},
    )
    assert overridden.transport.port == 9999
    assert overridden.transport.host == "127.0.0.1"


def test_the_defaults_when_nothing_is_set() -> None:
    config = resolve_config(ProfileDocument(), name="fallback")
    assert config.profile == "fallback"
    assert config.capabilities == ()
    assert config.seed_path is None
    assert config.chaos.seed == 1
    assert config.clock.mode == "real"
    assert config.webhooks.disable_delivery is False
    assert config.log_level == "info"


# ---------------------------------------------------------------------------
# Malformed input is loud, and names the field.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VENDORFAKE_PORT", "eighty-eighty"),
        ("VENDORFAKE_CHAOS_SEED", "1.5"),
        ("VENDORFAKE_WEBHOOK_TIMEOUT_MS", ""),
    ],
)
def test_a_malformed_numeric_variable_is_an_error_or_ignored_when_empty(name: str, value: str) -> None:
    """`Number(env.UNIT_PORT)` yields NaN for a typo and the unit starts on a
    nonsense port. An empty string keeps the reference's falsy skip."""
    if value == "":
        assert resolve_config(ProfileDocument(), name="p", env={name: value}).webhooks.retry.timeout_ms == 10_000
        return
    with pytest.raises(UnitError) as caught:
        resolve_config(ProfileDocument(), name="p", env={name: value})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == name


def test_a_non_finite_time_scale_is_rejected() -> None:
    with pytest.raises(UnitError) as caught:
        resolve_config(ProfileDocument(), name="p", env={"VENDORFAKE_WEBHOOK_TIME_SCALE": "inf"})
    assert caught.value.field == "VENDORFAKE_WEBHOOK_TIME_SCALE"


def test_an_unknown_clock_mode_is_rejected_rather_than_cast() -> None:
    with pytest.raises(UnitError) as caught:
        resolve_config(ProfileDocument(), name="p", env={"VENDORFAKE_CLOCK": "wall"})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "VENDORFAKE_CLOCK"


# ---------------------------------------------------------------------------
# VENDORFAKE_CLOCK_START (konyklabs/roadmap#71, D1)
# ---------------------------------------------------------------------------


def test_clock_start_is_reproducible_across_two_resolves() -> None:
    """The whole point: two calls with the same env agree, where the mode-only
    control left the start instant to wall-clock luck."""
    env = {"VENDORFAKE_CLOCK": "virtual", "VENDORFAKE_CLOCK_START": "2026-01-01T00:00:00Z"}
    first = resolve_config(ProfileDocument(), name="p", env=env)
    second = resolve_config(ProfileDocument(), name="p", env=env)
    assert first.clock.start == second.clock.start == "2026-01-01T00:00:00Z"
    assert first.clock.mode == "virtual"


def test_a_malformed_clock_start_names_the_expected_format() -> None:
    with pytest.raises(UnitError) as caught:
        resolve_config(
            ProfileDocument(),
            name="p",
            env={"VENDORFAKE_CLOCK": "virtual", "VENDORFAKE_CLOCK_START": "not-an-instant"},
        )
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "VENDORFAKE_CLOCK_START"
    assert "RFC 3339" in (caught.value.detail or "")


@pytest.mark.parametrize("naive", ["2026-01-01T00:00:00", "2026-01-01"])
def test_a_naive_clock_start_is_refused_not_silently_accepted(naive: str) -> None:
    """``datetime.fromisoformat`` accepts a naive instant and a bare date --
    neither is an RFC 3339 *instant*, which is what the error message this
    loader raises on a malformed value names. Before this fix both silently
    parsed and resolved to midnight UTC, so a caller who worked around the
    sibling ``ValueError`` on a naive ``datetime``
    (``vendorfake.testing._clock_start_env_value``) by switching to a string
    got no signal that they had routed around the same timezone requirement.
    """
    with pytest.raises(UnitError) as caught:
        resolve_config(
            ProfileDocument(),
            name="p",
            env={"VENDORFAKE_CLOCK": "virtual", "VENDORFAKE_CLOCK_START": naive},
        )
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "VENDORFAKE_CLOCK_START"
    assert "RFC 3339" in (caught.value.detail or "")


def test_clock_start_on_a_real_clock_is_a_loud_refusal_not_a_silent_switch() -> None:
    """The mode default is 'real'; setting only the start must not flip it --
    that would be exactly the silent mode switch the spec forbids."""
    with pytest.raises(UnitError) as caught:
        resolve_config(ProfileDocument(), name="p", env={"VENDORFAKE_CLOCK_START": "2026-01-01T00:00:00Z"})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "VENDORFAKE_CLOCK_START"
    assert "virtual" in (caught.value.detail or "")


def test_clock_start_is_fine_when_the_profile_document_itself_sets_virtual_mode() -> None:
    """The guard reads the resolved mode, not just the env layer: a profile
    document's own clock.mode='virtual' satisfies it without VENDORFAKE_CLOCK."""
    config = resolve_config(
        ProfileDocument(clock={"mode": "virtual"}),  # type: ignore[arg-type]
        name="p",
        env={"VENDORFAKE_CLOCK_START": "2026-01-01T00:00:00Z"},
    )
    assert config.clock.mode == "virtual"
    assert config.clock.start == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# VENDORFAKE_ERROR_SIDECAR (konyklabs/roadmap#71, D2)
# ---------------------------------------------------------------------------


def test_error_sidecar_defaults_to_headers() -> None:
    assert resolve_config(ProfileDocument(), name="p").errors.sidecar == "headers"


def test_resolved_config_does_not_require_naming_errors() -> None:
    """``ErrorsSection`` is a total default (``sidecar`` defaults to
    ``"headers"``) and :func:`resolve_config` always supplies a value, so
    ``ResolvedConfig.errors`` is defaulted the same way ``requests`` and
    ``unmatched`` are: a caller assembling one by hand should not have to name
    a knob it is not exercising. Constructed directly rather than through
    :func:`resolve_config`, which always passes ``errors`` and so would not
    catch a regression back to a required field.
    """
    config = ResolvedConfig(
        profile="p",
        webhooks=ResolvedWebhooks(retry=RetryPolicy()),
        chaos=ResolvedChaos(seed=1),
        clock=ClockSection(),
        transport=TransportSection(),
    )
    assert config.errors == ErrorsSection()


@pytest.mark.parametrize("mode", ["headers", "body", "both"])
def test_error_sidecar_env_overrides_the_profile_document(mode: str) -> None:
    config = resolve_config(ProfileDocument(), name="p", env={"VENDORFAKE_ERROR_SIDECAR": mode})
    assert config.errors.sidecar == mode


def test_an_unknown_error_sidecar_mode_is_rejected() -> None:
    with pytest.raises(UnitError) as caught:
        resolve_config(ProfileDocument(), name="p", env={"VENDORFAKE_ERROR_SIDECAR": "query-string"})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "VENDORFAKE_ERROR_SIDECAR"


def test_a_misspelled_profile_key_is_a_startup_failure_naming_the_field() -> None:
    """The reference's JSON.parse accepts this silently: 'capabilties' becomes a
    profile with no capabilities and the first symptom is a 501 much later."""
    with pytest.raises(UnitError) as caught:
        parse_profile_document({"name": "full", "capabilties": ["oauth"]})
    err = caught.value
    assert err.kind is UnitErrorKind.INVALID_VALUE
    assert err.field == "capabilties"


def test_a_missing_required_subscriber_field_reports_missing_field() -> None:
    """A raw ValidationError would reach the kernel's catch-all and become a
    500; the contract is a 400 naming the field."""
    with pytest.raises(UnitError) as caught:
        parse_profile_document({"webhooks": {"subscribers": [{"notification_url": "https://a.test"}]}})
    err = caught.value
    assert err.kind is UnitErrorKind.MISSING_FIELD
    assert err.field == "webhooks.subscribers.0.event_types"


def test_a_profile_subscriber_on_a_link_local_target_is_refused_like_a_control_plane_one() -> None:
    """The same target rule at both seams: a profile cannot name the instance
    metadata address any more than POST /__unit/webhooks/subscriptions can."""
    subscriber = {
        "notification_url": "http://169.254.169.254/latest",
        "event_types": ["order.created"],
        "signature_key": "k",
    }
    with pytest.raises(UnitError) as caught:
        parse_profile_document({"webhooks": {"subscribers": [subscriber]}})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert "link-local" in (caught.value.detail or "")
    loopback = {**subscriber, "notification_url": "http://127.0.0.1:9/hook"}
    assert parse_profile_document({"webhooks": {"subscribers": [loopback]}}).webhooks.subscribers[0].enabled


def test_chaos_rules_stay_opaque_documents_here() -> None:
    """The rule grammar belongs with the engine that evaluates it and with the
    control-plane body that also carries it; parsing it here would make this a
    second place the grammar is stated."""
    document = parse_profile_document({"chaos": {"seed": 3, "rules": [{"id": "r1", "anything": [1, 2]}]}})
    assert document.chaos.rules == ({"id": "r1", "anything": [1, 2]},)


# ---------------------------------------------------------------------------
# Loading from disk.
# ---------------------------------------------------------------------------


def test_load_profile_reads_the_named_profile_and_its_seed(tmp_path: Path) -> None:
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "default.seed.json").write_text(json.dumps({"orders": []}), encoding="utf-8")
    write_profile(
        tmp_path,
        "orders-only",
        {"name": "orders-only", "capabilities": ["order-lifecycle"], "seed": "seed/default.seed.json"},
    )

    loaded = load_profile(profile_dir=tmp_path, name="orders-only", defaults=VENDOR_DEFAULTS)

    assert loaded.config.profile == "orders-only"
    assert loaded.config.capabilities == ("order-lifecycle",)
    assert loaded.seed == {"orders": []}
    assert loaded.source_path == tmp_path / "orders-only.json"
    assert loaded.config.webhooks.retry.schedule_ms == (60_000, 120_000, 240_000)


def test_a_name_ending_in_json_is_treated_as_a_path(tmp_path: Path) -> None:
    path = write_profile(tmp_path / ".", "custom", {"name": "custom"})
    loaded = load_profile(profile_dir=tmp_path / "elsewhere", name=str(path))
    assert loaded.source_path == path


def test_a_missing_profile_lists_what_is_available(tmp_path: Path) -> None:
    write_profile(tmp_path, "full", {"name": "full"})
    write_profile(tmp_path, "no-chaos", {"name": "no-chaos"})
    with pytest.raises(UnitError) as caught:
        load_profile(profile_dir=tmp_path, name="fll")
    assert caught.value.field == "profile"
    assert caught.value.info is not None
    assert caught.value.info["available"] == ["full", "no-chaos"]


def test_malformed_profile_json_reports_invalid_json_with_a_position(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text('{"name": "broken",}', encoding="utf-8")
    with pytest.raises(UnitError) as caught:
        load_profile(profile_dir=tmp_path, name="broken")
    assert caught.value.kind is UnitErrorKind.INVALID_JSON
    assert caught.value.detail is not None
    assert "line 1" in caught.value.detail


def test_the_profile_variable_selects_the_file_when_no_name_is_passed(tmp_path: Path) -> None:
    write_profile(tmp_path, "full", {"name": "full"})
    write_profile(tmp_path, "oauth-only", {"name": "oauth-only", "capabilities": ["oauth"]})
    loaded = load_profile(profile_dir=tmp_path, env={"VENDORFAKE_PROFILE": "oauth-only"})
    assert loaded.config.profile == "oauth-only"


def test_the_seed_variable_overrides_the_profiles_seed(tmp_path: Path) -> None:
    (tmp_path / "other.seed.json").write_text(json.dumps({"from": "env"}), encoding="utf-8")
    (tmp_path / "default.seed.json").write_text(json.dumps({"from": "profile"}), encoding="utf-8")
    write_profile(tmp_path, "full", {"name": "full", "seed": "default.seed.json"})
    loaded = load_profile(profile_dir=tmp_path, env={"VENDORFAKE_SEED": "other.seed.json"})
    assert loaded.seed == {"from": "env"}


def test_the_resolved_config_is_frozen() -> None:
    config = resolve_config(ProfileDocument(), name="p")
    with pytest.raises(Exception, match=r"frozen"):
        config.profile = "other"  # type: ignore[misc]
