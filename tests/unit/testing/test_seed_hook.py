"""A third-party vendor publishing its own seed.

FOR: closing the gap stream B's report named. ``unit()``'s fallback overload
is documented as the one a vendor from the ``vendorfake.vendors`` entry-point
group takes, and it is annotated to yield a ``StartedUnit[Seed]`` -- but
``seed_for`` was a three-way branch on the names of the three vendors shipped
here, so that overload could only ever refuse. The refusal was correct and is
still correct; what was missing was any way for such a vendor to answer.

The hook is :class:`~vendorfake.core.kernel.types.SeedingVendor`, discovered
structurally rather than declared as a required member of ``VendorDefinition``
-- the argument for that is at the protocol. What is asserted here is the
behaviour a consumer sees: a vendor that implements it gets a real seed out of
``unit("<its name>")``, one that does not is refused exactly as before, and a
vendor that implements it wrongly is told so at the moment the unit is built
rather than at the first attribute access.

The vendor is constructed rather than installed. Publishing one through a real
entry point would mean building and installing a second distribution inside a
test; the registry's lookup is substituted instead, and the profile it then
loads is a genuine document on disk, which keeps these tests about ``unit()``
rather than about the substitution. This is the technique
``tests/unit/testing/test_seed_typing.py`` already uses for the seedless case,
and these are the same case answered the other way.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.fakes import FakeVendor
from vendorfake.core.kernel.types import SeedingVendor
from vendorfake.testing import NO_SEED_HINT, Seed, unit
from vendorfake.testing.seeds import CloverSeed, SquareSeed, ToastSeed, seed_for

VENDOR_BLOCK = {"app_id": "acme-app", "app_secret": "acme-secret"}
"""The profile's own ``vendor`` block, so the hook can be shown reading it."""


@dataclass(frozen=True, slots=True)
class AcmeSeed:
    """A seed satisfying :class:`~vendorfake.testing.Seed` structurally.

    Written out rather than reusing a shipped seed type: the claim under test
    is that the hook works for a vendor this distribution knows nothing about,
    and handing back a ``SquareSeed`` would have left that unproven.
    """

    app_id: str
    app_secret: str

    @property
    def credentials(self) -> object:
        """The neutral credential view, read straight off the profile."""
        from vendorfake.testing import Credentials

        return Credentials(app_id=self.app_id, app_secret=self.app_secret, grant="client_credentials")

    @property
    def auth(self) -> Mapping[str, str]:
        """Headers that authenticate as the seeded principal."""
        return {"authorization": "Bearer acme-token"}

    @property
    def read_only_auth(self) -> Mapping[str, str]:
        """Headers that authenticate as the seeded read-only principal."""
        return {"authorization": "Bearer acme-readonly"}

    @property
    def event_types(self) -> tuple[str, ...]:
        """Every event type this vendor's scenario can emit."""
        return ("acme.order.created",)


@dataclass
class SeedingFakeVendor(FakeVendor):
    """``FakeVendor`` plus the optional hook, and a record of every call."""

    seed_calls: list[Mapping[str, object]] = field(default_factory=list)

    def seed(self, vendor_config: Mapping[str, object]) -> object:
        """This vendor's seed, built from the profile's ``vendor`` block."""
        self.seed_calls.append(dict(vendor_config))
        return AcmeSeed(
            app_id=str(vendor_config.get("app_id", "")),
            app_secret=str(vendor_config.get("app_secret", "")),
        )


@dataclass
class WrongShapeVendor(FakeVendor):
    """A vendor whose hook returns something that is not a seed at all."""

    def seed(self, vendor_config: Mapping[str, object]) -> object:
        """Deliberately wrong: a plain mapping has none of the four members."""
        return {"app_id": "acme-app"}


def _install(definition: FakeVendor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeVendor:
    """Give ``definition`` a real profile on disk and make the registry find it."""
    (tmp_path / "acme-full.json").write_text(
        json.dumps({"name": "acme-full", "capabilities": ["orders", "chaos"], "vendor": VENDOR_BLOCK}),
        encoding="utf-8",
    )
    definition.profile_dir = tmp_path
    definition.base_dir = tmp_path
    monkeypatch.setattr("vendorfake.registry.resolve_vendor", lambda name: definition)
    return definition


# ---------------------------------------------------------------------------
# The case the hook exists for.
# ---------------------------------------------------------------------------


def test_a_vendor_that_publishes_a_seed_is_not_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``unit("acme").seed`` is the vendor's own object, through the ``str``
    overload -- the one the fallback overload was always annotated to serve
    and could not, because ``seed_for`` had no way to ask the vendor."""
    definition = _install(SeedingFakeVendor(), tmp_path, monkeypatch)

    with unit("acme", "acme-full") as started:
        assert isinstance(started.seed, AcmeSeed)
        assert started.seed.credentials.app_id == "acme-app"  # type: ignore[attr-defined]
        assert started.seed.auth == {"authorization": "Bearer acme-token"}

    assert isinstance(definition, SeedingFakeVendor)
    assert definition.seed_calls == [VENDOR_BLOCK], (
        f"hook called {len(definition.seed_calls)}x: {definition.seed_calls}"
    )


def test_the_hook_reads_the_resolved_profile_not_a_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mapping handed to the hook is the profile's own ``vendor`` block.

    The same reason the built-in seeds take it: a profile may override the
    application credentials, and a seed reporting the default while the unit
    ran on an override sends a consumer chasing a 401 the fixture caused.
    """
    definition = _install(SeedingFakeVendor(), tmp_path, monkeypatch)
    (tmp_path / "overridden.json").write_text(
        json.dumps(
            {
                "name": "overridden",
                "capabilities": ["orders", "chaos"],
                "vendor": {"app_id": "other-app", "app_secret": "other-secret"},
            }
        ),
        encoding="utf-8",
    )

    with unit("acme", "overridden") as started:
        assert started.seed.credentials.app_id == "other-app"  # type: ignore[attr-defined]

    assert isinstance(definition, SeedingFakeVendor)
    assert definition.seed_calls[-1]["app_id"] == "other-app"


def test_a_hook_returning_the_wrong_shape_is_named_at_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """As a hook defect, on the vendor, rather than as an ``AttributeError``
    on ``started.seed.credentials`` three frames into a consumer's test."""
    _install(WrongShapeVendor(), tmp_path, monkeypatch)

    with pytest.raises(TypeError) as refused:  # noqa: SIM117 - the `with unit(...)` is the subject
        with unit("acme", "acme-full") as started:
            pytest.fail(f"unit() yielded {started!r} for a vendor whose hook returns a dict")

    message = str(refused.value)
    assert "'acme'" in message
    assert "vendorfake.testing.Seed" in message
    for member in ("credentials", "auth", "read_only_auth", "event_types"):
        assert member in message


def test_a_vendor_without_the_hook_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook is additive. A vendor that does not implement it behaves
    exactly as it did before the hook existed, which is the whole reason it is
    a separate protocol rather than a new required member."""
    _install(FakeVendor(), tmp_path, monkeypatch)

    with pytest.raises(LookupError) as refused:  # noqa: SIM117 - the `with unit(...)` is the subject
        with unit("acme", "acme-full") as started:
            pytest.fail(f"unit() yielded {started!r} for a vendor with no seed")

    assert NO_SEED_HINT in str(refused.value)


# ---------------------------------------------------------------------------
# The hook is discovered structurally, and it does not disturb the three
# vendors shipped here.
# ---------------------------------------------------------------------------


def test_the_protocol_recognises_only_a_vendor_that_implements_it() -> None:
    """``SeedingVendor`` is ``runtime_checkable``, which is what lets
    ``seed_for`` ask without every ``VendorDefinition`` having to answer."""
    assert isinstance(SeedingFakeVendor(), SeedingVendor)
    assert not isinstance(FakeVendor(), SeedingVendor)


@pytest.mark.parametrize(
    ("vendor", "expected"),
    [("square", SquareSeed), ("clover", CloverSeed), ("toast", ToastSeed)],
)
def test_no_shipped_vendor_implements_the_hook(vendor: str, expected: type) -> None:
    """So asking the hook first cannot change what a built-in vendor answers.

    ``seed_for`` consults the vendor before its own three-way branch, because
    a vendor's statement about itself outranks this package's table. That
    ordering is only safe while none of the three implements the hook, and
    this is what says so out loud rather than leaving it to be rediscovered.
    """
    from vendorfake.registry import resolve_vendor

    assert not isinstance(resolve_vendor(vendor), SeedingVendor)
    seed = seed_for(vendor, {})
    assert isinstance(seed, expected)


def test_seed_for_answers_none_for_a_name_that_resolves_to_nothing() -> None:
    """Unchanged from v0.1.0, and deliberately so: ``resolve_vendor`` is where
    a typo is reported, because it is the one that names the alternatives."""
    assert seed_for("nosuchvendor", {}) is None


def test_seed_for_takes_a_definition_when_the_caller_already_has_one(tmp_path: Path) -> None:
    """The keyword ``served()`` uses, so refusing a seedless vendor costs no
    second registry lookup before the child process is spawned."""
    definition = SeedingFakeVendor(profile_dir=tmp_path, base_dir=tmp_path)
    published = seed_for("acme", VENDOR_BLOCK, definition=definition)

    assert isinstance(published, AcmeSeed)
    assert published.app_id == "acme-app"


def test_a_published_seed_satisfies_the_protocol_a_consumer_reads_through() -> None:
    """The point of the shape check: what comes out of the hook is usable by a
    function typed against :class:`~vendorfake.testing.Seed` with no
    ``isinstance`` and no per-vendor branch."""

    def grant_of(seed: Seed) -> str:
        return seed.credentials.grant

    assert grant_of(AcmeSeed(app_id="a", app_secret="b")) == "client_credentials"
