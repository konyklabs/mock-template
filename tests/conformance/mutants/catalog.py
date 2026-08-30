"""Forty units, each broken in exactly one way, and the check each must trip.

FOR: proving the conformance suite discriminates. Every contract in
``conformance/manifest.json`` is answered here by at least one unit that
violates it, and the meta-tests in ``tests/conformance/test_mutants.py`` assert
both directions -- the named check goes red, and no unnamed check does.

READING THIS FILE. Each mutant states the defect, its provenance, and the
check ids it must turn red. Two of them are not inventions:

* :data:`PERMISSIVE_SELF_TRANSITION` is the TypeScript reference's
  ``canTransition`` short-circuit, which makes a second payment on a COMPLETED
  order succeed.
* :data:`UNGATED_IN_BAND_CHAOS` is the losing bake-off entry's unconditional
  per-request chaos merge, which injected faults on a unit with fault
  injection switched off, for any caller who knew the field name.

Everything else is labelled ``hypothetical`` and means it: a defect nobody has
shipped in this lineage, written to give a contract something to catch.

M21 through M30 were written against measured holes rather than imagined ones.
Each reproduces a mutation that was applied to this codebase and left the whole
suite green: authentication deleted, a capability gate skipped for one
operation, the journal recording the version it read, the webhooks gate removed
outright, a retry schedule declared and not followed, idempotent replay
disabled, a cursor's query fingerprint ignored, a seed drawn from the process
id, and a vendor whose unit will not start at all.

WHAT IS NOT REACHABLE FROM A PRODUCTION PATH. Nothing in this module is
imported by ``src/vendorfake/``. Every mutation is applied by handing a
deliberately wrong collaborator to a constructor that already takes one, so
the seams are real and the defects are not.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import secrets
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tests.conformance.harness import PROFILES
from tests.conformance.mutants.model import Mutant, Provenance, register
from tests.conformance.mutants.seams import (
    AuthAdapterOverlay,
    ClientOverlay,
    DeafWebhookFaultSelector,
    ErrorShaperOverlay,
    ImpatientWebhookDispatcher,
    LeakyFaultSelector,
    LoopBreakingFaultSelector,
    PermissiveStateMachine,
    SignerOverlay,
    UngatedWebhookDispatcher,
    VendorOverlay,
    carries_magic,
    replace_control_route,
    rewrite_document,
    wrap_vendor_handlers,
)
from vendorfake.conformance.client import FORM_CONTENT_TYPE, ConformanceClient, ConformanceResponse
from vendorfake.core.capability.gates import CoreCapability
from vendorfake.core.capability.registry import CONTROL_CAPABILITY, CapabilityRegistry
from vendorfake.core.chaos.engine import ChaosEngine
from vendorfake.core.chaos.selector import FaultSelector
from vendorfake.core.kernel.types import (
    AuthAdapter,
    AuthResult,
    CapabilityDecl,
    Handler,
    HandlerArgs,
    ReplyInit,
    Route,
    ShapedError,
    Signer,
    SignInput,
    UnitContext,
    UnitError,
    UnitErrorKind,
    UnitResponse,
    VendorDefinition,
)
from vendorfake.core.state.machine import MachineDef
from vendorfake.core.util.json import dump_json
from vendorfake.core.webhooks.dispatcher import WebhookDispatcher
from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.square.retry import RETRY_NUMBER_HEADER, RETRY_REASON_HEADER, RETRY_REASONS
from vendorfake.square.signer import SIGNATURE_HEADER, square_signature
from vendorfake.square.vendor import SQUARE_SCOPES

__all__ = ["PERMISSIVE_SELF_TRANSITION", "UNGATED_IN_BAND_CHAOS"]

_PROBE_CAPABILITY = "merchant-directory"
"""An existing, enabled surface capability the route mutants can hang a route
off. Chosen so that a mutant about *route* wiring does not also become a
mutant about capability declaration."""

_GATED_CAPABILITY = CoreCapability.WEBHOOKS_CHAOS.value
"""The core-gated capability the construction mutant removes.

The leaf of the three, so its removal is a declaration failure and nothing
else: taking away ``webhooks`` would also orphan its child and produce two
problems where the contract is about one.
"""

VIRTUAL_CLOCK_PROFILE = "chaos-demo"
"""The one shipped profile whose clock is virtual.

Named rather than indexed, because the mutant that judges the retry schedule
must run somewhere a declared delay can be *crossed*; on every other profile
the contract's precondition is unmet and the mutant would prove nothing.
"""


# ---------------------------------------------------------------------------
# C01 -- the unit describes itself.
# ---------------------------------------------------------------------------

register(
    Mutant(
        id="M01",
        name="info-drops-a-documented-key",
        defect="GET /__unit/info omits `clock`, so a consumer cannot reproduce the run's time base.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C01"}),
        control=replace_control_route(
            "GET",
            "/__unit/info",
            # `clock` and not one of the other six deliberately: it is the only
            # documented key no other check reads, so this mutant measures C01
            # rather than measuring the checks that aim themselves with /info.
            rewrite_document(lambda document: {k: v for k, v in document.items() if k != "clock"}),
        ),
    )
)


# ---------------------------------------------------------------------------
# C02 -- routes and capabilities describe one unit.
# ---------------------------------------------------------------------------


def _orphan_route(args: HandlerArgs) -> ReplyInit:
    return ReplyInit(json={"orphan": True})


def _add_orphan(routes: Sequence[Route]) -> Sequence[Route]:
    return (
        *routes,
        Route(
            method="GET",
            path="/v2/conformance-orphan",
            capability="conformance-undeclared",
            handler=_orphan_route,
            operation_id="ConformanceOrphan",
            summary="A route whose capability the vendor never declares.",
        ),
    )


register(
    Mutant(
        id="M02",
        name="orphan-route",
        defect="A vendor route names a capability that appears in no CapabilityDecl, so it can never be enabled.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C02"}),
        also_trips=frozenset({"C03"}),
        cascade=(
            "An undeclared capability is one no profile can switch off, so the orphan route is also "
            "a route no capability gates -- which is exactly what C03's completeness clause now "
            "asserts against. The two findings are the same defect seen from the route table and "
            "from the gate, and suppressing either would mean C03 passing over a live, ungateable "
            "endpoint."
        ),
        vendor=lambda inner: VendorOverlay(inner, routes=_add_orphan),
    )
)


def _add_unused_surface(decls: Sequence[CapabilityDecl]) -> Sequence[CapabilityDecl]:
    return (
        *decls,
        CapabilityDecl(
            name="conformance-unused-surface",
            summary="Declared as surface, owns no route; switching it off would change nothing.",
        ),
    )


register(
    Mutant(
        id="M03",
        name="unused-surface-capability",
        defect="A capability is declared kind='surface' and owns no route, so it has no observable meaning.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C02"}),
        vendor=lambda inner: VendorOverlay(inner, capabilities=_add_unused_surface),
    )
)


# ---------------------------------------------------------------------------
# C03 -- a disabled capability answers explicitly.
# ---------------------------------------------------------------------------

register(
    Mutant(
        id="M04",
        name="capability-gate-shaped-as-not-found",
        defect="The vendor shapes capability_disabled as a 404, so a switched-off profile reads as a typo.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C03"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            errors=ErrorShaperOverlay(
                inner.errors,
                overrides={
                    # Status only. The kind header is stamped by the kernel from
                    # the core error, so this mutant is precisely "the shaper
                    # chose the wrong status" and not "the unit lied about why".
                    UnitErrorKind.CAPABILITY_DISABLED: ShapedError(
                        status=404,
                        body={"errors": [{"category": "INVALID_REQUEST_ERROR", "code": "NOT_FOUND"}]},
                    )
                },
            ),
        ),
    )
)


# ---------------------------------------------------------------------------
# C04, C05 -- no consumer ever meets anything but the vendor's own error.
# ---------------------------------------------------------------------------

register(
    Mutant(
        id="M05",
        name="not-found-is-the-frameworks-envelope",
        defect="ErrorShaper.not_found returns the web framework's {'detail': ...} document.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C04"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            errors=ErrorShaperOverlay(
                inner.errors,
                not_found=ShapedError(status=404, body={"detail": "Not Found"}),
            ),
        ),
    )
)

register(
    Mutant(
        id="M06",
        name="shaper-hole",
        defect="One of the twenty core error kinds is shaped as a 200 with an empty body.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C05"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            errors=ErrorShaperOverlay(
                inner.errors,
                # invalid_cursor because no other contract raises it: a shaper
                # hole in, say, unauthorized would break half the suite and
                # prove nothing about C05.
                overrides={UnitErrorKind.INVALID_CURSOR: ShapedError(status=200, body={})},
            ),
        ),
    )
)


# ---------------------------------------------------------------------------
# C06, C07 -- state is reproducible and append-only.
# ---------------------------------------------------------------------------

_MUTANT_COLLECTION = "conformance_mutant"


def _hydrate_with_a_random_entity(inner: VendorDefinition, ctx: UnitContext, seed: object) -> None:
    inner.hydrate(ctx, seed)
    # The classic: an id drawn from the system's entropy rather than from the
    # unit's seeded stream. Two units hydrated from one document now hold
    # different entities and digest differently.
    token = secrets.token_hex(8)
    ctx.store.collection(_MUTANT_COLLECTION).insert({"id": f"mutant-{token}", "value": token})


register(
    Mutant(
        id="M07",
        name="nondeterministic-seed",
        defect="hydrate() mints an id from the system entropy, so two units seeded alike hold different state.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C06"}),
        vendor=lambda inner: VendorOverlay(inner, hydrate=_hydrate_with_a_random_entity),
    )
)


def _hydrate_with_a_version_regression(inner: VendorDefinition, ctx: UnitContext, seed: object) -> None:
    inner.hydrate(ctx, seed)
    # Writing into the journal past the collection API -- which is what the
    # store's own docstring warns a vendor not to do -- with the version it
    # read rather than the version it wrote.
    for from_version, to_version, op in ((None, 2, "insert"), (2, 1, "update")):
        ctx.store.append_journal(
            collection=_MUTANT_COLLECTION,
            entity_id="regressing-entity",
            op=op,  # type: ignore[arg-type]
            from_version=from_version,
            to_version=to_version,
            changed=("value",),
        )


register(
    Mutant(
        id="M08",
        name="version-regression",
        defect="A journalled mutation lands on a version lower than the one already recorded for that entity.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C07"}),
        vendor=lambda inner: VendorOverlay(inner, hydrate=_hydrate_with_a_version_regression),
    )
)


# ---------------------------------------------------------------------------
# C08 -- identical rules and identical traffic produce identical outcomes.
# ---------------------------------------------------------------------------

_DRIFT = itertools.count()
"""Process-global, and that is the defect: a handler whose answer depends on
something outside the scenario. Deliberately a counter rather than a random
draw, so the mutant trips every time instead of almost every time."""


def _drifting(args: HandlerArgs) -> ReplyInit:
    # Parse first, so that a malformed JSON body still fails as invalid_json
    # and this mutant does not accidentally also become a C04 mutant.
    args.body()
    return ReplyInit(status=200 + next(_DRIFT) % 2, json={"drift": True})


def _add_drifting_route(routes: Sequence[Route]) -> Sequence[Route]:
    # Prepended: the checks that probe "the first vendor route" must land on it,
    # otherwise the mutant is a route nothing exercises.
    return (
        Route(
            method="GET",
            path="/v2/conformance-drift",
            capability=_PROBE_CAPABILITY,
            handler=_drifting,
            operation_id="ConformanceDrift",
            summary="Answers with a status drawn from outside the scenario.",
        ),
        *routes,
    )


register(
    Mutant(
        id="M09",
        name="response-drawn-from-outside-the-scenario",
        defect="A handler's status depends on process-global state, so two units given identical traffic diverge.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C08"}),
        vendor=lambda inner: VendorOverlay(inner, routes=_add_drifting_route),
    )
)


# ---------------------------------------------------------------------------
# C09 -- signing is deterministic and matches the declared bindings.
# ---------------------------------------------------------------------------


def _static_signature(inner: Signer, payload: SignInput) -> Mapping[str, str]:
    return {SIGNATURE_HEADER: "c29tZXRoaW5nLXN0YXRpYy1hbmQtd3Jvbmc="}


register(
    Mutant(
        id="M17",
        name="static-signature-declaring-three-bindings",
        defect="The signer emits a constant header while SignerProperties declares url, secret and body bindings.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C09"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            signer=None if inner.signer is None else SignerOverlay(inner.signer, sign=_static_signature),
        ),
    )
)


def _signature_ignoring_the_body(inner: Signer, payload: SignInput) -> Mapping[str, str]:
    return {SIGNATURE_HEADER: square_signature(payload.secret, payload.notification_url, b"")}


register(
    Mutant(
        id="M18",
        name="signer-not-bound-to-body",
        defect="The signature covers the URL and the secret but not the body, while declaring body_bound=True.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C09"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            signer=None if inner.signer is None else SignerOverlay(inner.signer, sign=_signature_ignoring_the_body),
        ),
    )
)
"""The mutant three subscriptions cannot catch.

C09 takes a fourth observation -- the same subscriber signing a second,
different body -- for exactly this unit. Vary only the URL and the secret and
this signer answers correctly in both directions and ships unverifiable
webhooks.
"""


# ---------------------------------------------------------------------------
# C10, C15, C23 -- the transport carries bytes, content types and query strings, and changes none.
# ---------------------------------------------------------------------------


def _reserialising_binding(transport: str, client: ConformanceClient) -> ConformanceClient:
    if transport != "http":
        return client

    def on_response(method: str, path: str, answered: ConformanceResponse) -> ConformanceResponse:
        import json

        try:
            document = json.loads(answered.body)
        except ValueError:
            return answered
        # JSONResponse(parsed): the same document, different bytes. Key order
        # survives; separators do not -- and a webhook signature covers bytes.
        return ConformanceResponse(
            status=answered.status,
            headers=answered.headers,
            body=json.dumps(document, separators=(", ", ": ")).encode("utf-8"),
        )

    return ClientOverlay(client, on_response=on_response)


register(
    Mutant(
        id="M10",
        name="re-serialising-transport-adapter",
        defect="The HTTP binding re-encodes the unit's JSON instead of returning its bytes.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C10"}),
        transports=("inprocess", "http"),
        client=_reserialising_binding,
    )
)


def _form_eating_binding(transport: str, client: ConformanceClient) -> ConformanceClient:
    def on_request(method: str, path: str, call: dict[str, Any]) -> dict[str, Any]:
        if call.get("form") is None:
            return call
        # An adapter that consumed the stream to parse the form itself and left
        # the core an empty body -- the exact shape that broke two of three
        # prior implementations.
        headers = dict(call.get("headers") or {})
        headers["content-type"] = FORM_CONTENT_TYPE
        return {**call, "form": None, "body": b"", "headers": headers}

    return ClientOverlay(client, on_request=on_request)


register(
    Mutant(
        id="M15",
        name="form-body-arrives-empty",
        defect="A urlencoded body is consumed by the binding, so the handler sees the content type and no fields.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C15"}),
        client=_form_eating_binding,
    )
)


def _query_collapsing_binding(transport: str, client: ConformanceClient) -> ConformanceClient:
    def on_request(method: str, path: str, call: dict[str, Any]) -> dict[str, Any]:
        query = call.get("query")
        if query is None or isinstance(query, Mapping):
            return call
        # `dict(request.query_params)`: the adapter this codebase shipped with
        # until konyklabs/roadmap#37, which kept one value per key and threw
        # the rest away before the core could see them.
        return {**call, "query": dict(query)}

    return ClientOverlay(client, on_request=on_request)


register(
    Mutant(
        id="M31",
        name="repeated-query-key-collapses",
        defect="The binding builds a dict from the query pairs, so a repeated key reaches the handler with its last value only.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C23"}),
        client=_query_collapsing_binding,
    )
)


# ---------------------------------------------------------------------------
# C11 -- every core-gated capability is declared or explicitly excused.
# ---------------------------------------------------------------------------


def _drop_a_gated_declaration(document: dict[str, Any]) -> dict[str, Any]:
    return {
        **document,
        "capabilities": [row for row in document["capabilities"] if row["name"] != "webhooks.chaos"],
    }


register(
    Mutant(
        id="M11",
        name="undeclared-core-gated-capability",
        defect="A capability the core gates on is neither declared nor excused, so its behaviour is silently off.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C11"}),
        control=replace_control_route("GET", "/__unit/capabilities", rewrite_document(_drop_a_gated_declaration)),
    )
)


# ---------------------------------------------------------------------------
# C12, C14 -- fault injection is leak-proof and gated.
# ---------------------------------------------------------------------------


def _leaky_selector(engine: ChaosEngine, capabilities: CapabilityRegistry) -> FaultSelector:
    return LeakyFaultSelector(engine, capabilities)


register(
    Mutant(
        id="M12",
        name="leaky-one-shot",
        defect="An in-band fire advances the standing rules' counters, so 'the second call fails' becomes the first.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C12"}),
        selector=_leaky_selector,
    )
)


def _ungated_in_band(handler: Handler) -> Handler:
    def wrapped(args: HandlerArgs) -> ReplyInit | UnitResponse:
        # The losing entry's defect, transplanted: the request is scanned for
        # the trigger and the fault is armed with no capability consulted
        # anywhere on the path.
        if carries_magic(args):
            raise UnitError(
                UnitErrorKind.RATE_LIMITED,
                detail="Injected by an in-band trigger that consulted no capability.",
            )
        return handler(args)

    return wrapped


UNGATED_IN_BAND_CHAOS = register(
    Mutant(
        id="M14",
        name="ungated-in-band-chaos",
        defect="The in-band trigger is honoured before any capability check, so a unit with chaos off still injects.",
        provenance=Provenance.LOSING_ENTRY,
        trips=frozenset({"C14"}),
        vendor=lambda inner: VendorOverlay(inner, routes=wrap_vendor_handlers(_ungated_in_band)),
    )
)
"""Verbatim prior art.

The losing bake-off entry read a per-request chaos field and merged it over the
engine's configuration unconditionally: correct about one-shot semantics --
nothing global was mutated -- and wrong about the thing that mattered, because
no capability was consulted anywhere on that path. A unit with fault injection
switched off still injected faults for any caller who knew the field name.
"""


# ---------------------------------------------------------------------------
# C13 -- self-transitions are illegal unless declared.
# ---------------------------------------------------------------------------


def _permissive_probe(handler: Handler) -> Handler:
    def wrapped(args: HandlerArgs) -> ReplyInit | UnitResponse:
        body = args.body()
        name = body.get("machine")
        from_state = body.get("from")
        to_state = body.get("to")
        declared: Mapping[str, MachineDef] = args.ctx.vendor.machines
        definition = declared.get(name) if isinstance(name, str) else None
        if definition is None or not isinstance(from_state, str) or not isinstance(to_state, str):
            # The mutant is about the transition predicate and about nothing
            # else: the mutability probe and every malformed request are
            # answered by the real route.
            return handler(args)
        machine = PermissiveStateMachine(definition)
        machine.assert_transition(from_state, to_state, f"machine {name!r}")
        return ReplyInit(
            json={
                "ok": True,
                "machine": name,
                "from": from_state,
                "to": to_state,
                "terminal": machine.is_terminal(from_state),
            }
        )

    return wrapped


PERMISSIVE_SELF_TRANSITION = register(
    Mutant(
        id="M13",
        name="permissive-self-transition",
        defect="can_transition returns True whenever from == to, so a COMPLETED order can be paid a second time.",
        provenance=Provenance.REFERENCE,
        trips=frozenset({"C13"}),
        control=replace_control_route("POST", "/__unit/machines/probe", _permissive_probe),
    )
)
"""Verbatim prior art.

``packages/core/src/state/machine.ts`` in the TypeScript reference opens both
``canTransition`` and ``assertTransition`` with a ``from === to`` short-circuit.
Confirmed by probe against that codebase: paying an order already in COMPLETED
returns 200, replaces the tenders and bumps the version -- the double payment
the lifecycle existed to prevent.

The substitution is made where the control plane constructs its
``StateMachine``, because the probe route is the whole of what C13 can see: a
predicate defect that never reached the control plane would be invisible to any
language-independent check, which is the point of the route existing.
"""


# ---------------------------------------------------------------------------
# C16 -- the core brands no delivery header, and retry metadata is retry-only.
# ---------------------------------------------------------------------------


def _retry_metadata_on_every_attempt(inner: Signer, meta: DeliveryMetadata) -> Mapping[str, str]:
    reason = meta.retry_reason
    return {
        **inner.headers(meta),
        RETRY_NUMBER_HEADER: str(meta.retry_number),
        RETRY_REASON_HEADER: RETRY_REASONS[reason] if reason is not None else next(iter(RETRY_REASONS.values())),
    }


register(
    Mutant(
        id="M16",
        name="retry-metadata-on-every-attempt",
        defect="Retry headers are sent on the first attempt too, so a subscriber cannot tell a redelivery apart.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C16"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            signer=None
            if inner.signer is None
            else SignerOverlay(inner.signer, headers=_retry_metadata_on_every_attempt),
        ),
    )
)


def _core_branded_delivery_header(inner: Signer, meta: DeliveryMetadata) -> Mapping[str, str]:
    return {**inner.headers(meta), "x-unit-delivery-attempt": str(meta.attempt)}


register(
    Mutant(
        id="M19",
        name="core-branded-delivery-header",
        defect="A delivery carries a header in the core's own x-unit- namespace, branding an outbound webhook.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C16"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            signer=None if inner.signer is None else SignerOverlay(inner.signer, headers=_core_branded_delivery_header),
        ),
    )
)


# ---------------------------------------------------------------------------
# The skip path: a contract that is never asked anywhere.
# ---------------------------------------------------------------------------

register(
    Mutant(
        id="M20",
        name="contract-skipped-on-every-profile",
        defect="The vendor declares no state machines, so C13's precondition fails on every profile and it is "
        "never asked at all.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset(),
        skips_everywhere=frozenset({"C13"}),
        profiles=PROFILES,
        # Both bindings, so that C13 is the ONLY contract left having passed
        # nowhere. With one binding C10's precondition would be unmet too, and
        # the anti-vacuity rule would be firing on two things at once -- which
        # is exactly the ambiguity this mutant exists to remove.
        transports=("inprocess", "http"),
        # And an out-of-process transport, for the same reason and no other:
        # C22 needs one, and a target that offered none would leave C22 having
        # passed nowhere too. This mutant is the one place where "passed
        # nowhere" must mean exactly one contract.
        out_of_process=("subprocess",),
        vendor=lambda inner: VendorOverlay(inner, machines={}),
    )
)
"""The mutant that must NOT be caught by a failing check.

A contract whose precondition is unmet everywhere never runs, so no check goes
red and a naive suite reports the emptiest possible matrix as its cleanest. The
report's anti-vacuity rule is what catches it: ``report.ok`` is False when any
check passed on no profile at all. This mutant is how that rule is exercised,
and it is the reason the rule is asserted at the report level rather than being
inferred from a count of failures.
"""


# ---------------------------------------------------------------------------
# C01, C04 -- the framework tripwire is a measurement, not a constant.
# ---------------------------------------------------------------------------


def _framework_answered_a_request(document: dict[str, Any]) -> dict[str, Any]:
    return {**document, "framework_answered": 1}


register(
    Mutant(
        id="M21",
        name="the-framework-answered-a-consumer",
        defect="GET /__unit/health reports framework_answered=1: some request was answered above the unit.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C01"}),
        also_trips=frozenset({"C04"}),
        cascade=(
            "C01 and C04 assert the same number for two different reasons -- C01 that a quiet unit "
            "reports zero, C04 that three deliberately wrong requests still report zero -- so a "
            "non-zero counter is a genuine violation of both. That both were previously satisfied "
            "by a literal 0 is the defect this mutant exists alongside: tests/conformance/"
            "harness.py now builds the served unit with framework_answered=tripwire.get and hands "
            "create_app the same tripwire, so the number is measured rather than assumed."
        ),
        control=replace_control_route("GET", "/__unit/health", rewrite_document(_framework_answered_a_request)),
    )
)
"""Why this one is a document mutant, and why that is the right shape.

The counter is incremented by the ASGI application and read from the unit; over
the in-process transport there is no framework at all, so a *behavioural*
mutant here would be a mutant of the web framework rather than of the unit. The
wiring that makes the number real is pinned separately, by
``tests/conformance/test_harness_wiring.py``, which drives a verb the catch-all
does not claim and watches the number the unit reports move. This mutant pins
the other half: that a non-zero value is not tolerated by either contract.
"""


# ---------------------------------------------------------------------------
# C04 -- a malformed body must not meet a validation library's envelope.
# ---------------------------------------------------------------------------

register(
    Mutant(
        id="M22",
        name="malformed-body-shaped-as-422",
        defect="The vendor shapes invalid_json as HTTP 422, the validation-library envelope a consumer must never meet.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C04"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            errors=ErrorShaperOverlay(
                inner.errors,
                overrides={
                    UnitErrorKind.INVALID_JSON: ShapedError(
                        status=422,
                        body={"detail": [{"loc": ["body", 0], "msg": "Expecting value", "type": "value_error"}]},
                    )
                },
            ),
        ),
    )
)
"""The mutant a probe aimed at the wrong route could not catch.

C04 used to send its malformed body to ``env.first_vendor_route()``, which on
this vendor is ``GET /oauth2/authorize`` -- a route with no body at all, whose
own evidence line read ``malformed body -> 400:missing_field``. Under this
mutant every route that genuinely parses a body answered 422 and the suite
stayed green. C05 does not catch it either: 422 is a 4xx with a non-empty body,
which is all the error *table* is asked for.
"""


# ---------------------------------------------------------------------------
# C03 -- no vendor route escapes the capability gate.
# ---------------------------------------------------------------------------


def _ungated_vendor_route(args: HandlerArgs) -> ReplyInit:
    return ReplyInit(json={"reachable": True})


def _add_route_owned_by_the_control_capability(routes: Sequence[Route]) -> Sequence[Route]:
    return (
        *routes,
        Route(
            method="GET",
            path="/v2/conformance-ungated",
            # The control plane's own capability: always enabled, filtered out
            # of every capability listing, and therefore invisible to a loop
            # over declared capabilities. A copy-paste away from the real thing.
            capability=CONTROL_CAPABILITY,
            handler=_ungated_vendor_route,
            operation_id="ConformanceUngated",
            summary="A vendor route no profile can switch off.",
        ),
    )


register(
    Mutant(
        id="M23",
        name="vendor-route-outside-every-capability",
        defect="A vendor route names the control capability, so it is live on every profile and no gate applies.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C03"}),
        vendor=lambda inner: VendorOverlay(inner, routes=_add_route_owned_by_the_control_capability),
    )
)
"""The hole per-capability iteration structurally cannot see.

C03 iterates capabilities and probes the routes each one owns; a route owned by
the one capability that is never listed belongs to no iteration at all. C02
tolerates it by design -- a route naming the control capability is not an
orphan -- so the route table and the capability table stay consistent while an
endpoint sits outside every profile's reach. Only the complementary assertion
catches it: every vendor route must have been probed by *some* capability.
"""


# ---------------------------------------------------------------------------
# C17 -- the unit actually authenticates somebody.
# ---------------------------------------------------------------------------


def _accepts_anything(inner: AuthAdapter, args: HandlerArgs, mode: str) -> AuthResult:
    # Every scope, for any caller, with or without a credential. This is what
    # `if False:` around step 5 of the pipeline looks like from the outside,
    # expressed through the seam a vendor author would actually get wrong.
    return AuthResult(principal_id="anyone", scopes=SQUARE_SCOPES, meta={"mode": mode})


register(
    Mutant(
        id="M24",
        name="auth-adapter-accepts-anyone",
        defect="The auth adapter resolves any request -- credential or none -- to a principal holding every scope.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C17"}),
        vendor=lambda inner: VendorOverlay(inner, auth=AuthAdapterOverlay(inner.auth, resolve=_accepts_anything)),
    )
)
"""The unit the suite certified before C17 existed.

Replacing the whole authentication step in ``core/kernel/unit.py`` with ``if
False:`` left all sixteen contracts green: ``unauthorized`` and
``forbidden_scope`` appeared only as rows of the error table read from
``GET /__unit/errors``, never as behaviour. This mutant is that unit, reached
through the vendor's own adapter, and it violates all three of C17's clauses at
once -- anonymous accepted, invented credential accepted, under-scoped
credential accepted.
"""


# ---------------------------------------------------------------------------
# C18 -- the delivery capability gate is real.
# ---------------------------------------------------------------------------


def _ungated_dispatcher(**kwargs: Any) -> WebhookDispatcher:
    return UngatedWebhookDispatcher(**kwargs)


register(
    Mutant(
        id="M25",
        name="delivery-gate-consults-no-capability",
        defect="The dispatcher's journal listener never asks whether the webhooks capability is enabled.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C18"}),
        dispatcher=_ungated_dispatcher,
    )
)
"""Two deleted lines that nothing noticed.

C11 publishes this gate with its ``gated_at`` and its ``effect`` -- "the
listener returns at once, so no event is ever mapped, prepared or delivered" --
and until C18 there was no equivalent of C14 asserting that the effect happens.
The two contracts that touch delivery both declare ``requires=webhooks``, so
they skip on precisely the profiles where an ungated dispatcher would show.
"""


# ---------------------------------------------------------------------------
# C19 -- a replayed idempotency key runs nothing.
# ---------------------------------------------------------------------------


def _lie_about_the_idempotency_key(document: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in document["routes"]:
        spec = row.get("idempotency")
        if spec is None:
            rows.append(row)
            continue
        rows.append({**row, "idempotency": {**spec, "key_path": f"{spec['key_path']}_v2"}})
    return {**document, "routes": rows}


register(
    Mutant(
        id="M26",
        name="published-idempotency-key-is-not-the-one-used",
        defect="GET /__unit/routes names an idempotency key_path the route does not deduplicate on.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C19"}),
        control=replace_control_route("GET", "/__unit/routes", rewrite_document(_lie_about_the_idempotency_key)),
    )
)
"""A published contract that is not the behaviour.

The route table is what a consumer builds against, so a ``key_path`` there that
the kernel does not read means every retry a consumer sends under the
documented field is a fresh execution -- a second order per dropped
acknowledgement, with a 200 each time. Observationally identical, from outside,
to the lookup at step 7 of ``core/kernel/unit.py::_run_pipeline`` being
disabled, which is the defect that left the suite green; both are caught by the
same two observations, the stored bytes and the unmoved journal.
"""


# ---------------------------------------------------------------------------
# C20 -- a cursor belongs to the query that issued it.
# ---------------------------------------------------------------------------


def _page_without_a_fingerprint(handler: Handler) -> Handler:
    def wrapped(args: HandlerArgs) -> ReplyInit | UnitResponse:
        body = args.body()
        if not isinstance(body, Mapping) or not isinstance(body.get("collection"), str):
            return handler(args)
        collection = args.ctx.store.collection(str(body["collection"]))
        limit = body.get("limit")
        cursor = body.get("cursor")
        # The call site forgets `fingerprint=`, which the store treats as the
        # query being `None` every time -- so every cursor matches every query.
        # One missing keyword argument, and the rule the store implements is
        # silently unreachable from this endpoint.
        page = collection.paginate(
            collection.all(),
            limit=limit if isinstance(limit, int) else None,
            cursor=cursor if isinstance(cursor, str) else None,
        )
        return ReplyInit(
            json={
                "collection": body["collection"],
                "count": len(page.items),
                "ids": [str(item.get("id", "")) for item in page.items],
                "cursor": page.cursor,
            }
        )

    return wrapped


register(
    Mutant(
        id="M27",
        name="cursor-issued-without-a-query-fingerprint",
        defect="A paginating call site omits the query fingerprint, so a cursor from one query pages another.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C20"}),
        control=replace_control_route("POST", "/__unit/state/page", _page_without_a_fingerprint),
    )
)
"""A wrong answer with no error attached.

``if decoded.q != fp`` in ``core/state/store.py`` could be replaced with ``if
False`` and nothing went red. The consequence is the worst kind: a consumer
changes a filter, keeps paging, and receives rows from the previous query with
a 200 and no indication anywhere that the page does not belong to the question
they asked.
"""


# ---------------------------------------------------------------------------
# C21 -- the declared retry schedule is the one followed.
# ---------------------------------------------------------------------------


def _impatient_dispatcher(**kwargs: Any) -> WebhookDispatcher:
    return ImpatientWebhookDispatcher(**kwargs)


register(
    Mutant(
        id="M28",
        name="retry-schedule-is-decoration",
        defect="Retries are submitted immediately instead of being put on the clock, so every declared interval is 0.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C21"}),
        # The one profile that runs a virtual clock, which is what makes a
        # declared interval crossable rather than waitable. On any other
        # profile C21's precondition is unmet and this mutant would prove
        # nothing at all.
        profiles=(VIRTUAL_CLOCK_PROFILE,),
        dispatcher=_impatient_dispatcher,
    )
)
"""The schedule still declared, published and exhausted -- and never waited for.

C16 asserted the published ``schedule_ms`` was non-empty and positive; C09 and
C16 then required ``len(attempts) >= 2``. Eleven declared intervals with the
whole cascade running in one millisecond satisfies every one of those, and a
consumer's backoff test written against this fake would pass without the
backoff ever being exercised.
"""


# ---------------------------------------------------------------------------
# C22 -- determinism across processes.
# ---------------------------------------------------------------------------


def _hydrate_with_a_per_process_entity(inner: VendorDefinition, ctx: UnitContext, seed: object) -> None:
    inner.hydrate(ctx, seed)
    # Constant within one interpreter and different in the next. Two units
    # built here agree exactly, which is why C06 stays green and why this
    # mutant needs a unit built somewhere else to be caught at all.
    ctx.store.collection(_MUTANT_COLLECTION).insert({"id": f"per-process-{os.getpid()}", "pid": os.getpid()})


register(
    Mutant(
        id="M29",
        name="per-process-seed",
        defect="hydrate() mints an id from the process id: constant within one interpreter, different in the next.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C22"}),
        out_of_process=("subprocess",),
        vendor=lambda inner: VendorOverlay(inner, hydrate=_hydrate_with_a_per_process_entity),
    )
)
"""Invisible to C06 by construction, which is the finding.

C06 builds its second unit with ``target.open_client``, and every transport the
harness offered built it in this interpreter -- ``uvicorn`` on a background
thread is a second *binding*, not a second process. So both of C06's units read
the same pid, digest identically, and C06 reports determinism. The claim C06
makes is about runs, and runs are processes: only a unit built by
``tests/conformance/unit_child.py`` can falsify it.
"""


# ---------------------------------------------------------------------------
# The unit that will not start at all.
# ---------------------------------------------------------------------------


def _drop_a_core_gated_declaration(decls: Sequence[CapabilityDecl]) -> Sequence[CapabilityDecl]:
    return tuple(decl for decl in decls if decl.name != _GATED_CAPABILITY)


register(
    Mutant(
        id="M30",
        name="unit-refuses-to-start",
        defect=(
            "The vendor neither declares nor excuses a core-gated capability, so the startup "
            "assertion refuses to construct the unit."
        ),
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset(),
        fails_to_construct=True,
        vendor=lambda inner: VendorOverlay(inner, capabilities=_drop_a_core_gated_declaration),
    )
)
"""The mutant that must NOT be reported as a failing contract.

Removing this declaration used to print ``[FAIL] C11`` -- which reads exactly
like C11 having been asked and having found the declaration missing, and which
every other contract printed at the same moment for the same reason. C11's body
never ran. A unit that constructs always passes C11 and a unit that does not
fails everything, so the line said nothing about C11 at all.

The FAIL/ERROR split is what this holds down: every case here must be ERROR,
none may be FAIL, and the report must be red. What C11 discriminates is a
*document* -- which is honest, and is why C11's own prose now says so: its value
is against a foreign implementation reached over ``--base-url``, which has no
Python startup assertion in front of it.
"""


# ---------------------------------------------------------------------------
# konyklabs/roadmap#15 -- the coverage the third adversarial round found
# missing. Each of M32 through M40 reproduces a mutation that was applied to
# this codebase after the first remediation and left the whole conformance
# matrix green (konyklabs/roadmap#10, findings N-3a..f, N-5, N-6, N-7).
# ---------------------------------------------------------------------------

_UNAUTHENTICATED_OPERATION = "ListLocations"
"""The one route M32 stops authenticating. Chosen because it is NOT the route
C17 used to probe: the finding was that C17 asked its question of one route
out of sixteen, and a mutant on that one route would not have shown it."""


def _skips_auth_on_one_route(inner: AuthAdapter, args: HandlerArgs, mode: str) -> AuthResult:
    if args.route.operation_id == _UNAUTHENTICATED_OPERATION:
        return AuthResult(principal_id="anyone", scopes=SQUARE_SCOPES, meta={"mode": mode})
    return inner.resolve(args, mode)


register(
    Mutant(
        id="M32",
        name="auth-skipped-for-one-route",
        defect="One route that declares auth resolves any caller, credential or none, to a principal holding every scope.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C17"}),
        vendor=lambda inner: VendorOverlay(
            inner, auth=AuthAdapterOverlay(inner.auth, resolve=_skips_auth_on_one_route)
        ),
    )
)
"""N-3a. C17 probed ``POST /v2/orders`` and only that; a unit that served every
seeded location to an anonymous caller was certified conformant because the
route it stopped authenticating was not the one asked. The fix is the C03
treatment: every route that declares ``auth`` is asked all four questions."""


def _first_scoped_route(routes: Sequence[Route]) -> str | None:
    """The route the single-instance C17 chose: the first that declares scopes."""
    return next((route.key for route in routes if route.auth is not None and route.scopes), None)


def _scope_enforced_on_one_route_only(spared: str | None) -> Callable[[AuthAdapter, HandlerArgs, str], AuthResult]:
    def resolve(inner: AuthAdapter, args: HandlerArgs, mode: str) -> AuthResult:
        result = inner.resolve(args, mode)
        if args.route.key == spared:
            return result
        # The kernel checks Route.scopes against what comes back, so an
        # adapter that grants every scope to any authenticated caller has
        # deleted scope enforcement without touching a route declaration.
        return AuthResult(principal_id=result.principal_id, scopes=SQUARE_SCOPES, meta=result.meta)

    return resolve


register(
    Mutant(
        id="M33",
        name="scope-enforced-on-one-route-only",
        defect="Every authenticated caller is granted every scope, on every route but the first one that declares scopes.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C17"}),
        vendor=lambda inner: VendorOverlay(
            inner,
            auth=AuthAdapterOverlay(
                inner.auth, resolve=_scope_enforced_on_one_route_only(_first_scoped_route(inner.routes))
            ),
        ),
    )
)
"""N-3b. The complement of M32, and the one that says why C17 has to iterate:
scope enforcement removed from every route EXCEPT the one C17 probed left the
matrix green, because a check that asks one route can be satisfied by a unit
that is correct on exactly that route."""


_REPLAY_MARKER = "x-unit-idempotent-replay"


def _stamp_replay_marker(handler: Handler) -> Handler:
    def wrapped(args: HandlerArgs) -> ReplyInit | UnitResponse:
        reply = handler(args)
        headers = {**(reply.headers or {}), _REPLAY_MARKER: "true"}
        return dataclasses.replace(reply, headers=headers)

    return wrapped


register(
    Mutant(
        id="M39",
        name="replay-marker-on-every-response",
        defect="Every successful response claims to be an idempotent replay, the first execution included.",
        provenance=Provenance.HYPOTHETICAL,
        trips=frozenset({"C19"}),
        vendor=lambda inner: VendorOverlay(inner, routes=wrap_vendor_handlers(_stamp_replay_marker)),
    )
)
"""N-6. C19 asserted the replay carries the marker and never that the first
execution does not; a consumer routing on "was this deduplicated?" is misled
on every call, and the suite stayed green."""
