"""C09, C16, C18, C21, C29 -- signing is what the vendor says it is, delivery is
unbranded and gated, the schedule is followed, and delivery faults are real.

C09 asks the signing scheme what it depends on and then checks each direction
*in the direction declared*. That is the difference between a conformance
suite and a re-statement of the first vendor's answers: a scheme that sends a
static shared header is conformant, not merely tolerated, provided it declares
itself that way. Four observations are needed and not three -- three
subscriptions separate the URL and the secret but cannot separate the body,
and the body is the input a real signer is most likely to get wrong.

C16 asserts the split the core and the vendor make over a delivery: the core
sends a body and a content type, and every other header on the wire -- the
signature, the retry number, the retry reason, the initial-delivery timestamp
-- is named by the vendor. A core that branded a delivery with a vendor's
header names would make vendor number two inherit vendor number one's wire
format, which is the coupling this architecture exists to refuse.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceFailure, ConformanceSkip, Requires, require
from vendorfake.core.capability.gates import CoreCapability
from vendorfake.core.chaos.rules import BUILTIN_FAULTS

__all__ = [
    "delivery_headers_are_the_vendors_own",
    "every_delivery_fault_has_its_effect",
    "signing_matches_the_declared_bindings",
    "the_declared_retry_schedule_is_the_one_followed",
    "the_webhooks_capability_gate_is_real",
]

_URL_A = "https://receiver-a.conformance.test/hooks"
_URL_B = "https://receiver-b.conformance.test/hooks"
_SECRET_ONE = "conformance-signature-key-one"
_SECRET_TWO = "conformance-signature-key-two"
_EVENT_TYPE = "conformance.probe"
_ENTITY = "conformance-entity-1"

CORE_HEADER_NAMESPACE = "x-unit-"
"""The core's own header namespace, used on *responses* to a consumer.

A delivery carrying one would mean the core had branded an outbound webhook,
which is the direction the neutrality rule runs in: the core knows the outcome
of an attempt, and the vendor knows what to call it.
"""


def _subscribe(
    env: CheckEnv,
    subscriber_id: str,
    url: str,
    secret: str,
    *,
    event_types: tuple[str, ...] = (_EVENT_TYPE,),
) -> None:
    answered = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}webhooks/subscriptions",
        json_body={
            "id": subscriber_id,
            "notification_url": url,
            "signature_key": secret,
            "event_types": list(event_types),
        },
    )
    require(
        answered.status == 201,
        f"POST /__unit/webhooks/subscriptions answered {answered.status} for {subscriber_id!r}: "
        f"{answered.text}. A consumer must be able to register a receiver without holding a vendor "
        f"credential -- that is what makes 'test my webhook handler' one call.",
    )


def _emit(env: CheckEnv, body: Any, *, drain: bool = True) -> str:
    """Enqueue one synthetic event, and by default settle everything it causes.

    ``drain=False`` exists for the one contract that is about *when* a retry
    happens: draining a virtual clock advances it to every pending timer in
    turn (core/webhooks/dispatcher.py::drain), which runs the whole retry
    cascade before the caller has had a chance to observe a single interval.
    """
    answered = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}webhooks/emit",
        json_body={"type": _EVENT_TYPE, "entity_id": _ENTITY, "body": body},
    )
    require(
        answered.status == 202,
        f"POST /__unit/webhooks/emit answered {answered.status}: {answered.text}. The emitter is "
        f"how a profile with no mutating route makes a delivery happen at all.",
    )
    if drain:
        env.client.call("POST", f"{CONTROL_PREFIX}webhooks/drain", json_body={})
    return str(answered.json()["event_id"])


def _records(env: CheckEnv, event_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in env.deliveries() if row["event_id"] == event_id]


def _one(env: CheckEnv, event_id: str, subscriber_id: str) -> dict[str, Any]:
    for row in _records(env, event_id):
        if row["subscription_id"] == subscriber_id:
            return row
    raise ConformanceFailure(
        f"no delivery was recorded for subscriber {subscriber_id!r} and event {event_id!r}. Either "
        f"the dispatcher's journal listener never attached (check the webhooks capability gate in "
        f"core/webhooks/dispatcher.py::attach) or subscription matching dropped it "
        f"(core/webhooks/models.py::matches_event_type)."
    )


def _signature(record: dict[str, Any], headers: list[str]) -> tuple[str | None, ...]:
    sent = {str(name).lower(): str(value) for name, value in dict(record["headers"]).items()}
    return tuple(sent.get(name) for name in headers)


@check(
    id="C09",
    name="webhooks: signing is deterministic and matches the declared bindings",
    asserts=(
        "The signature is identical for identical input and across attempts, and changes with the "
        "URL, the secret and the body if and only if the signer declares that binding."
    ),
    requires=Requires(signer=True, signature_headers=True, webhooks=True, memory_sink=True),
)
def signing_matches_the_declared_bindings(env: CheckEnv) -> str:
    declared = env.signer()
    if declared is None:  # pragma: no cover - the precondition already refused this
        raise ConformanceFailure("the vendor declares no signer")
    bindings = dict(declared["bindings"])
    headers = [str(name).lower() for name in bindings["signature_headers"]]
    url_bound = bool(bindings["url_bound"])
    body_bound = bool(bindings["body_bound"])
    secret_bound = bool(bindings["secret_bound"])

    _subscribe(env, "conformance-a", _URL_A, _SECRET_ONE)
    _subscribe(env, "conformance-b", _URL_B, _SECRET_ONE)
    _subscribe(env, "conformance-c", _URL_A, _SECRET_TWO)

    first_event = _emit(env, {"probe": "one"})
    sig_a = _signature(_one(env, first_event, "conformance-a"), headers)
    sig_b = _signature(_one(env, first_event, "conformance-b"), headers)
    sig_c = _signature(_one(env, first_event, "conformance-c"), headers)
    require(
        all(part is not None for part in sig_a),
        f"the delivery to conformance-a carries no {headers} header, though the signer declares it "
        f"as its signature header. WebhookSigner.sign must return it and the dispatcher must merge "
        f"it into the attempt headers (core/webhooks/dispatcher.py).",
    )

    def state(bound: bool) -> str:
        return "declares it is bound to" if bound else "declares it is NOT bound to"

    require(
        (sig_a != sig_b) is url_bound,
        f"the signer {state(url_bound)} the notification URL, and two subscribers with the same "
        f"secret and body but different URLs produced "
        f"{'different' if sig_a != sig_b else 'identical'} signatures. Either the scheme or its "
        f"SignerProperties.url_bound declaration is wrong -- and the declaration is what a "
        f"consumer verifying a signature will build against.",
    )
    require(
        (sig_a != sig_c) is secret_bound,
        f"the signer {state(secret_bound)} the subscription secret, and two subscribers with the "
        f"same URL and body but different signature keys produced "
        f"{'different' if sig_a != sig_c else 'identical'} signatures. Fix the scheme or "
        f"SignerProperties.secret_bound.",
    )

    second_event = _emit(env, {"probe": "two", "different": True})
    sig_a2 = _signature(_one(env, second_event, "conformance-a"), headers)
    require(
        (sig_a != sig_a2) is body_bound,
        f"the signer {state(body_bound)} the body, and the same subscriber signing two different "
        f"bodies produced {'different' if sig_a != sig_a2 else 'identical'} signatures. Three "
        f"subscriptions cannot separate this dimension at all, which is why this observation "
        f"exists: a signer genuinely bound to URL and secret but not to body would otherwise pass. "
        f"Fix the scheme or SignerProperties.body_bound.",
    )

    third_event = _emit(env, {"probe": "one"})
    sig_a3 = _signature(_one(env, third_event, "conformance-a"), headers)
    require(
        sig_a3 == sig_a,
        f"the same subscriber signing the same body twice produced different signatures "
        f"({sig_a} then {sig_a3}). Signing must be a pure function of its declared inputs; a "
        f"timestamp or a nonce mixed into the payload makes every delivery unverifiable by a "
        f"consumer replaying it.",
    )

    for subscriber in ("conformance-b", "conformance-c"):
        env.client.call("DELETE", f"{CONTROL_PREFIX}webhooks/subscriptions/{subscriber}")
    programmed = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}webhooks/sink",
        json_body={"statuses": [500], "then": 200},
    )
    require(
        programmed.status == 200,
        f"POST /__unit/webhooks/sink answered {programmed.status}: {programmed.text}. Forcing a "
        f"retry from outside the process is the only way a language-independent check can observe "
        f"one; it needs the in-memory sink.",
    )
    retried_event = _emit(env, {"probe": "one"})
    attempts = sorted(
        (row for row in _records(env, retried_event) if row["subscription_id"] == "conformance-a"),
        key=lambda row: int(row["attempt"]),
    )
    require(
        len(attempts) >= 2,
        f"programming the sink to answer 500 once produced {len(attempts)} attempt(s) for "
        f"{retried_event!r}, expected at least 2. A 5xx from a subscriber must schedule a retry "
        f"(core/webhooks/dispatcher.py), otherwise the documented retry schedule is decoration.",
    )
    sig_first, sig_retry = _signature(attempts[0], headers), _signature(attempts[1], headers)
    require(
        sig_first == sig_retry,
        f"the retry of one delivery was signed differently from its first attempt ({sig_first} vs "
        f"{sig_retry}). The attempt number is not a signing input: a consumer that deduplicates on "
        f"event id and verifies once would find the redelivery unverifiable.",
    )
    return (
        f"signature headers {headers}; url_bound={url_bound}, secret_bound={secret_bound}, "
        f"body_bound={body_bound} each observed in the declared direction; identical input signed "
        f"identically; {len(attempts)} attempts of one delivery share one signature"
    )


@check(
    id="C16",
    name="webhooks: the core brands no delivery header, and the retry metadata is the vendor's",
    asserts=(
        "The retry schedule is declared by the vendor; a delivery carries content-type from the "
        "core and nothing in the core's own header namespace; retry metadata appears only on a retry."
    ),
    requires=Requires(signer=True, webhooks=True, memory_sink=True),
)
def delivery_headers_are_the_vendors_own(env: CheckEnv) -> str:
    retry = env.info()["webhooks"]["retry"]
    schedule: list[int] = [int(value) for value in retry["schedule_ms"]]
    require(
        schedule,
        "the retry schedule published at /__unit/info is empty. The core ships no retry defaults "
        "on purpose -- 'how often does this vendor retry' is a documented property of the vendor -- "
        "so an empty schedule means the vendor definition supplied none.",
    )
    require(
        all(interval > 0 for interval in schedule),
        f"the retry schedule contains a non-positive interval: {schedule}. Compress time with "
        f"time_scale, which is a knob for tests; a zero interval makes the schedule meaningless.",
    )

    _subscribe(env, "conformance-headers", _URL_A, _SECRET_ONE)
    env.client.call("POST", f"{CONTROL_PREFIX}webhooks/sink", json_body={"statuses": [500], "then": 200})
    event_id = _emit(env, {"probe": "headers"})
    attempts = sorted(_records(env, event_id), key=lambda row: int(row["attempt"]))
    require(
        len(attempts) >= 2,
        f"the forced failure produced {len(attempts)} attempt(s), expected at least 2. Without a "
        f"retry there is no retry metadata to attribute to anybody.",
    )

    first = {str(name).lower(): str(value) for name, value in dict(attempts[0]["headers"]).items()}
    retried = {str(name).lower(): str(value) for name, value in dict(attempts[1]["headers"]).items()}

    branded = sorted(name for name in (*first, *retried) if name.startswith(CORE_HEADER_NAMESPACE))
    require(
        not branded,
        f"delivery headers {branded} are in the core's own namespace {CORE_HEADER_NAMESPACE!r}. The "
        f"core sends a body and a content type; every other header is named by the vendor through "
        f"WebhookSigner.headers(meta), over the neutral DeliveryMetadata in "
        f"core/webhooks/models.py. A core that spelled a vendor's header names would make the next "
        f"vendor inherit this one's wire format.",
    )
    require(
        "content-type" in first,
        "the delivery carries no content-type. That one header is the core's own contribution and "
        "a subscriber cannot parse the body without it.",
    )

    added = sorted(set(retried) - set(first))
    require(
        added,
        f"the retry carried no header the first attempt did not: {sorted(retried)}. Retry metadata "
        f"is retry-only by contract -- a first attempt that already announces a retry number is "
        f"telling a subscriber it is a redelivery when it is not.",
    )
    return (
        f"retry schedule of {len(schedule)} intervals declared by the vendor; "
        f"{len(first)} headers on attempt 1, none in {CORE_HEADER_NAMESPACE!r}; "
        f"retry adds {added}"
    )


# ---------------------------------------------------------------------------
# C18, C21 -- the gate is real, and the schedule is the one followed.
# ---------------------------------------------------------------------------

_WILDCARD = "*"
"""What a subscription registers to hear everything.

Needed because a delivery caused by a *real* vendor mutation carries whatever
event type that vendor's EventMapper chose, and a check that knew the name
would be a check about one vendor.
"""

_GATE_SUBSCRIBER = "conformance-gate"
_SCHEDULE_SUBSCRIBER = "conformance-schedule"
_ALWAYS_FAIL = 500


def _drive_example_mutation(env: CheckEnv, label: str) -> str:
    """Commit one mutation through the vendor's own surface. Returns its key."""
    route = env.first_example_route(methods=frozenset({"POST", "PUT"}))
    body = dict(route.example_body or {})
    spec = route.idempotency
    if spec is not None:
        body[str(spec["key_path"])] = f"conformance-{label}"
    answered = env.client.call(route.method, route.example_path, json_body=body, headers=env.authorized(route))
    require(
        200 <= answered.status < 300,
        f"{route.key} refused its own published example_body: {answered.status} "
        f"{answered.error_kind!r} {answered.text[:200]}.",
    )
    env.client.call("POST", f"{CONTROL_PREFIX}webhooks/drain", json_body={})
    return route.key


@check(
    id="C18",
    name="webhooks: the delivery capability gate is real, at the point of delivery",
    asserts=(
        "With the webhooks capability off, a committed mutation delivers nothing and records "
        "nothing; switching it back on delivers again -- so the gate is evaluated per event and "
        "not once at construction."
    ),
    requires=Requires(webhooks=True, memory_sink=True, signer=True, mutating_example=True, credentials=True),
)
def the_webhooks_capability_gate_is_real(env: CheckEnv) -> str:
    """What C14 does for ``chaos``, for ``webhooks`` and ``webhooks.chaos``.

    The two lines that gate delivery in
    ``core/webhooks/dispatcher.py::attach`` could be deleted outright and the
    whole suite stayed green. C11 *published* this gate -- its ``gated_at`` and
    its ``effect``, "the listener returns at once, so no event is ever mapped,
    prepared or delivered" -- and nothing anywhere checked that the effect
    happened. Worse, the two contracts that touch delivery both declare
    ``requires=webhooks``, so they SKIP on exactly the profiles where an
    ungated dispatcher would be visible.

    The mutation is driven through the vendor's surface rather than through
    ``POST /__unit/webhooks/emit``, and that is load-bearing: the emitter calls
    ``enqueue`` directly, which is *below* the gate, so an emitted event proves
    nothing about it. The gate is on the journal listener, so only a journalled
    mutation asks the question.

    Restoring the capability and delivering again is the second half, and it is
    not decoration: a gate evaluated once at construction would also deliver
    nothing after being switched off, and would deliver nothing after being
    switched back on either. Only the round trip separates "gated per event"
    from "gated per profile".
    """
    gate = CoreCapability.WEBHOOKS.value
    chaos_gate = CoreCapability.WEBHOOKS_CHAOS.value
    original = [row.name for row in env.capabilities() if row.enabled]
    # This contract drives the same mutating route three times, and a profile
    # is entitled to ship a standing rule that refuses every third request --
    # one does. The subject here is the delivery gate, so the fault engine is
    # cleared first rather than the contract being written around whichever
    # rules a profile happens to preload.
    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    _subscribe(env, _GATE_SUBSCRIBER, _URL_A, _SECRET_ONE, event_types=(_WILDCARD,))

    baseline_route = _drive_example_mutation(env, "gate-on")
    delivered_on = len(env.deliveries())
    require(
        delivered_on > 0,
        f"with {gate!r} enabled, a committed mutation through {baseline_route} delivered nothing, "
        f"so switching the capability off could not be observed to change anything and this "
        f"contract would pass vacuously. Either the vendor's EventMapper produces no event for "
        f"this mutation, or the dispatcher's journal listener never attached.",
    )

    try:
        env.set_capabilities([name for name in original if name != gate and not name.startswith(f"{gate}.")])
        require(
            not env.capability_enabled(gate),
            f"capability {gate!r} is still reported enabled after being removed from the enabled "
            f"set; core/capability/registry.py::set_enabled replaces the set outright.",
        )
        require(
            not env.capability_enabled(chaos_gate),
            f"{chaos_gate!r} survived {gate!r} being switched off. A dotted capability is blocked "
            f"by its parent (core/capability/registry.py), and delivery-scope faults on a unit "
            f"that delivers nothing are a contradiction the registry must not permit.",
        )
        _drive_example_mutation(env, "gate-off")
        delivered_off = len(env.deliveries())
        require(
            delivered_off == delivered_on,
            f"with {gate!r} disabled, a committed mutation still produced "
            f"{delivered_off - delivered_on} delivery record(s). The gate is inside the listener "
            f"registered by core/webhooks/dispatcher.py::attach and it must run before the vendor "
            f"is asked to map anything: a gated-off delivery is not merely undelivered, it never "
            f"happened -- nothing mapped, nothing prepared, nothing recorded.",
        )
    finally:
        env.set_capabilities(original)

    _drive_example_mutation(env, "gate-back-on")
    delivered_again = len(env.deliveries())
    require(
        delivered_again > delivered_off,
        f"after {gate!r} was switched back on, a committed mutation still delivered nothing "
        f"({delivered_again} records, unchanged). The gate is being evaluated once, at "
        f"construction, so it answers a question about the PROFILE rather than about the unit's "
        f"current state -- and a consumer who switched delivery off for one scenario can never "
        f"switch it back on.",
    )
    return (
        f"{gate!r} on -> {delivered_on} deliveries; off -> {delivered_off} (unchanged), with "
        f"{chaos_gate!r} blocked by its parent; on again -> {delivered_again}"
    )


@check(
    id="C21",
    name="webhooks: the declared retry schedule is the schedule actually followed",
    asserts=(
        "Under the virtual clock, a subscriber that always fails is retried exactly as many times "
        "as the vendor declares intervals, and each retry happens after the declared interval and "
        "not before."
    ),
    requires=Requires(signer=True, webhooks=True, memory_sink=True, virtual_clock=True),
)
def the_declared_retry_schedule_is_the_one_followed(env: CheckEnv) -> str:
    """Observed attempts and observed delays, against the declared schedule.

    Everything that existed before was declarative: C16 asserted the published
    ``schedule_ms`` was non-empty and positive, and both delivery contracts
    then required ``len(attempts) >= 2``. Eleven declared intervals with one
    retry actually performed passed; so did the whole schedule run *backwards*.
    C09's own prose says that without this "the documented retry schedule is
    decoration", and it was.

    The virtual clock is what makes it askable at all: the delay between
    attempts is crossed on demand instead of waited out, so the contract can
    assert that nothing happened before the declared interval AND that
    something happened at it -- and the first half is the half a test that
    merely waits can never assert.

    Each step advances to one millisecond *short* of the interval, asserts
    nothing moved, then advances the last millisecond. A schedule scaled by
    ``time_scale`` is compared after scaling, because that is the delay the
    dispatcher actually schedules.
    """
    retry = env.info()["webhooks"]["retry"]
    scale = float(retry["time_scale"])
    declared = [int(value) for value in retry["schedule_ms"]]
    require(
        declared,
        "the retry schedule published at /__unit/info is empty, so there is nothing to follow.",
    )
    # floor(x + 0.5), matching core/util/numbers.py::js_round -- replicated
    # rather than imported so the check stays independent of the code it
    # certifies. Python's round() disagrees on exact halves (round(2.5) == 2),
    # which would put this check one millisecond early precisely when
    # interval * scale lands on a half.
    scaled = [max(1, math.floor(interval * scale + 0.5)) for interval in declared]
    if any(interval < 2 for interval in scaled):
        raise ConformanceSkip(
            f"the scaled retry schedule {scaled} contains an interval under 2ms, so 'one "
            f"millisecond before it was due' is not expressible; raise time_scale for this profile"
        )

    _subscribe(env, _SCHEDULE_SUBSCRIBER, _URL_A, _SECRET_ONE)
    programmed = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}webhooks/sink",
        json_body={"statuses": [_ALWAYS_FAIL] * (len(scaled) + 1), "then": _ALWAYS_FAIL},
    )
    require(
        programmed.status == 200,
        f"POST /__unit/webhooks/sink answered {programmed.status}: {programmed.text}.",
    )
    # Emitted WITHOUT a drain: draining a virtual clock runs the whole cascade,
    # which is exactly the observation this contract exists to make one step at
    # a time. `advance(0)` settles the worker without moving time.
    event_id = _emit(env, {"probe": "schedule"}, drain=False)

    def attempts() -> int:
        return len(_records(env, event_id))

    def advance(ms: int) -> None:
        # `drain: false`, and it is the whole reason this contract can exist:
        # a drain on a virtual clock advances to every pending timer in turn,
        # so a single advance would run the entire cascade and there would be
        # no interval left to observe.
        answered = env.client.call("POST", f"{CONTROL_PREFIX}clock/advance", json_body={"ms": ms, "drain": False})
        require(
            answered.status == 200,
            f"POST /__unit/clock/advance answered {answered.status}: {answered.text}. A virtual "
            f"clock is what lets a declared delay be crossed rather than waited out.",
        )

    advance(0)
    require(
        attempts() == 1,
        f"the first delivery produced {attempts()} record(s) before any virtual time passed, "
        f"expected 1. A retry that is submitted to the worker instead of being put on the clock "
        f"(core/webhooks/dispatcher.py::_schedule) makes the whole declared schedule instantaneous, "
        f"which no consumer's backoff test can then observe.",
    )

    early: list[str] = []
    for index, interval in enumerate(scaled):
        advance(interval - 1)
        seen = attempts()
        if seen != index + 1:
            early.append(f"retry {index + 1} fired {seen - index - 1} attempt(s) early, {interval - 1}ms in")
        advance(1)
        due = attempts()
        require(
            due == index + 2,
            f"retry {index + 1} was declared to happen {interval}ms after attempt {index + 1} "
            f"(schedule_ms[{index}]={declared[index]} scaled by {scale}), and after exactly that "
            f"much virtual time there are {due} attempts, expected {index + 2}. The dispatcher "
            f"reads its delay from core/webhooks/retry.py::retry_delay_ms, indexed by the retry "
            f"number: an off-by-one, a reversed schedule or a constant would all land here.",
        )
    require(
        not early,
        "the dispatcher retried BEFORE the interval the vendor declares:\n  "
        + "\n  ".join(early)
        + "\ncore/webhooks/retry.py::retry_delay_ms must return schedule_ms[retry_number] scaled, "
        "and the dispatcher must put the attempt on the clock rather than submitting it.",
    )

    advance(max(scaled) * 2)
    final = _records(env, event_id)
    require(
        len(final) == len(scaled) + 1,
        f"a subscriber that never acknowledges produced {len(final)} attempts against a schedule "
        f"of {len(scaled)} intervals, expected {len(scaled) + 1} (the first send plus one retry "
        f"per interval) even after {max(scaled) * 2}ms more. "
        f"core/webhooks/retry.py::schedule_exhausted is `retry_number >= len(schedule_ms)`; "
        f"anything else makes the published schedule a decoration.",
    )
    require(
        str(final[-1]["status"]) == "exhausted",
        f"the last attempt is recorded as {final[-1]['status']!r}, expected 'exhausted'. A "
        f"consumer reading the delivery log has no other way to tell a subscriber that gave up "
        f"from one still waiting on a retry.",
    )
    return (
        f"{len(scaled)} declared intervals {scaled} (scale {scale}) followed exactly: "
        f"{len(final)} attempts, each retry after its interval and none before, last recorded "
        f"{final[-1]['status']!r}"
    )


# ---------------------------------------------------------------------------
# C29 -- a delivery-scope rule changes what the sink observes.
# ---------------------------------------------------------------------------

_EFFECT_SUBSCRIBER = "conformance-effect"
_EFFECT_RULE = "conformance-effect"
_DELAY_MS = 750
"""Long enough that the pending timer is read before it fires on a real
clock, short enough that draining it afterwards does not cost a profile."""
_DUPLICATE_COPY = "duplicate-copy"
_RELEASED = "released-after-reorder"
"""The two chaos labels the core stamps on the extra copy and on the held
event, in ``core/webhooks/dispatcher.py::_apply_chaos_and_schedule``."""


def _delivered(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in records if row["status"] == "delivered"]


def _observe_duplicate(env: CheckEnv) -> str:
    event = _emit(env, {"probe": "duplicate"})
    records = _records(env, event)
    delivered = _delivered(records)
    require(
        len(delivered) == 2 and all(int(row["attempt"]) == 1 for row in delivered),
        f"webhook.duplicate (default copies=1) produced {len(delivered)} delivered record(s) for "
        f"{event!r} at attempt {[row['attempt'] for row in delivered]}, expected 2 first attempts. The "
        f"copy is built in core/webhooks/dispatcher.py::_apply_chaos_and_schedule; a subscriber "
        f"deduplicating on event id is what this fault exists to exercise.",
    )
    require(
        any(_DUPLICATE_COPY in row.get("chaos", []) for row in delivered),
        f"neither delivered copy of {event!r} is labelled {_DUPLICATE_COPY!r} in its chaos list "
        f"({[row.get('chaos') for row in delivered]}); a consumer reading the log cannot tell the "
        f"copy from the original.",
    )
    return f"duplicate: 2 first attempts, one labelled {_DUPLICATE_COPY!r}"


def _observe_delay(env: CheckEnv) -> str:
    event = _emit(env, {"probe": "delay"}, drain=False)
    before = len(_records(env, event))
    pending = [timer for timer in env.get_json(f"{CONTROL_PREFIX}info")["clock"]["pending_timers"]]
    due = [float(timer["due_in_ms"]) for timer in pending]
    require(
        before == 0 and any(value > 0 for value in due),
        f"webhook.delay (delay_ms={_DELAY_MS}) left {before} record(s) for {event!r} and pending "
        f"timers due in {due}ms before any drain; expected no delivery yet and a timer counting down. "
        f"A delay must put the attempt on the clock (core/webhooks/dispatcher.py::_schedule), not "
        f"submit it and label it delayed.",
    )
    env.client.call("POST", f"{CONTROL_PREFIX}webhooks/drain", json_body={})
    delivered = _delivered(_records(env, event))
    require(
        len(delivered) == 1 and any("webhook.delay" in label for label in delivered[0].get("chaos", [])),
        f"after the drain, {event!r} has {len(delivered)} delivered record(s) with chaos "
        f"{[row.get('chaos') for row in delivered]}; expected one, labelled with the delay rule.",
    )
    return f"delay: nothing delivered while a timer was due in {max(due):.0f}ms, one delivery after the drain"


def _observe_drop_ack(env: CheckEnv) -> str:
    event = _emit(env, {"probe": "drop_ack"})
    attempts = sorted(_records(env, event), key=lambda row: int(row["attempt"]))
    require(
        len(attempts) >= 2,
        f"webhook.drop_ack produced {len(attempts)} attempt(s) for {event!r}, expected at least 2: the "
        f"subscriber's acknowledgement is lost, so the dispatcher must retry.",
    )
    first, second = attempts[0], attempts[1]
    require(
        first["status"] == "failed" and 200 <= int(first["response_status"]) < 300,
        f"the first attempt of {event!r} is recorded as {first['status']!r} with response_status "
        f"{first['response_status']}; expected 'failed' with a 2xx, because the subscriber DID answer "
        f"and the answer was dropped -- that is what makes the fault distinguishable from an outage "
        f"(core/webhooks/dispatcher.py::_run_attempt applies drop_ack after the send).",
    )
    require(
        second["status"] == "delivered",
        f"the retry of {event!r} is recorded as {second['status']!r}, expected 'delivered': the "
        f"dropped acknowledgement applies to one attempt, not to the subscriber.",
    )
    return "drop_ack: attempt 1 failed with a 2xx from the subscriber, attempt 2 delivered"


def _observe_out_of_order(env: CheckEnv) -> str:
    held = _emit(env, {"probe": "held"}, drain=False)
    releaser = _emit(env, {"probe": "releaser"})
    log = list(env.deliveries())
    held_rows = [row for row in log if row["event_id"] == held]
    require(
        any(row["status"] == "skipped" for row in held_rows),
        f"webhook.out_of_order recorded no 'skipped' entry for the held event {held!r} "
        f"({[row['status'] for row in held_rows]}); the hold is recorded so a consumer can see why "
        f"an event arrived late.",
    )
    order = [row["event_id"] for row in _delivered(log) if row["event_id"] in (held, releaser)]
    require(
        order == [releaser, held],
        f"delivery order was {order}; expected the later event {releaser!r} delivered BEFORE the held "
        f"one {held!r}. The one reorder slot is released on the next enqueue, after that event's own "
        f"copies (core/webhooks/dispatcher.py::_apply_chaos_and_schedule).",
    )
    require(
        any(_RELEASED in row.get("chaos", []) for row in held_rows),
        f"the held event's delivery is not labelled {_RELEASED!r} ({[row.get('chaos') for row in held_rows]}).",
    )
    return "out_of_order: the second event was delivered first and the held one after it"


def _observe_drop(env: CheckEnv) -> str:
    event = _emit(env, {"probe": "drop"})
    records = _records(env, event)
    require(
        [row["status"] for row in records] == ["dropped"],
        f"webhook.drop produced {[row['status'] for row in records]} for {event!r}, expected exactly "
        f"one 'dropped' record and no delivery: the subscriber gets nothing and no retry is scheduled.",
    )
    return "drop: one 'dropped' record, nothing delivered"


_CORE_WEBHOOK_FAULTS = frozenset(fault.name for fault in BUILTIN_FAULTS if fault.scope == "webhook")
"""The specification's own delivery-fault vocabulary, so the published list is
checked against the contract rather than against itself."""

_OBSERVATIONS: dict[str, tuple[Callable[[CheckEnv], str], dict[str, Any]]] = {
    "webhook.duplicate": (_observe_duplicate, {}),
    "webhook.delay": (_observe_delay, {"delay_ms": _DELAY_MS}),
    "webhook.drop_ack": (_observe_drop_ack, {}),
    "webhook.out_of_order": (_observe_out_of_order, {}),
    "webhook.drop": (_observe_drop, {}),
}
"""How each delivery-scope fault the core publishes is observed at the sink,
and the parameters its rule carries."""


@check(
    id="C29",
    name="chaos: every delivery-scope fault the unit publishes has its effect at the sink",
    asserts=(
        "For every webhook-scope fault listed at /__unit/chaos: a rule naming it changes the delivery "
        "log the way the fault promises -- a second copy, a timer before delivery, a lost "
        "acknowledgement then a retry, a later event delivered first, or no delivery at all."
    ),
    requires=Requires(signer=True, webhooks=True, memory_sink=True, webhooks_chaos=True),
)
def every_delivery_fault_has_its_effect(env: CheckEnv) -> str:
    """What C14 is for the request scope, one level down.

    Making no webhook-scope rule ever fire -- all four delivery faults dead at
    once -- left the matrix green (konyklabs/roadmap#10, N-7; tracked as
    konyklabs/roadmap#15). C14 proves the request-scope gate, C18 proves the
    delivery gate, and nothing observed a delivery fault at the sink: a
    profile could ship ``reorder-order-updated`` and no consumer's handling of
    reordering would ever have been exercised.

    The fault list is read from the unit rather than written here, so a fault
    the core adds is a fault this contract refuses to leave unobserved: an
    entry with no observation is a failure naming it, not a silent pass.
    """
    published = [str(fault["name"]) for fault in env.info()["chaos"]["faults"] if fault["scope"] == "webhook"]
    require(published, "GET /__unit/info publishes no webhook-scope fault, so there is nothing to observe.")
    # The list is cross-checked against the core's own fault catalogue -- the
    # same shape as C11's core_gates comparison -- because a fault list read
    # from the unit under test is otherwise a list the unit can shorten:
    # publish one fault, implement one fault, and "1 delivery faults
    # observed" reads as a pass. BUILTIN_FAULTS is the specification's
    # vocabulary, imported exactly as CoreCapability is.
    missing = sorted(_CORE_WEBHOOK_FAULTS - set(published))
    require(
        not missing,
        f"GET /__unit/info publishes {sorted(published)} as its webhook-scope faults and the core's "
        f"catalogue (core/chaos/rules.py::BUILTIN_FAULTS) declares {sorted(_CORE_WEBHOOK_FAULTS)}: "
        f"{missing} are missing. A fault dropped from the published list would silently drop out of "
        f"this observation too, so under-declaring is a failure, not a smaller contract.",
    )
    unknown = sorted(set(published) - set(_OBSERVATIONS))
    require(
        not unknown,
        f"the unit publishes webhook-scope fault(s) {unknown} that this contract has no observation for. "
        f"A delivery fault nothing observes is a delivery fault a consumer cannot rely on; add its "
        f"observation to conformance/checks/webhooks.py alongside the others.",
    )

    env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    _subscribe(env, _EFFECT_SUBSCRIBER, _URL_A, _SECRET_ONE)
    observed: list[str] = []
    for fault in published:
        observe, params = _OBSERVATIONS[fault]
        rule: dict[str, Any] = {
            "id": f"{_EFFECT_RULE}-{fault}",
            "scope": "webhook",
            "fault": fault,
            "match": {"event_type": _EVENT_TYPE},
            "when": {"times": 1},
        }
        if params:
            rule["params"] = params
        installed = env.client.call("POST", f"{CONTROL_PREFIX}chaos/rules", json_body={"rules": [rule]})
        require(
            installed.status == 200,
            f"POST /__unit/chaos/rules refused the webhook-scope rule for {fault!r} with "
            f"{installed.status}: {installed.text[:200]}. The {CoreCapability.WEBHOOKS_CHAOS.value!r} "
            f"capability is enabled on this profile, so the rule must be accepted.",
        )
        observed.append(observe(env))
        env.client.call("POST", f"{CONTROL_PREFIX}chaos/reset", json_body={})
    return f"{len(published)} delivery faults observed at the sink -- " + "; ".join(observed)
