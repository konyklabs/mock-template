from __future__ import annotations

from collections.abc import Iterator

import pytest

from launch import UnitHandle, announce, launch_unit
from subscriber import Subscriber, start_subscriber

SEED_LOCATION = "18YC4JDH91E1H"
TEA_MUG = "2TZFAOHWGG7PAK2QEXWYPZSP"
SEEDED_TOKEN = "EAAAl-unit-seeded-access-token-full-scopes"
APPLICATION_ID = "sandbox-sq0idb-unit-square-application"
APPLICATION_SECRET = "sandbox-sq0csb-unit-square-secret"


@pytest.fixture(scope="session")
def subscriber() -> Iterator[Subscriber]:
    handle = start_subscriber()
    yield handle
    handle.close()


@pytest.fixture(scope="session")
def unit(subscriber: Subscriber) -> Iterator[UnitHandle]:
    handle = launch_unit(profile="full")
    announce(handle, [f"subscriber={handle.host_url(subscriber.port)}"])
    yield handle
    handle.stop()


@pytest.fixture
def auth() -> dict[str, str]:
    return {"authorization": f"Bearer {SEEDED_TOKEN}"}
