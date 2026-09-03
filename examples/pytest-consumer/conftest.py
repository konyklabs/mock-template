"""Fixtures: one unit per test, and a receiver for the webhooks it sends.

A unit is cheap to build -- a few milliseconds -- so every test gets a fresh
one and no test can see another's orders. ``webhook_receiver`` is a real HTTP
endpoint on loopback; the unit posts to it exactly as the vendor would post to
your service, signature and all.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vendorfake.testing import CloverSeed, SquareSeed, StartedUnit, WebhookReceiver, unit, webhook_receiver


@pytest.fixture
def square() -> Iterator[StartedUnit[SquareSeed]]:
    with unit("square") as started:
        yield started


@pytest.fixture
def clover() -> Iterator[StartedUnit[CloverSeed]]:
    with unit("clover") as started:
        yield started


@pytest.fixture
def receiver() -> Iterator[WebhookReceiver]:
    with webhook_receiver() as listening:
        yield listening
