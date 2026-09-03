# Journal and request log

Two different records of "what happened", answering two different
questions, and easy to reach for the wrong one.

## The journal: what changed

`GET /__unit/journal?since=N` lists every **state mutation**, in order,
with what changed and which operation did it:

```sh
curl -s "http://localhost:8080/__unit/journal?since=19"
```

The journal is written by the state store itself, so only a *committed*
mutation appears in it. A read, a 4xx, and a call that matched no route
leave no trace here at all — which is exactly why the request log exists
alongside it.

`GET /__unit/state`, `GET /__unit/state/snapshot` and
`POST /__unit/state/reset` read and reset the store the journal describes;
`POST /__unit/state/restore` replays a snapshot. `reset()` on a
[driver](driver.md) returns to the seed scenario and also drops every
webhook subscriber a test registered — subscribe again after a reset, not
before it.

## The request log: what was called

`GET /__unit/requests` (filters: `operation_id`, `route`, `unmatched`,
`limit`), `DELETE /__unit/requests`, and
`GET /__unit/requests/unmatched/near-misses` answer the question the
journal cannot: what actually landed on this unit, matched or not, 2xx or
4xx.

```python
with unit("square") as square:
    place_an_order(base_url=square.base_url)  # the code under test

    square.assert_called("CreateOrder", times=1)  # fails listing what WAS called
    (call,) = square.requests(operation_id="CreateOrder")
    assert call["status"] == 200
    assert call.get("fault") is None  # a key with nothing to say is absent

    square.requests(unmatched=True)  # anything that landed nowhere
    square.clear_requests()  # draw a line under setup
```

The log is a ring holding the last 10,000 requests by default
(`requests: {"capacity": N}` in a profile, or
`VENDORFAKE_REQUEST_LOG_CAPACITY`; zero switches it off). It records no
bodies and no headers, control-plane calls never appear in it, and
`reset()` clears it along with the state store.

## Near misses

An unmatched request scores every route in the *active* capability set
against the path and method actually called, and reports the closest
matches — deterministically, so the same typo prints the same diagnosis
every run. In-process, that diagnosis is the message on
`vendorfake.testing.UnmatchedRequest` (an `AssertionError` by default — see
[Unit → Unmatched requests](unit.md#unmatched-requests)); over HTTP, the
same ranking rides on every unmatched response as the
`Vendorfake-Near-Miss` header, so a served unit or the container reports it
too:

```sh
curl -si http://localhost:8080/oauth2/tokens -X POST | grep -i near-miss
# vendorfake-near-miss: [{"route":"POST /oauth2/token","score":0.7,"operation_id":"ObtainToken"}, ...]
```

`GET /__unit/requests/unmatched/near-misses` is the same diagnosis, read
back from the request log rather than off a single response.

## Which one answers your question

| Question | Answer |
|---|---|
| "Did the order actually get created?" | Journal (a committed mutation) |
| "Did my client call the right path?" | Request log |
| "Why did my client get a 404 / near-miss?" | Request log, `unmatched=True`, or the `Vendorfake-Near-Miss` header |
| "What is the state right now?" | `GET /__unit/state` / `state/snapshot` |
