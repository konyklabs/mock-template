"""Root fixtures for the outer test session.

``pytest_plugins`` must be declared in a conftest.py reachable from the
rootdir, not in an individual test module -- this is that conftest.
``pytester`` (pytest's own internal testing plugin) is not loaded by default;
``tests/unit/testing/test_pytest_plugin.py`` is the one place in this suite
that drives a real pytest subprocess, to prove what a downstream consumer's
own pytest run sees when vendorfake's ``pytest11`` entry point is installed
(konyklabs/roadmap#71, D3).
"""

from __future__ import annotations

import os

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True, scope="session")
def _no_ambient_vendorfake_environment() -> None:
    """Every binding now honours exported ``VENDORFAKE_*`` variables, so this
    suite strips any the developer's shell carries; tests that want one set it."""
    for key in [k for k in os.environ if k.startswith("VENDORFAKE_")]:
        del os.environ[key]
