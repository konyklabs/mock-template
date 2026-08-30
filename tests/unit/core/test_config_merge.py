"""The one merge idiom every vendor config uses."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.config.models import merged_over


class Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "base"
    ttl_ms: int = Field(default=1000, gt=0)
    scopes: tuple[str, ...] = ("READ",)


def test_the_block_wins_and_everything_else_is_kept() -> None:
    base = Cfg(name="mine", ttl_ms=5)
    merged = merged_over(base, {"ttl_ms": 60_000})
    assert merged == Cfg(name="mine", ttl_ms=60_000, scopes=("READ",))
    assert base.ttl_ms == 5  # frozen, and never mutated


def test_the_result_is_the_bases_own_type() -> None:
    class Sub(Cfg):
        extra_bit: bool = False

    merged = merged_over(Sub(), {"extra_bit": True})
    assert type(merged) is Sub
    assert merged.extra_bit is True


def test_an_unknown_key_is_refused_naming_it() -> None:
    with pytest.raises(ValidationError) as excinfo:
        merged_over(Cfg(), {"nonsense": True})
    assert excinfo.value.errors()[0]["loc"] == ("nonsense",)


def test_the_merge_revalidates_rather_than_patching() -> None:
    """A field constraint holds on the merged value, which `model_copy(update=)`
    would have skipped."""
    with pytest.raises(ValidationError) as excinfo:
        merged_over(Cfg(), {"ttl_ms": 0})
    assert excinfo.value.errors()[0]["loc"] == ("ttl_ms",)


def test_json_shaped_containers_are_coerced_as_a_profile_writes_them() -> None:
    assert merged_over(Cfg(), {"scopes": ["A", "B"]}).scopes == ("A", "B")


def test_an_empty_block_is_the_identity() -> None:
    base = Cfg(name="x")
    assert merged_over(base, {}) == base
