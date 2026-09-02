"""Assert the built WHEEL carries its data files, not just the source tree.

`uv sync` installs this project editable, so `importlib.resources.files(...)`
resolves straight back to `src/vendorfake/...` on disk. Every test that reads a
profile or the seed through that API therefore passes whether or not the wheel
would ship them -- which is the opposite of what those tests' docstrings claim
to prove, and it means a broken `[tool.hatch.build.targets.wheel]` would reach
a real `pip install` with CI green throughout.

Data files that silently fail to ship are the classic packaging bug: the source
tree works, the wheel does not, and nothing notices until someone installs it.
This builds the artifact and looks inside it, which is the only way to know.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Every non-Python file the package promises to ship, by wheel-relative path.
REQUIRED = (
    "vendorfake/py.typed",
    "vendorfake/fidelity/corpus.schema.json",
    "vendorfake/fidelity/declaration.schema.json",
    "vendorfake/square/seed/default.seed.json",
    "vendorfake/square/fidelity/declaration.json",
    "vendorfake/square/fidelity/extract.json",
    "vendorfake/square/fidelity/pin.json",
    "vendorfake/square/fidelity/corpus/auth.unauthenticated.json",
    "vendorfake/square/fidelity/corpus/locations.list.shape.json",
    "vendorfake/square/fidelity/corpus/loyalty.programs.retrieve.main.json",
    "vendorfake/square/fidelity/corpus/merchants.retrieve.me.json",
    "vendorfake/square/fidelity/corpus/oauth.token.refresh.json",
    "vendorfake/square/fidelity/corpus/orders.create.idempotency-key-reused.json",
    "vendorfake/square/fidelity/corpus/orders.create.idempotent-replay.json",
    "vendorfake/square/fidelity/corpus/orders.create.minimal.json",
    "vendorfake/square/fidelity/corpus/orders.create.missing-location-id.json",
    "vendorfake/square/fidelity/corpus/orders.pay.external-payment.json",
    "vendorfake/square/fidelity/corpus/orders.pay.zero-total.json",
    "vendorfake/square/fidelity/corpus/orders.search.by-location.json",
    "vendorfake/square/fidelity/corpus/webhooks.subscriptions.create.shape.json",
    "vendorfake/toast/fidelity/declaration.json",
    "vendorfake/toast/fidelity/pin.json",
    "vendorfake/toast/fidelity/corpus/auth.bearer.missing.json",
    "vendorfake/toast/fidelity/corpus/auth.bearer.unrecognized.json",
    "vendorfake/toast/fidelity/corpus/auth.login.invalid-credentials.json",
    "vendorfake/toast/fidelity/corpus/auth.login.machine-client.json",
    "vendorfake/toast/fidelity/corpus/auth.restaurant.unknown.json",
    "vendorfake/toast/fidelity/corpus/config.taxrates.by-guid.json",
    "vendorfake/toast/fidelity/corpus/config.taxrates.list.json",
    "vendorfake/toast/fidelity/corpus/menus.v3.menus.json",
    "vendorfake/toast/fidelity/corpus/menus.v3.metadata.json",
    "vendorfake/toast/fidelity/corpus/orders.create.other-payment.json",
    "vendorfake/toast/fidelity/corpus/orders.get.errors.json",
    "vendorfake/toast/fidelity/corpus/orders.prices.documented-example.json",
    "vendorfake/toast/fidelity/corpus/orders.void.voidall.json",
    "vendorfake/toast/fidelity/corpus/stock.search.by-guid.json",
    "vendorfake/square/profiles/full.json",
    "vendorfake/square/profiles/no-chaos.json",
    "vendorfake/square/profiles/no-faults.json",
    "vendorfake/square/profiles/oauth-only.json",
    "vendorfake/square/profiles/orders-only.json",
    "vendorfake/square/profiles/chaos-demo.json",
    "vendorfake/clover/profiles/full.json",
    "vendorfake/clover/profiles/no-chaos.json",
    "vendorfake/clover/profiles/no-faults.json",
    "vendorfake/clover/profiles/oauth-only.json",
    "vendorfake/clover/profiles/orders-only.json",
    "vendorfake/clover/profiles/chaos-demo.json",
    "vendorfake/clover/seed/default.seed.json",
    "vendorfake/toast/profiles/full.json",
    "vendorfake/toast/profiles/no-chaos.json",
    "vendorfake/toast/profiles/no-faults.json",
    "vendorfake/toast/profiles/oauth-only.json",
    "vendorfake/toast/profiles/orders-only.json",
    "vendorfake/toast/profiles/chaos-demo.json",
    "vendorfake/toast/seed/default.seed.json",
)


#: Files that must NOT ship: a non-vendored vendor's extract is cut at run time
#: from a fresh fetch and never committed (konyklabs/roadmap#56). Its presence in
#: a wheel would mean a copy of the vendor's document went out under our name.
FORBIDDEN = ("vendorfake/toast/fidelity/extract.json",)


def main() -> int:
    with tempfile.TemporaryDirectory() as out:
        built = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", out],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        if built.returncode != 0:
            print(built.stdout + built.stderr)
            print("wheel: BUILD FAILED")
            return 1

        wheels = sorted(Path(out).glob("*.whl"))
        if len(wheels) != 1:
            print(f"wheel: expected exactly one wheel, found {[w.name for w in wheels]}")
            return 1

        names = set(zipfile.ZipFile(wheels[0]).namelist())
        missing = [path for path in REQUIRED if path not in names]
        for path in REQUIRED:
            print(f"  {'ok  ' if path in names else 'MISS'} {path}")
        leaked = [path for path in FORBIDDEN if path in names]
        for path in leaked:
            print(f"  LEAK {path} -- must never ship; see FORBIDDEN")
        if leaked:
            print(f"wheel: {len(leaked)} file(s) that must never ship are in {wheels[0].name}")
            return 1
        if missing:
            print(f"wheel: {len(missing)} data file(s) missing from {wheels[0].name}")
            print("       the source tree works and the wheel does not; check")
            print("       [tool.hatch.build.targets.wheel] in pyproject.toml")
            return 1
        print(f"wheel: {wheels[0].name} carries all {len(REQUIRED)} data files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
