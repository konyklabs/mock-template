"""Conformance targets for the vendors shipped in this distribution, usable from
an installed wheel. ``vendorfake.conformance`` may not import a web framework, so
the targets live here, where the ``http`` transport can be a real uvicorn on a
background thread. ``tests/conformance/harness.py`` reads the skip matrices from
this module rather than keeping copies.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from vendorfake.conformance import ConformanceClient, ConformanceTarget, HttpConformanceClient
from vendorfake.conformance.client import InProcessConformanceClient
from vendorfake.core.logging import JsonLogger
from vendorfake.core.transport.inprocess import in_process
from vendorfake.core.util.json import canonical_json
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.registry import create_unit
from vendorfake.testing import served

__all__ = [
    "CLOVER_EXPECTED_SKIPS",
    "CLOVER_INAPPLICABLE",
    "LIGHTSPEED_EXPECTED_SKIPS",
    "LIGHTSPEED_INAPPLICABLE",
    "OUT_OF_PROCESS_TRANSPORT",
    "PROFILES",
    "TOAST_EXPECTED_SKIPS",
    "TOAST_INAPPLICABLE",
    "clover_target",
    "lightspeed_target",
    "open_with_seed_overlay",
    "square_target",
    "target",
    "toast_target",
]

PROFILES: tuple[str, ...] = ("full", "no-chaos", "no-faults", "orders-only", "oauth-only", "chaos-demo")
"""The six profiles every built-in vendor ships, so one matrix shape fits all."""

OUT_OF_PROCESS_TRANSPORT = "subprocess"
"""A unit served by ``vendorfake serve`` in a child process; declared in
``out_of_process`` and never in ``transports``, so the matrix does not run it."""

CLOVER_EXPECTED_SKIPS: Mapping[str, Sequence[str]] = {
    # A profile that lacks the capability a contract needs.
    "C07": ("oauth-only",),
    "C08": ("no-faults",),
    "C09": ("oauth-only", "orders-only"),
    "C12": ("no-faults",),
    "C16": ("oauth-only", "orders-only"),
    "C17": ("oauth-only",),
    "C18": ("oauth-only", "orders-only"),
    "C21": ("full", "no-chaos", "no-faults", "oauth-only", "orders-only"),
    "C26": ("oauth-only",),
    "C27": ("no-faults",),
    "C29": ("no-chaos", "no-faults", "oauth-only", "orders-only"),
    "C32": ("full", "no-chaos", "no-faults", "oauth-only", "orders-only"),
}

CLOVER_INAPPLICABLE: Mapping[str, str] = {
    "C19": (
        "Clover's REST API documents no idempotency key on any endpoint, so no clover route carries an "
        "IdempotencySpec and the replay contract can never be asked of this vendor."
    ),
    "C24": (
        "Clover's REST API documents no idempotency key on any endpoint, so no clover route carries an "
        "IdempotencySpec and the key-scope contract can never be asked of this vendor."
    ),
    "C25": (
        "Clover's REST API documents no idempotency key on any endpoint, so no clover route carries an "
        "IdempotencySpec and the mismatch contract can never be asked of this vendor."
    ),
}

TOAST_EXPECTED_SKIPS: Mapping[str, Sequence[str]] = {
    "C07": ("oauth-only",),
    "C08": ("no-faults",),
    "C09": ("oauth-only", "orders-only"),
    "C12": ("no-faults",),
    "C16": ("oauth-only", "orders-only"),
    "C17": ("oauth-only",),
    "C18": ("oauth-only", "orders-only"),
    "C21": ("full", "no-chaos", "no-faults", "oauth-only", "orders-only"),
    "C27": ("no-faults",),
    "C29": ("no-chaos", "no-faults", "oauth-only", "orders-only"),
    "C32": ("full", "no-chaos", "no-faults", "oauth-only", "orders-only"),
}

_TOAST_NO_IDEMPOTENCY_KEY = (
    "Toast's REST APIs document no idempotency key on any endpoint (the orders API deduplicates on the "
    "caller's unique externalId instead), so no toast route carries an IdempotencySpec and the {contract} "
    "contract can never be asked of this vendor."
)

TOAST_INAPPLICABLE: Mapping[str, str] = {
    "C19": _TOAST_NO_IDEMPOTENCY_KEY.format(contract="replay"),
    "C26": (
        "Every paginating toast surface opts out of the identity walk by declaration -- the config "
        "lists answer a bare array with the next token in a response header and no page-size "
        "parameter, and the partners envelope can never hold two rows because this unit models one "
        "connected restaurant -- so the page-walk contract can never be asked of this vendor. The "
        "inapplicable guard fails the day a walkable toast list appears."
    ),
    "C24": _TOAST_NO_IDEMPOTENCY_KEY.format(contract="key-scope"),
    "C25": _TOAST_NO_IDEMPOTENCY_KEY.format(contract="mismatch"),
}


LIGHTSPEED_EXPECTED_SKIPS: Mapping[str, Sequence[str]] = {
    "C07": ("oauth-only",),
    "C08": ("no-faults",),
    "C09": ("oauth-only", "orders-only"),
    "C12": ("no-faults",),
    "C16": ("oauth-only", "orders-only"),
    "C17": ("oauth-only",),
    "C18": ("oauth-only", "orders-only"),
    "C21": ("full", "no-chaos", "no-faults", "oauth-only", "orders-only"),
    "C26": ("oauth-only",),
    "C27": ("no-faults",),
    "C29": ("no-chaos", "no-faults", "oauth-only", "orders-only"),
    "C32": ("full", "no-chaos", "no-faults", "oauth-only", "orders-only"),
}
"""Which contract skips on which Lightspeed profile: ``oauth-only`` enables only
the token endpoint, ``orders-only`` has no webhooks, ``no-faults`` no chaos, the
delivery-scope contracts need ``webhooks.chaos``, and the retry-cascade and
clock-independence contracts run only on the virtual-clock profile."""

_LIGHTSPEED_NO_IDEMPOTENCY_KEY = (
    "Lightspeed's API documents no idempotency key on any endpoint -- there is no Idempotency-Key header and "
    "no request-id member on any of the 201 operations -- so no lightspeed route carries an IdempotencySpec "
    "and the {contract} contract can never be asked of this vendor."
)

LIGHTSPEED_INAPPLICABLE: Mapping[str, str] = {
    "C19": _LIGHTSPEED_NO_IDEMPOTENCY_KEY.format(contract="replay"),
    "C24": _LIGHTSPEED_NO_IDEMPOTENCY_KEY.format(contract="key-scope"),
    "C25": _LIGHTSPEED_NO_IDEMPOTENCY_KEY.format(contract="mismatch"),
}


@contextmanager
def _in_process(vendor: str, profile: str, env: Mapping[str, str] | None = None) -> Iterator[ConformanceClient]:
    built = create_unit(vendor=vendor, profile=profile, sink=MemorySink(), logger=JsonLogger("warn"), env=env)
    try:
        yield InProcessConformanceClient(in_process(built))
    finally:
        built.stop()


@contextmanager
def open_with_seed_overlay(vendor: str, profile: str, overlay: Mapping[str, Any]) -> Iterator[ConformanceClient]:
    """A unit with ``overlay`` laid over the profile's seed, in process whatever
    the matrix row's transport is, the overlay being applied while the unit is
    built. Nothing is caught: an overlay the unit refuses raises out of the
    ``with``, which is the half of the contract the clause is about."""
    with _in_process(vendor, profile, {"VENDORFAKE_SEED_OVERLAY": canonical_json(dict(overlay))}) as client:
        yield client


@contextmanager
def _http(vendor: str, profile: str) -> Iterator[ConformanceClient]:
    # Local import: the target must resolve without the web framework loading.
    from vendorfake.asgi import create_app, serve_in_thread

    built = create_unit(vendor=vendor, profile=profile, sink=MemorySink(), logger=JsonLogger("warn"))
    try:
        with serve_in_thread(create_app(built)) as base_url:
            client = HttpConformanceClient(base_url)
            try:
                yield client
            finally:
                client.close()
    finally:
        built.stop()


@contextmanager
def _subprocess(vendor: str, profile: str) -> Iterator[ConformanceClient]:
    with served(vendor, profile) as child:
        client = HttpConformanceClient(child.base_url)
        try:
            yield client
        finally:
            client.close()


@contextmanager
def open_client(vendor: str, profile: str, transport: str) -> Iterator[ConformanceClient]:
    if transport == "inprocess":
        with _in_process(vendor, profile) as client:
            yield client
    elif transport == "http":
        with _http(vendor, profile) as client:
            yield client
    elif transport == OUT_OF_PROCESS_TRANSPORT:
        with _subprocess(vendor, profile) as client:
            yield client
    else:
        raise ValueError(
            f"unknown transport {transport!r}; this target offers 'inprocess', 'http' and {OUT_OF_PROCESS_TRANSPORT!r}"
        )


def target(
    vendor: str,
    *,
    profiles: Sequence[str] = PROFILES,
    transports: Sequence[str] = ("inprocess", "http"),
    out_of_process: Sequence[str] = (OUT_OF_PROCESS_TRANSPORT,),
    path_params: Mapping[str, str] | None = None,
    expected_skips: Mapping[str, Sequence[str]] | None = None,
    inapplicable: Mapping[str, str] | None = None,
) -> ConformanceTarget:
    """A target for any installed vendor; each shipped one is wrapped below."""

    def opener(profile: str, transport: str) -> AbstractContextManager[ConformanceClient]:
        return open_client(vendor, profile, transport)

    def overlay_opener(profile: str, overlay: Mapping[str, Any]) -> AbstractContextManager[ConformanceClient]:
        return open_with_seed_overlay(vendor, profile, overlay)

    return ConformanceTarget(
        name=vendor,
        open_client=opener,
        open_with_seed_overlay=overlay_opener,
        profiles=tuple(profiles),
        transports=tuple(transports),
        out_of_process=tuple(out_of_process),
        path_params=dict(path_params or {}),
        expected_skips=expected_skips,
        inapplicable=dict(inapplicable or {}),
    )


def square_target() -> ConformanceTarget:
    return target("square")


def clover_target() -> ConformanceTarget:
    """Every Clover route is scoped to ``{mId}``, filled with the seeded merchant."""
    from vendorfake.clover.seed.constants import SEED_MERCHANT_ID

    return target(
        "clover",
        path_params={"mId": SEED_MERCHANT_ID},
        expected_skips=CLOVER_EXPECTED_SKIPS,
        inapplicable=CLOVER_INAPPLICABLE,
    )


def lightspeed_target() -> ConformanceTarget:
    """No ``path_params``: tenancy is the per-retailer subdomain, not a path."""
    return target(
        "lightspeed",
        expected_skips=LIGHTSPEED_EXPECTED_SKIPS,
        inapplicable=LIGHTSPEED_INAPPLICABLE,
    )


def toast_target() -> ConformanceTarget:
    """No ``path_params``: Toast scopes with a header the credentials publish."""
    return target(
        "toast",
        expected_skips=TOAST_EXPECTED_SKIPS,
        inapplicable=TOAST_INAPPLICABLE,
    )
