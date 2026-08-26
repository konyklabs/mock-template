"""Semantics of in-band fault triggering.

The interesting claims are all about precedence and about what is *not* a
magic value: extraction is the pure half of a mechanism whose impure half is
gated, so everything a reviewer could disagree about lives here.
"""

from __future__ import annotations

from vendorfake.core.kernel.magic import NO_MAGIC, extract_magic
from vendorfake.core.kernel.types import FormData, MagicTriggerSpec, UnitRequest

SPEC = MagicTriggerSpec(
    prefix="chaos:",
    body_paths=("idempotency_key", "order.reference_id"),
    query_params=("chaos",),
    headers=("x-chaos",),
)


def request(*, query: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> UnitRequest:
    return UnitRequest(
        id="req-1",
        method="POST",
        path="/v2/orders",
        query=query or {},
        headers=headers or {},
        raw_body=b"",
        transport="inprocess",
        received_at="2024-01-01T00:00:00.000Z",
    )


def test_no_spec_means_no_magic() -> None:
    """A vendor that declares nothing gets the shared empty result, not a new one."""
    assert extract_magic(None, request(), {"order": {"reference_id": "chaos:rate_limit"}}) is NO_MAGIC


def test_a_declared_body_path_arms_a_fault() -> None:
    got = extract_magic(SPEC, request(), {"order": {"reference_id": "chaos:rate_limit"}})
    assert got.faults == ("rate_limit",)
    assert got.armed is True
    assert dict(got.params) == {}


def test_parameters_are_split_on_the_first_equals_only() -> None:
    body = {"idempotency_key": "chaos:timeout:delay_ms=250:note=a=b"}
    got = extract_magic(SPEC, request(), body)
    assert got.faults == ("timeout",)
    assert dict(got.params) == {"delay_ms": "250", "note": "a=b"}


def test_a_leading_equals_names_no_parameter() -> None:
    """Ported literally: `indexOf('=') > 0`, not `>= 0`. A pair that starts with
    '=' carries no key, and inventing an empty-string key would put a parameter
    into the decision that nobody wrote."""
    got = extract_magic(SPEC, request(), {"idempotency_key": "chaos:timeout:=250"})
    assert got.faults == ("timeout",)
    assert dict(got.params) == {}


def test_a_bare_prefix_names_no_fault_and_is_skipped_not_rejected() -> None:
    """`chaos:` alone. This is a field the vendor uses for its own purposes, so
    a 400 here would make an ordinary reference id a hazard."""
    assert extract_magic(SPEC, request(), {"idempotency_key": "chaos:"}) is NO_MAGIC


def test_a_value_without_the_prefix_is_an_ordinary_value() -> None:
    assert extract_magic(SPEC, request(), {"idempotency_key": "rate_limit"}) is NO_MAGIC
    assert extract_magic(SPEC, request(), {"order": {"reference_id": "my-chaos:rate_limit"}}) is NO_MAGIC


def test_only_strings_are_candidates() -> None:
    """A numeric reference id is not a magic value in any vendor's vocabulary,
    and stringifying it would make 42 a candidate for a prefix nobody typed."""
    assert extract_magic(SPEC, request(), {"idempotency_key": 42}) is NO_MAGIC
    assert extract_magic(SPEC, request(), {"idempotency_key": None}) is NO_MAGIC
    assert extract_magic(SPEC, request(), {"idempotency_key": ["chaos:rate_limit"]}) is NO_MAGIC


def test_candidate_order_is_body_then_query_then_header() -> None:
    """Contract, not incidental: later candidates overwrite earlier parameters
    under the same key, so 'the header wins' is a statement a test can make."""
    body = {"idempotency_key": "chaos:timeout:delay_ms=10"}
    got = extract_magic(
        SPEC,
        request(query={"chaos": "chaos:server_error"}, headers={"x-chaos": "chaos:unavailable:delay_ms=99"}),
        body,
    )
    assert got.faults == ("timeout", "server_error", "unavailable")
    assert dict(got.params) == {"delay_ms": "99"}


def test_only_the_first_fault_would_be_armed_but_every_one_is_reported() -> None:
    """One fault per request, as one rule fires per subject. The rest are kept
    so a body carrying two magic values is visible rather than half-honoured."""
    body = {"idempotency_key": "chaos:rate_limit", "order": {"reference_id": "chaos:server_error"}}
    got = extract_magic(SPEC, request(), body)
    assert got.faults == ("rate_limit", "server_error")


def test_header_names_are_matched_case_insensitively() -> None:
    spec = MagicTriggerSpec(prefix="chaos:", headers=("X-Chaos",))
    got = extract_magic(spec, request(headers={"x-chaos": "chaos:unavailable"}), {})
    assert got.faults == ("unavailable",)


def test_a_form_encoded_body_reaches_the_same_paths() -> None:
    """Recorded divergence from the reference, which fed extraction from a
    JSON-only reader and so could never see a form field. One body reader means
    one answer; keeping a second would re-create the drift it removes."""
    form = FormData([("idempotency_key", "chaos:rate_limit")])
    assert extract_magic(SPEC, request(), form).faults == ("rate_limit",)
