"""Conformance targets for the vendors shipped in this distribution.

FOR: a consumer who installed the wheel and wants to run the contracts::

    pytest --pyargs vendorfake.conformance --conformance-target vendorfake.testing.conformance:square_target
    python -m vendorfake.conformance --target vendorfake.testing.conformance:clover_target

The suite itself never starts a server -- ``vendorfake.conformance`` may not
import a web framework -- so the targets live here, one layer out, where the
``http`` transport can be a real uvicorn on a background thread and the
out-of-process one the shipped ``vendorfake serve`` command in a child.

The repository's own harness (``tests/conformance/harness.py``) reads the
vendors' skip matrices from this module rather than keeping copies: a matrix
that lived in two places would let the wheel's target and CI's disagree about
what a skip means.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager

from vendorfake.conformance import ConformanceClient, ConformanceTarget, HttpConformanceClient
from vendorfake.conformance.client import InProcessConformanceClient
from vendorfake.core.logging import JsonLogger
from vendorfake.core.transport.inprocess import in_process
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.registry import create_unit
from vendorfake.testing import served

__all__ = [
    "CLOVER_EXPECTED_SKIPS",
    "CLOVER_INAPPLICABLE",
    "OUT_OF_PROCESS_TRANSPORT",
    "PROFILES",
    "TOAST_EXPECTED_SKIPS",
    "TOAST_INAPPLICABLE",
    "clover_target",
    "square_target",
    "target",
    "toast_target",
]

PROFILES: tuple[str, ...] = ("full", "no-chaos", "no-faults", "orders-only", "oauth-only", "chaos-demo")
"""The six profiles every built-in vendor ships, so one matrix shape covers
all of them."""

OUT_OF_PROCESS_TRANSPORT = "subprocess"
"""A unit served by ``vendorfake serve`` in a child process. Declared in
``out_of_process`` and never in ``transports``: the matrix does not run it,
the one contract whose claim is about separate runs opens it deliberately."""

CLOVER_EXPECTED_SKIPS: Mapping[str, Sequence[str]] = {
    # A profile that lacks the capability a contract needs, the same shape as
    # conformance/manifest.json describes for the first vendor.
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


@contextmanager
def _in_process(vendor: str, profile: str) -> Iterator[ConformanceClient]:
    built = create_unit(vendor=vendor, profile=profile, sink=MemorySink(), logger=JsonLogger("warn"))
    try:
        yield InProcessConformanceClient(in_process(built))
    finally:
        built.stop()


@contextmanager
def _http(vendor: str, profile: str) -> Iterator[ConformanceClient]:
    # Local import: the target must be resolvable -- and `inprocess` runnable --
    # without the web framework ever loading.
    from vendorfake.asgi import FrameworkTripwire, create_app, serve_in_thread

    tripwire = FrameworkTripwire()
    built = create_unit(
        vendor=vendor,
        profile=profile,
        sink=MemorySink(),
        logger=JsonLogger("warn"),
        framework_answered=tripwire.get,
    )
    try:
        with serve_in_thread(create_app(built, tripwire=tripwire)) as base_url:
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
    """A target for any installed vendor. Each shipped one is wrapped below
    with the tenant parameter and skip matrix it needs."""

    def opener(profile: str, transport: str) -> AbstractContextManager[ConformanceClient]:
        return open_client(vendor, profile, transport)

    return ConformanceTarget(
        name=vendor,
        open_client=opener,
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
    """Every Clover route is scoped to ``{mId}``, so it is filled with the
    seeded merchant; the skip matrix is Clover's own."""
    from vendorfake.clover.seed.constants import SEED_MERCHANT_ID

    return target(
        "clover",
        path_params={"mId": SEED_MERCHANT_ID},
        expected_skips=CLOVER_EXPECTED_SKIPS,
        inapplicable=CLOVER_INAPPLICABLE,
    )


def toast_target() -> ConformanceTarget:
    """The third vendor. No ``path_params``: Toast scopes a request to its
    restaurant with the ``Toast-Restaurant-External-ID`` header, which the
    vendor's credentials publish together with the bearer, so every probe
    path stays the probe."""
    return target(
        "toast",
        expected_skips=TOAST_EXPECTED_SKIPS,
        inapplicable=TOAST_INAPPLICABLE,
    )
