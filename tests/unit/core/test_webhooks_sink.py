"""The delivery seam: two implementations, one protocol, no HTTP assumption."""

from __future__ import annotations

import httpx
import pytest

from vendorfake.core.webhooks.sink import DeliverySink, HttpSink, MemorySink, SinkRequest, SinkResult


def _req(url: str = "https://sub.test/hooks", body: bytes = b'{"a":1}', timeout_ms: int = 10_000) -> SinkRequest:
    return SinkRequest(url=url, headers={"content-type": "application/json"}, body=body, timeout_ms=timeout_ms)


# ---------------------------------------------------------------------------
# MemorySink.
# ---------------------------------------------------------------------------


def test_a_scalar_status_answers_every_call() -> None:
    sink = MemorySink(respond_with=503)
    assert [sink.send(_req()).status for _ in range(3)] == [503, 503, 503]
    assert len(sink.received) == 3


def test_the_callable_form_sees_a_zero_based_call_index() -> None:
    """ "Fail the first two attempts, then accept" is how the retry test is
    written at all, and the index is what makes it one line."""
    sink = MemorySink(respond_with=lambda _req, index: 500 if index < 2 else 200)
    assert [sink.send(_req()).status for _ in range(4)] == [500, 500, 200, 200]


def test_the_index_counts_calls_to_the_sink_and_not_attempts_for_one_event() -> None:
    """Stated because it is the plausible misreading.

    A test that registered two subscribers and expected ``index`` to restart
    per subscriber would be surprised; it counts sends, in send order, which is
    what makes it usable at all with one delivery worker.
    """
    seen: list[int] = []
    sink = MemorySink(respond_with=lambda _req, index: seen.append(index) or 200)  # type: ignore[func-returns-value]
    sink.send(_req(url="https://a.test/x"))
    sink.send(_req(url="https://b.test/x"))
    assert seen == [0, 1]


def test_status_zero_means_nothing_came_back() -> None:
    """The contract the retry classification reads.

    ``0`` is not a status; it is the absence of one. The reference uses it for
    the same purpose, and returning it as a *result* rather than raising is
    what lets a test say "this attempt timed out" in one assignment.
    """
    result = MemorySink(respond_with=0).send(_req())
    assert result == SinkResult(status=0, error="simulated transport failure", timed_out=True)


def test_a_memory_sink_keeps_the_exact_bytes_it_was_handed() -> None:
    """The signature was computed over these bytes before the request existed.

    A sink that re-serialised -- or that stored the parsed object and rebuilt
    the bytes on read -- would invalidate a signature while every assertion
    still passed, which is the failure this whole design is arranged against.
    """
    body = '{"merchant":"café","n":1}'.encode()
    sink = MemorySink()
    sink.send(_req(body=body))
    assert sink.received[0].body == body


def test_clearing_forgets_deliveries_but_not_the_responder() -> None:
    sink = MemorySink(respond_with=418)
    sink.send(_req())
    sink.clear()
    assert sink.received == []
    assert sink.send(_req()).status == 418


def test_the_sinks_satisfy_one_protocol() -> None:
    """Structural, not nominal: nothing here inherits from anything.

    The protocol is the design point of this module -- the dispatcher's whole
    knowledge of transport -- so it is asserted rather than assumed.
    """
    sinks: list[DeliverySink] = [MemorySink(), HttpSink()]
    assert [s.kind for s in sinks] == ["memory", "http"]


# ---------------------------------------------------------------------------
# HttpSink.
# ---------------------------------------------------------------------------


def test_an_http_sink_opens_nothing_until_it_sends() -> None:
    """A unit whose vendor has no webhooks must not open a connection pool.

    Every unit builds one of these at construction, so eager client creation
    would leak a pool per unit across a suite that builds hundreds.
    """
    sink = HttpSink()
    assert sink._client is None


def test_an_http_sink_reports_a_status_and_a_snippet() -> None:
    sink = HttpSink()
    sink._client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(202, text="thanks " + "x" * 500))
    )
    result = sink.send(_req())
    assert result.status == 202
    assert result.body_snippet is not None
    assert len(result.body_snippet) == 200
    assert result.timed_out is False


def test_a_timeout_is_reported_as_timed_out_and_not_merely_as_no_status() -> None:
    """The two are different facts and a vendor maps them to different wire
    strings; collapsing them loses the distinction before the vendor sees it."""

    def raise_timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=_request)

    sink = HttpSink()
    sink._client = httpx.Client(transport=httpx.MockTransport(raise_timeout))
    result = sink.send(_req())
    assert (result.status, result.timed_out) == (0, True)
    assert result.error is not None


def test_a_transport_failure_is_reported_as_no_status_and_not_a_timeout() -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=_request)

    sink = HttpSink()
    sink._client = httpx.Client(transport=httpx.MockTransport(refuse))
    result = sink.send(_req())
    assert (result.status, result.timed_out) == (0, False)
    assert result.error is not None and result.error.strip() != ""


def test_an_error_description_is_never_empty() -> None:
    """``str(httpx.ConnectError())`` is often ``''``.

    An empty ``error`` on a delivery record is indistinguishable from no error
    at all, which is the difference between "the subscriber refused" and "the
    subscriber accepted".
    """

    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=_request)

    sink = HttpSink()
    sink._client = httpx.Client(transport=httpx.MockTransport(refuse))
    assert sink.send(_req()).error == "ConnectError"


def test_a_redirect_is_not_followed() -> None:
    """A subscriber answering 302 has not accepted the delivery.

    Following it would report the redirect target's status as the subscriber's
    -- so a misconfigured subscriber redirecting to a friendly 200 would look
    healthy while receiving nothing.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://elsewhere.test/ok"})

    sink = HttpSink()
    sink._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert sink.send(_req()).status == 302
    assert seen == ["https://sub.test/hooks"]


def test_closing_an_unused_sink_is_harmless() -> None:
    HttpSink().close()


@pytest.mark.parametrize("timeout_ms", [1, 250, 10_000])
def test_the_timeout_reaches_httpx_in_seconds(timeout_ms: int) -> None:
    """Milliseconds on the policy, seconds at the client. The conversion is one
    division and is exactly the kind of thing that is right until it is not."""
    seen: list[httpx.Timeout] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200)

    sink = HttpSink()
    sink._client = httpx.Client(transport=httpx.MockTransport(handler))
    sink.send(_req(timeout_ms=timeout_ms))
    assert seen[0] == {
        "connect": timeout_ms / 1000,
        "read": timeout_ms / 1000,
        "write": timeout_ms / 1000,
        "pool": timeout_ms / 1000,
    }
