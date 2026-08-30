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
    "vendorfake/square/seed/default.seed.json",
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
)


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
        if missing:
            print(f"wheel: {len(missing)} data file(s) missing from {wheels[0].name}")
            print("       the source tree works and the wheel does not; check")
            print("       [tool.hatch.build.targets.wheel] in pyproject.toml")
            return 1
        print(f"wheel: {wheels[0].name} carries all {len(REQUIRED)} data files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
