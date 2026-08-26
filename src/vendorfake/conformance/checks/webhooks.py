"""C09, C16 -- signing is what the vendor says it is, and delivery is unbranded.

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

from typing import Any

from vendorfake.conformance.env import CONTROL_PREFIX, CheckEnv
from vendorfake.conformance.registry import check
from vendorfake.conformance.types import ConformanceFailure, Requires, require

__all__ = ["delivery_headers_are_the_vendors_own", "signing_matches_the_declared_bindings"]

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


def _subscribe(env: CheckEnv, subscriber_id: str, url: str, secret: str) -> None:
    answered = env.client.call(
        "POST",
        f"{CONTROL_PREFIX}webhooks/subscriptions",
        json_body={
            "id": subscriber_id,
            "notification_url": url,
            "signature_key": secret,
            "event_types": [_EVENT_TYPE],
        },
    )
    require(
        answered.status == 201,
        f"POST /__unit/webhooks/subscriptions answered {answered.status} for {subscriber_id!r}: "
        f"{answered.text}. A consumer must be able to register a receiver without holding a vendor "
        f"credential -- that is what makes 'test my webhook handler' one call.",
    )


def _emit(env: CheckEnv, body: Any) -> str:
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
