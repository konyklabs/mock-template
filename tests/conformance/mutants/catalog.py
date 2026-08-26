"""Twenty units, each broken in exactly one way, and the check each must trip.

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

WHAT IS NOT REACHABLE FROM A PRODUCTION PATH. Nothing in this module is
imported by ``src/vendorfake/``. Every mutation is applied by handing a
deliberately wrong collaborator to a constructor that already takes one, so
the seams are real and the defects are not.
"""

from __future__ import annotations

import itertools
import secrets
from collections.abc import Mapping, Sequence
from typing import Any

from tests.conformance.harness import PROFILES
from tests.conformance.mutants.model import Mutant, Provenance, register
from tests.conformance.mutants.seams import (
    ClientOverlay,
    ErrorShaperOverlay,
    LeakyFaultSelector,
    PermissiveStateMachine,
    SignerOverlay,
    VendorOverlay,
    carries_magic,
    replace_control_route,
    rewrite_document,
    wrap_vendor_handlers,
)
from vendorfake.conformance.client import FORM_CONTENT_TYPE, ConformanceClient, ConformanceResponse
from vendorfake.core.capability.registry import CapabilityRegistry
from vendorfake.core.chaos.engine import ChaosEngine
from vendorfake.core.chaos.selector import FaultSelector
from vendorfake.core.kernel.types import (
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
from vendorfake.core.webhooks.models import DeliveryMetadata
from vendorfake.square.retry import RETRY_NUMBER_HEADER, RETRY_REASON_HEADER, RETRY_REASONS
from vendorfake.square.signer import SIGNATURE_HEADER, square_signature

__all__ = ["PERMISSIVE_SELF_TRANSITION", "UNGATED_IN_BAND_CHAOS"]

_PROBE_CAPABILITY = "merchant-directory"
"""An existing, enabled surface capability the route mutants can hang a route
off. Chosen so that a mutant about *route* wiring does not also become a
mutant about capability declaration."""


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
# C10, C15 -- the transport carries bytes and content types, and changes neither.
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
