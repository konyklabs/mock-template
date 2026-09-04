"""The typed-seed contract: one neutral view, one protocol, and no ``None``.

Three claims are under test here, and only the first two are ordinary runtime
assertions:

1. every seed satisfies :class:`~vendorfake.testing.Seed` structurally, so a
   function typed against the protocol takes all three without an
   ``isinstance``;
2. ``credentials`` reports each vendor's own application credential under the
   neutral names, and the ``grant`` each vendor documents;
3. the narrowing is *load-bearing* -- ``unit("toast").seed.merchant_id`` is a
   type error and not merely un-asserted;
4. and the same for the overlay types on the way IN --
   ``unit("square", seed_overlay={"merchants": {}})`` names a collection
   Square's seed document does not have, and a checker says so.

The last two are negatives, and a negative cannot be proved by a type check
that passes. So each is proved by running mypy on a module written to fail
(``tests/typing/negative/``) and reading the error out of the output. Those
subprocesses are the only slow tests in this file and they are the ones that
matter: without them, deleting the overloads or the overlay types would leave
every other test here green.

The overlay types have a third guard that is an ordinary assertion: their keys
are typed by hand while the seed documents are data, so
``test_every_overlay_type_names_exactly_its_vendors_seed_collections``
compares the two and fails the day a collection is added to a document and not
to its type.

The seedless-vendor test is the other half of "seed is never ``None``". There
is no shipped vendor without a seed -- all three have one -- so the case is
constructed: a vendor definition with a temporary profile directory,
substituted for the registry's lookup for the duration of one call.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes import FakeVendor
from vendorfake.core.kernel.unit import Unit
from vendorfake.registry import resolve_vendor
from vendorfake.testing import (
    NO_SEED_HINT,
    CloverSeedOverlay,
    Credentials,
    LightspeedSeedOverlay,
    Seed,
    SquareSeedOverlay,
    ToastSeedOverlay,
    unit,
)
from vendorfake.testing.seeds import CloverSeed, SquareSeed, ToastSeed, seed_for

REPO_ROOT = Path(__file__).resolve().parents[3]
NEGATIVE_MODULE = Path("tests/typing/negative/toast_merchant_id.py")
OVERLAY_NEGATIVE_MODULE = Path("tests/typing/negative/square_overlay_unknown_collection.py")


def credentials_of(seed: Seed) -> Credentials:
    """The structural use the protocol exists for.

    Typed against the protocol and nothing else: if a seed stopped satisfying
    :class:`Seed`, this signature is where it would show, and the three tests
    below are the three vendors it has to accept.
    """
    return seed.credentials


# ---------------------------------------------------------------------------
# The protocol, and the neutral credentials.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vendor", ["square", "clover", "toast"])
def test_every_seed_satisfies_the_protocol_without_an_isinstance(vendor: str) -> None:
    with unit(vendor) as started:
        credentials = credentials_of(started.seed)
        assert credentials.app_id
        assert credentials.app_secret
        assert started.seed.auth["Authorization"].startswith("Bearer ")
        assert started.seed.read_only_auth["Authorization"].startswith("Bearer ")
        assert started.seed.event_types


def test_square_credentials_are_its_own_application_fields() -> None:
    with unit("square") as square:
        assert square.seed.credentials == Credentials(
            app_id=square.seed.application_id,
            app_secret=square.seed.application_secret,
            grant="refresh_token",
        )


def test_clover_credentials_are_its_own_client_fields() -> None:
    with unit("clover") as clover:
        assert clover.seed.credentials == Credentials(
            app_id=clover.seed.client_id,
            app_secret=clover.seed.client_secret,
            grant="refresh_token",
        )


def test_toast_credentials_report_a_grant_with_no_refresh() -> None:
    """Toast's login answers a bearer and no refresh token, so the seed has
    no ``refresh_token`` field and the grant says why."""
    with unit("toast") as toast:
        assert toast.seed.credentials == Credentials(
            app_id=toast.seed.client_id,
            app_secret=toast.seed.client_secret,
            grant="client_credentials",
        )
        assert not hasattr(toast.seed, "refresh_token")


def test_the_grant_distinguishes_the_vendors_that_rotate_a_refresh_token() -> None:
    """The one branch a parametrized consumer legitimately writes. It is on
    the neutral view rather than on a field only two seeds carry, so the
    branch reads as a vendor difference and not as a missing attribute."""
    grants = {}
    for vendor in ("square", "clover", "toast"):
        with unit(vendor) as started:
            grants[vendor] = started.seed.credentials.grant
    assert grants == {"square": "refresh_token", "clover": "refresh_token", "toast": "client_credentials"}


def test_credentials_follow_a_profile_override_rather_than_the_constants() -> None:
    """The reason ``credentials`` is derived from the seed's config-backed
    fields and not from the vendor's constants: a profile may override the
    application credentials, and a fixture reporting the default while the
    unit ran on an override sends a consumer chasing a 401 it caused."""
    with unit("square", env={"VENDORFAKE_VENDOR_APPLICATION_ID": "sandbox-sq0idb-overridden"}) as square:
        assert square.seed.credentials.app_id == "sandbox-sq0idb-overridden"


# ---------------------------------------------------------------------------
# The narrowing, proved by the case that must fail.
# ---------------------------------------------------------------------------


def test_a_toast_seed_rejects_a_field_that_belongs_to_another_vendor() -> None:
    """mypy must refuse ``unit("toast").seed.merchant_id``.

    Run on the one file, from the repository root, so the project's strict
    configuration applies. The module lives under an excluded directory --
    the ordinary ``uv run mypy`` must stay green -- and mypy's ``exclude``
    does not apply to a path passed explicitly, which is what makes this
    arrangement possible at all.

    ``mypy`` is a dev dependency, present under ``uv run pytest`` but not
    necessarily wherever else this suite runs -- a consumer running the
    packaged tests, or a CI job installing only a test extra. Without the
    guard below, the subprocess would exit nonzero on ``No module named
    mypy``, which happens to satisfy the returncode assertion for the wrong
    reason before failing loudly one assertion later on a stdout that never
    contained a type error.
    """
    pytest.importorskip("mypy", reason="shells out to `python -m mypy`; not installed outside the dev group")
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-error-summary", str(NEGATIVE_MODULE)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0, f"mypy accepted the negative module:\n{completed.stdout}{completed.stderr}"
    assert '"ToastSeed" has no attribute "merchant_id"' in completed.stdout, completed.stdout + completed.stderr


def test_a_square_overlay_rejects_a_collection_square_does_not_have() -> None:
    """mypy must refuse an overlay key that is not one of Square's seed
    collections, in both the shapes a consumer writes one.

    Run exactly as the sibling above is, and for the same reason: the type is
    the only thing that can catch this mistake early, because a partial
    document has nothing to be wrong against at run time -- a mistyped
    collection merges cleanly and hydrates nothing. The unit refuses it when
    it starts; this asserts the editor does too.

    Two errors, not one, and the negative module's docstring says why: the
    annotated form is a plain TypedDict rejection, while the call site
    resolves to ``unit()``'s ``vendor: str`` fallback overload instead, which
    costs the vendor narrowing rather than the call. Both are asserted so that
    a change to either half is a red test rather than a quiet loosening.
    """
    pytest.importorskip("mypy", reason="shells out to `python -m mypy`; not installed outside the dev group")
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-error-summary", str(OVERLAY_NEGATIVE_MODULE)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0, f"mypy accepted the negative module:\n{completed.stdout}{completed.stderr}"
    assert 'Extra key "merchants" for TypedDict "SquareSeedOverlay"' in completed.stdout, (
        completed.stdout + completed.stderr
    )
    assert 'Expression is of type "StartedUnit[Seed]", not "StartedUnit[SquareSeed]"' in completed.stdout, (
        completed.stdout + completed.stderr
    )


def test_every_overlay_type_names_exactly_its_vendors_seed_collections() -> None:
    """The keys are typed by hand and the seed documents are data, so nothing
    but this stops the two drifting -- which is the whole failure mode the
    types exist to prevent, one level up.

    ``_comment`` is excluded deliberately: it is a document annotation, not a
    collection, and ``overlay_collections`` leaves it out of the refusal's
    listing for the same reason.
    """
    for vendor, overlay_type in (
        ("square", SquareSeedOverlay),
        ("clover", CloverSeedOverlay),
        ("toast", ToastSeedOverlay),
        ("lightspeed", LightspeedSeedOverlay),
    ):
        definition = resolve_vendor(vendor)
        document = json.loads((definition.base_dir / "seed" / "default.seed.json").read_text(encoding="utf-8"))
        assert set(overlay_type.__annotations__) == {key for key in document if not key.startswith("_")}, vendor


# ---------------------------------------------------------------------------
# The seed is never None.
# ---------------------------------------------------------------------------


def test_seed_for_still_answers_none_for_a_vendor_it_does_not_describe() -> None:
    """The ``None`` did not go away; it moved. ``seed_for`` is the low-level
    lookup and still reports absence the way a lookup should -- it is
    :func:`unit` that refuses to hand the absence on."""
    assert seed_for("acme", {}) is None


@pytest.fixture
def seedless_vendor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vendor named ``acme`` with a real profile at a temporary path.

    Constructing the case needs a vendor outside the three shipped ones,
    because whether a seed exists is a property of the vendor and not of the
    profile. The registry's lookup is substituted for the duration of the
    test; the profile it then loads is a genuine document on disk, which is
    what keeps these tests about ``unit()`` rather than about the
    substitution.
    """
    (tmp_path / "seedless.json").write_text(
        json.dumps({"name": "seedless", "capabilities": ["orders", "chaos"]}), encoding="utf-8"
    )
    definition = FakeVendor(name="acme", profile_dir=tmp_path, base_dir=tmp_path)
    monkeypatch.setattr("vendorfake.registry.resolve_vendor", lambda name: definition)


@pytest.mark.usefixtures("seedless_vendor")
def test_a_vendor_with_no_seed_is_refused_at_unit_time_rather_than_yielded_as_none() -> None:
    """A vendor with no seed used to yield ``StartedUnit(seed=None)`` and let
    every consumer discover it at the first attribute access. It is now a
    refusal at the one place that knows the vendor and the profile.
    """
    with pytest.raises(LookupError) as refused:  # noqa: SIM117 - the `with unit(...)` is the subject
        with unit("acme", "seedless") as started:
            pytest.fail(f"unit() yielded {started!r} for a vendor with no seed")

    message = str(refused.value)
    assert "'acme'" in message
    assert "'seedless'" in message
    assert NO_SEED_HINT in message


@pytest.mark.usefixtures("seedless_vendor")
def test_the_refused_unit_is_stopped_before_the_error_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal happens after the unit is built, so it must not leak one.
    ``Unit.stop`` is idempotent; what is checked here is that it ran at all.
    """
    stopped: list[bool] = []
    original = Unit.stop

    def record(self: Unit) -> None:
        stopped.append(True)
        original(self)

    monkeypatch.setattr(Unit, "stop", record)

    with pytest.raises(LookupError), unit("acme", "seedless"):
        pass

    assert stopped == [True]


# ---------------------------------------------------------------------------
# Backward compatibility: nothing on the seeds was renamed or removed.
# ---------------------------------------------------------------------------


def test_the_vendor_faithful_names_are_untouched() -> None:
    """``credentials`` is a second view, not a replacement: a v0.1.0 consumer
    reading ``application_id`` or ``client_id`` still finds it."""
    assert "application_id" in SquareSeed.__dataclass_fields__
    assert "application_secret" in SquareSeed.__dataclass_fields__
    assert "client_id" in CloverSeed.__dataclass_fields__
    assert "client_secret" in CloverSeed.__dataclass_fields__
    assert "client_id" in ToastSeed.__dataclass_fields__
    assert "client_secret" in ToastSeed.__dataclass_fields__
