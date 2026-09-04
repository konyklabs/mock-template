"""Webhook goldens: a captured delivery against a signer, and what a divergence says.

The golden under ``goldens/`` was produced by :func:`stub_signer` below and
says so in its ``source.note``. Nothing in this repository has been recorded
from a real vendor account, and a golden that claimed otherwise would be the
one thing the format exists to prevent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from vendorfake.core.kernel.types import SignInput
from vendorfake.fidelity.webhooks import (
    GOLDEN_SCHEMA,
    Golden,
    GoldenError,
    format_goldens,
    load_goldens,
    run_goldens,
    verify_golden,
)

GOLDENS = Path(__file__).parent / "goldens"

SIGNATURE_HEADER = "X-Stub-Signature"


def stub_signer(payload: SignInput) -> Mapping[str, str]:
    """A signing scheme with the three properties a real one has: bound to the
    URL, to the exact bytes, and to the secret."""
    digest = hmac.new(
        payload.secret.encode("utf-8"),
        payload.notification_url.encode("utf-8") + payload.raw_body,
        hashlib.sha256,
    ).digest()
    return {SIGNATURE_HEADER: base64.b64encode(digest).decode("ascii"), "x-stub-attempt": str(payload.attempt)}


def _doc(**changes: Any) -> dict[str, Any]:
    doc = json.loads((GOLDENS / "stub-delivery.json").read_text(encoding="utf-8"))
    for dotted, value in changes.items():
        parts = dotted.split(".")
        node = doc
        for part in parts[:-1]:
            node = node[part]
        if value is ...:
            del node[parts[-1]]
        else:
            node[parts[-1]] = value
    return doc


# ---------------------------------------------------------------------------
# Verification.
# ---------------------------------------------------------------------------


def test_a_golden_the_signer_reproduces_has_no_divergences() -> None:
    ((name, golden),) = load_goldens(GOLDENS)
    assert name == "stub-delivery.json"
    assert verify_golden(golden, stub_signer) == ()


def test_a_tampered_signature_header_is_one_divergence_naming_both_values() -> None:
    tampered = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    golden = Golden.of(_doc(**{f"delivery.headers.{SIGNATURE_HEADER}": tampered}))
    (divergence,) = verify_golden(golden, stub_signer)
    assert divergence.kind == "signature"
    assert divergence.header == SIGNATURE_HEADER.lower()
    assert divergence.expected == tampered
    assert divergence.actual is not None and divergence.actual != tampered


def test_a_header_the_signer_does_not_produce_at_all_is_its_own_kind() -> None:
    golden = Golden.of(_doc())

    def silent(payload: SignInput) -> Mapping[str, str]:
        return {"x-stub-attempt": "1"}

    (divergence,) = verify_golden(golden, silent)
    assert (divergence.kind, divergence.header, divergence.actual) == ("missing", SIGNATURE_HEADER.lower(), None)
    assert "signer produced nothing" in str(divergence)


def test_header_names_are_compared_case_insensitively_and_values_are_not() -> None:
    golden = Golden.of(_doc())

    def shouting(payload: SignInput) -> Mapping[str, str]:
        return {name.upper(): value.upper() for name, value in stub_signer(payload).items()}

    # Both names matched, so neither is "missing"; the upper-cased signature did not.
    (divergence,) = verify_golden(golden, shouting)
    assert (divergence.kind, divergence.header) == ("signature", SIGNATURE_HEADER.lower())


def test_the_signature_is_bound_to_the_bytes_the_delivery_carried() -> None:
    """One byte of the body changes the signature: the golden is evidence about bytes."""
    body = json.loads((GOLDENS / "stub-delivery.json").read_text(encoding="utf-8"))["delivery"]["body"]
    golden = Golden.of(_doc(**{"delivery.body": body.replace("MER_1", "MER_2")}))
    (divergence,) = verify_golden(golden, stub_signer)
    assert divergence.kind == "signature"


def test_a_base64_body_signs_the_same_bytes_as_the_text_one() -> None:
    text = json.loads((GOLDENS / "stub-delivery.json").read_text(encoding="utf-8"))["delivery"]["body"]
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    doc = _doc(**{"delivery.body": ...})
    doc["delivery"]["body_b64"] = encoded
    assert verify_golden(Golden.of(doc), stub_signer) == ()


# ---------------------------------------------------------------------------
# What a golden must say to be one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "vendorfake.webhook-golden/2"}, f"expected {GOLDEN_SCHEMA!r}"),
        ({"secret": ...}, "exactly one of secret"),
        ({"delivery.url": ...}, "delivery has no url"),
        ({"delivery.body": ...}, "exactly one of body"),
        ({"signature_headers": []}, "a golden with nothing to compare proves nothing"),
        ({"signature_headers": ["x-not-sent"]}, "which the delivery does not carry"),
    ],
)
def test_a_malformed_golden_is_refused_by_name(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(GoldenError) as raised:
        Golden.of(_doc(**changes), where="g.json")
    assert message in str(raised.value)


def test_a_golden_claiming_to_be_recorded_needs_the_recording_fields() -> None:
    """The same rule a corpus case obeys, enforced by the same schema."""
    with pytest.raises(GoldenError) as raised:
        Golden.of(_doc(**{"source.provenance": "recorded"}), where="g.json")
    text = str(raised.value)
    assert "source is not a valid provenance block" in text
    assert "'environment' is a required property" in text


def test_the_shipped_fixture_does_not_claim_to_be_a_recording() -> None:
    ((_, golden),) = load_goldens(GOLDENS)
    assert golden.source.provenance == "judgment"
    assert "NOT A RECORDING" in golden.source.note


# ---------------------------------------------------------------------------
# A directory of them.
# ---------------------------------------------------------------------------


def test_run_goldens_reports_each_file_and_a_total() -> None:
    text = format_goldens(run_goldens(GOLDENS, stub_signer))
    assert "[PASS] stub-delivery.json (synthetic, judgment)" in text
    assert "1 passed, 0 failed" in text
    assert text.endswith("\nOK")


def test_a_failing_directory_renders_the_divergence_under_the_file(tmp_path: Path) -> None:
    tampered = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    (tmp_path / "one.json").write_text(json.dumps(_doc(**{f"delivery.headers.{SIGNATURE_HEADER}": tampered})))
    text = format_goldens(run_goldens(tmp_path, stub_signer))
    assert "[FAIL] one.json" in text
    assert f"        {SIGNATURE_HEADER.lower()}: expected '{tampered}'" in text
    assert text.endswith("\n0 passed, 1 failed\nNOT OK")


def test_a_recorded_golden_prints_the_account_it_came_from(tmp_path: Path) -> None:
    recorded = _doc()
    recorded["source"] = {
        **recorded["source"],
        "provenance": "recorded",
        "environment": "sandbox",
        "api_version": "2026-08-20",
        "recorded": "2026-09-01",
        "script": "tools/record.py",
        "redaction": "the merchant id",
    }
    (tmp_path / "one.json").write_text(json.dumps(recorded))
    text = format_goldens(run_goldens(tmp_path, stub_signer))
    assert "(synthetic, recorded sandbox 2026-08-20 on 2026-09-01)" in text


def test_a_directory_that_is_not_there_says_so(tmp_path: Path) -> None:
    with pytest.raises(GoldenError) as raised:
        load_goldens(tmp_path / "nope")
    assert "no such directory of goldens" in str(raised.value)
