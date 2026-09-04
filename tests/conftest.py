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

pytest_plugins = ["pytester"]
