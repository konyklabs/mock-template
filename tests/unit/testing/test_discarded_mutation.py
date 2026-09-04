"""A response-phase fault after a commit is visible in the request log, and a
rule-authoring refusal names its rule in a header.

konyklabs/roadmap#101, items 17b and 19. The scenario is the one the
feedback found: ``malformed_body`` against Clover's single-use refresh
rotates the token and hands the caller an HTML 502 -- the credential is
spent by a call that looked like it failed. Before, only the journal knew.
"""

from __future__ import annotations

import pytest

from vendorfake.testing import CloverSeed, StartedUnit, unit

REFRESH = "POST /oauth/v2/refresh"


def _refresh(clover: StartedUnit[CloverSeed], refresh_token: str) -> object:
    return clover.client.post(
        "/oauth/v2/refresh",
        json={"client_id": clover.seed.credentials.app_id, "refresh_token": refresh_token},
    )


@pytest.fixture
def clover():  # type: ignore[no-untyped-def]
    with unit("clover") as started:
        yield started


def test_a_response_phase_fault_after_a_commit_is_marked_on_the_request_log(clover: StartedUnit[CloverSeed]) -> None:
    clover.add_chaos_rule(
        {
            "id": "mangle",
            "scope": "request",
            "fault": "malformed_body",
            "match": {"route": REFRESH},
            "params": {"mode": "html"},
            "when": {"nth": [1]},
        }
    )
    seq_before = clover.client.get("/__unit/info").json()["state"]["journal_seq"]
    mangled = _refresh(clover, clover.seed.token.refresh_token or "")
    assert mangled.status_code == 502  # type: ignore[attr-defined]
    assert mangled.headers["content-type"] == "text/html"  # type: ignore[attr-defined]

    (row,) = clover.requests(route=REFRESH)
    assert row["fault"] == "malformed_body"
    assert row["discarded_mutation"] is True
    assert row["committed_journal_seq"] > seq_before
    # The seq points at the commit the caller never saw succeed.
    journal = clover.client.get("/__unit/journal", params={"since": seq_before}).json()["entries"]
    assert journal[-1]["seq"] == row["committed_journal_seq"]

    # And the credential is spent: the stored token is now a 401 -- a refusal, so no commit.
    spent = _refresh(clover, clover.seed.token.refresh_token or "")
    assert spent.status_code == 401  # type: ignore[attr-defined]
    newest, _older = clover.requests(route=REFRESH)
    assert newest["status"] == 401
    assert newest["discarded_mutation"] is False
    assert "committed_journal_seq" not in newest


def test_slow_body_delivers_the_commit_intact_and_is_not_discarded(clover: StartedUnit[CloverSeed]) -> None:
    """The one response-phase fault that leaves the answer alone: the caller
    has the rotated token, only later, so nothing was discarded."""
    clover.add_chaos_rule(
        {
            "id": "drip",
            "scope": "request",
            "fault": "slow_body",
            "match": {"route": REFRESH},
            "params": {"chunk_bytes": 4096, "chunk_delay_ms": 1},
            "when": {"nth": [1]},
        }
    )
    late = _refresh(clover, clover.seed.token.refresh_token or "")
    assert late.status_code == 200  # type: ignore[attr-defined]
    assert late.headers["vendorfake-fault"] == "slow_body"  # type: ignore[attr-defined]
    (row,) = clover.requests(route=REFRESH)
    assert row["fault"] == "slow_body"
    assert row["discarded_mutation"] is False
    assert isinstance(row["committed_journal_seq"], int)


def test_a_request_phase_fault_commits_nothing_and_says_so(clover: StartedUnit[CloverSeed]) -> None:
    clover.add_chaos_rule(
        {"id": "outage", "scope": "request", "fault": "unavailable", "match": {"route": REFRESH}, "when": {"nth": [1]}}
    )
    refused = _refresh(clover, clover.seed.token.refresh_token or "")
    assert refused.status_code == 503  # type: ignore[attr-defined]
    (row,) = clover.requests(route=REFRESH)
    assert row["fault"] == "unavailable"
    assert row["discarded_mutation"] is False
    assert "committed_journal_seq" not in row


def test_a_clean_commit_names_its_seq_and_is_not_discarded(clover: StartedUnit[CloverSeed]) -> None:
    ok = _refresh(clover, clover.seed.token.refresh_token or "")
    assert ok.status_code == 200  # type: ignore[attr-defined]
    (row,) = clover.requests(route=REFRESH)
    assert row["discarded_mutation"] is False
    assert isinstance(row["committed_journal_seq"], int)


def test_a_read_commits_nothing(clover: StartedUnit[CloverSeed]) -> None:
    answered = clover.client.get(clover.seed.path(), headers=clover.seed.auth)
    assert answered.status_code == 200, answered.text
    (row,) = clover.requests(route="GET /v3/merchants/{mId}")
    assert row["discarded_mutation"] is False
    assert "committed_journal_seq" not in row


def test_a_malformed_body_rule_with_a_bad_mode_is_refused_the_same_way(clover: StartedUnit[CloverSeed]) -> None:
    clover.add_chaos_rule(
        {
            "id": "badmode",
            "scope": "request",
            "fault": "malformed_body",
            "match": {"route": REFRESH},
            "params": {"mode": "garbage"},
        }
    )
    refused = _refresh(clover, clover.seed.token.refresh_token or "")
    assert refused.status_code == 400, refused.text  # type: ignore[attr-defined]
    assert refused.headers["vendorfake-rule-error"] == "badmode"  # type: ignore[attr-defined]
    assert "vendorfake-fault" not in refused.headers  # type: ignore[attr-defined]
    # The handler committed before the payout refused: the rotation is spent, and the log says so.
    (row,) = clover.requests(route=REFRESH)
    assert row["discarded_mutation"] is True


def test_a_rule_authoring_refusal_names_the_rule_in_a_header(clover: StartedUnit[CloverSeed]) -> None:
    """Item 19: the pointer exists in the 200 and not in the 401 that follows
    (the token was spent by the first call, item 17), so the second call is
    the fake refusing the rule, not the vendor failing. The status is a 400
    either way; the header is what tells the two apart without a body."""
    clover.add_chaos_rule(
        {
            "id": "drop-token",
            "scope": "request",
            "fault": "body_mutation",
            "match": {"route": REFRESH},
            "params": {"ops": [{"op": "remove", "pointer": "/access_token"}]},
        }
    )
    stored = clover.seed.token.refresh_token or ""
    first = _refresh(clover, stored)
    assert first.status_code == 200  # type: ignore[attr-defined]
    assert "access_token" not in first.json()  # type: ignore[attr-defined]
    assert first.headers["vendorfake-fault"] == "body_mutation"  # type: ignore[attr-defined]

    second = _refresh(clover, stored)
    assert second.status_code == 400, second.text  # type: ignore[attr-defined]
    assert second.headers["vendorfake-rule-error"] == "drop-token"  # type: ignore[attr-defined]
    assert "vendorfake-fault" not in second.headers  # type: ignore[attr-defined]
    assert "vendorfake-rule" not in second.headers  # type: ignore[attr-defined]
