"""Claims the tree makes about itself that a grep can keep honest.

konyklabs/roadmap#99, item 3: a module docstring showed
``pytest --pyargs vendorfake.conformance`` without the ``-p`` flag that
actually loads the plugin, after the same false claim had been fixed in four
other places. A file that names the selection must also name the flag.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELECTION = "--pyargs vendorfake.conformance"
FLAG = "-p vendorfake.conformance.plugin"
SCOPE = ("src", "docs", "tools", "examples", "README.md", "AGENTS.md", "CHANGELOG.md")


def _files() -> list[Path]:
    found: list[Path] = []
    for entry in SCOPE:
        path = ROOT / entry
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(
                p for p in path.rglob("*") if p.is_file() and p.suffix in {".py", ".md", ".sh", ".toml", ".txt"}
            )
    return found


def test_every_file_that_names_the_conformance_selection_also_names_the_plugin_flag() -> None:
    offenders: list[str] = []
    for path in _files():
        text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8", errors="replace"))
        if SELECTION in text and FLAG not in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"{SELECTION!r} without {FLAG!r}: {offenders}"


def test_the_check_has_something_to_check() -> None:
    assert any(SELECTION in p.read_text(encoding="utf-8", errors="replace") for p in _files())
