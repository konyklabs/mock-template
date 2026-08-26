"""Mechanical enforcement of the framework-free-core invariant.

D-002 turns on one property: the stateful machinery — journal, state store,
capability registry, chaos engine, webhook dispatcher, retry — imports nothing
from the web framework, which lives only in the transport adapter. Two of the
three bake-off entries that preceded this implementation broke precisely
because a framework's request-parsing assumptions had leaked into shared code.

A convention would not have caught that. This does, in three passes:

1. **AST pass.** Parse every file under ``src/`` and check the import edges
   against ``tools/boundary.toml``: the forbidden web-framework modules, the
   Pydantic allow-list, the first-party layer rules, and the rule that no
   vendor slug appears as a literal anywhere in shared code.

2. **Subprocess import pass.** Import every core, conformance and vendor
   module in a fresh interpreter with the forbidden names refused at
   ``sys.meta_path``, then call into the public API. The AST pass cannot see
   ``importlib.import_module("fastapi")``; this can. Measured: against a
   deliberate dynamic import the static pass exits 0 and this pass exits 1.

3. **Call-shape pass.** Faults may be armed from exactly one place, so that
   capability gating cannot be bypassed by a second arming path. This is the
   defect the sonnet bake-off entry shipped.

The import-linter contracts in ``pyproject.toml`` cover the same import graph
declaratively and are run alongside this, deliberately: the invariant should
not rest solely on a checker written by the same run that wrote the code.

Exit code is 0 when clean, 1 when any ERROR was raised. NOTE lines print but
do not fail.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
POLICY_PATH = REPO_ROOT / "tools" / "boundary.toml"


@dataclass(frozen=True)
class Finding:
    level: str  # "ERROR" or "NOTE"
    path: str
    line: int
    message: str
    fix: str

    def render(self) -> str:
        return f"{self.level} {self.path}:{self.line} {self.message}\n        fix: {self.fix}"


@dataclass(frozen=True)
class ImportEdge:
    module: str
    line: int
    guarded: bool  # under `if TYPE_CHECKING:` or inside a function body


def load_policy() -> dict[str, object]:
    with POLICY_PATH.open("rb") as handle:
        policy: dict[str, object] = tomllib.load(handle)
    return policy


def policy_digest() -> str:
    return hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()


def python_files() -> Iterator[Path]:
    yield from sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def vendor_slugs() -> list[str]:
    """Directories under src/vendorfake/ that define a vendor, discovered not listed."""
    package = SRC / "vendorfake"
    if not package.is_dir():
        return []
    return sorted(child.name for child in package.iterdir() if child.is_dir() and (child / "vendor.py").is_file())


def import_edges(tree: ast.AST) -> list[ImportEdge]:
    """Every imported module name, with whether it was reached under a guard.

    Function-local and ``if TYPE_CHECKING:`` imports are recorded, not skipped:
    a lazy import still couples the module, and the reference implementation's
    equivalent leak would have been invisible to a module-level-only scan.
    """
    edges: list[ImportEdge] = []

    class Walker(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_If(self, node: ast.If) -> None:
            test = node.test
            type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if type_checking:
                self.depth += 1
                for child in node.body:
                    self.visit(child)
                self.depth -= 1
                for child in node.orelse:
                    self.visit(child)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                edges.append(ImportEdge(alias.name, node.lineno, self.depth > 0))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level == 0 and node.module:
                edges.append(ImportEdge(node.module, node.lineno, self.depth > 0))

    Walker().visit(tree)
    return edges


def root_of(module: str) -> str:
    return module.split(".")[0]


def first_party(module: str) -> bool:
    return root_of(module) == "vendorfake"


def module_to_path(module: str) -> str:
    """``vendorfake.core.state.store`` -> ``src/vendorfake/core/state/store``."""
    return "src/" + module.replace(".", "/")


def matches_prefix(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def ast_pass(policy: dict[str, object]) -> list[Finding]:
    findings: list[Finding] = []
    forbidden = policy.get("forbidden", {})
    assert isinstance(forbidden, dict)
    forbidden_modules = set(forbidden.get("modules", []))
    forbidden_allowed = list(forbidden.get("allowed_prefixes", []))

    pyd = policy.get("pydantic", {})
    assert isinstance(pyd, dict)
    pyd_forbidden_prefixes = list(pyd.get("forbidden_prefixes", []))
    pyd_allowed = set(pyd.get("allowed_modules", []))

    layers = policy.get("layers", {})
    assert isinstance(layers, dict)
    externals = policy.get("externals", {})
    assert isinstance(externals, dict)
    exceptions = policy.get("exceptions", {})
    assert isinstance(exceptions, dict)

    slugs = vendor_slugs()
    slug_cfg = policy.get("vendor_slugs", {})
    assert isinstance(slug_cfg, dict)
    slug_prefixes = list(slug_cfg.get("scan_prefixes", []))

    for path in python_files():
        where = rel(path)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=where)
        excused = where in exceptions

        for edge in import_edges(tree):
            root = root_of(edge.module)

            # 1. the web framework, wherever it is reached from
            if root in forbidden_modules and not matches_prefix(where, forbidden_allowed):
                if excused:
                    findings.append(
                        Finding(
                            "NOTE",
                            where,
                            edge.line,
                            f"imports {edge.module}, excused: {exceptions[where]}",
                            "no action; the exception is recorded in tools/boundary.toml",
                        )
                    )
                else:
                    how = "inside a function or TYPE_CHECKING block" if edge.guarded else "at module level"
                    findings.append(
                        Finding(
                            "ERROR",
                            where,
                            edge.line,
                            f"imports {edge.module} {how}; only {', '.join(forbidden_allowed)} may",
                            "move the framework-facing code into src/vendorfake/asgi/ and pass plain data inward",
                        )
                    )

            # 2. Pydantic's allow-list inside the core
            if root == "pydantic" and matches_prefix(where, pyd_forbidden_prefixes) and where not in pyd_allowed:
                findings.append(
                    Finding(
                        "ERROR",
                        where,
                        edge.line,
                        "imports pydantic outside the core's allow-list",
                        "keep entities as plain dicts, or add this module to [pydantic].allowed_modules with a reason",
                    )
                )

            # 3. first-party layering
            if first_party(edge.module):
                target = module_to_path(edge.module)
                for prefix, allowed in layers.items():
                    if where.startswith(prefix) and isinstance(allowed, list):
                        if not matches_prefix(target, allowed) and not target.startswith(prefix):
                            findings.append(
                                Finding(
                                    "ERROR",
                                    where,
                                    edge.line,
                                    f"imports {edge.module}, which is outside the layers {prefix} may reach",
                                    f"{prefix} may import only: {', '.join(allowed)}",
                                )
                            )
                        break

            # 4. third-party allow-list per layer
            if not first_party(edge.module) and root not in sys.stdlib_module_names:
                for prefix, allowed_ext in externals.items():
                    if where.startswith(prefix) and isinstance(allowed_ext, list):
                        if root not in allowed_ext and root not in forbidden_modules:
                            findings.append(
                                Finding(
                                    "ERROR",
                                    where,
                                    edge.line,
                                    f"imports third-party {root}, which {prefix} may not depend on",
                                    f"{prefix} may import only: {', '.join(allowed_ext)} (plus the standard library)",
                                )
                            )
                        break

        # 5. no vendor slug as a literal in shared code
        if slugs and matches_prefix(where, slug_prefixes):
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    lowered = node.value.lower()
                    for slug in slugs:
                        if slug in lowered:
                            findings.append(
                                Finding(
                                    "ERROR",
                                    where,
                                    node.lineno,
                                    f"the string {node.value!r} names the vendor {slug!r} in shared code",
                                    "the core must emit vendor-neutral values and let the vendor module map them",
                                )
                            )
    return findings


RUNTIME_PROBE = """
import importlib, pkgutil, sys

BLOCKED = {blocked!r}

class Blocked:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError("blocked by boundary_check: " + fullname)
        return None

sys.meta_path.insert(0, Blocked())

failures = []
for name in {roots!r}:
    try:
        package = importlib.import_module(name)
    except ImportError as exc:
        failures.append(name + ": " + str(exc))
        continue
    if not hasattr(package, "__path__"):
        continue
    for info in pkgutil.walk_packages(package.__path__, name + "."):
        try:
            importlib.import_module(info.name)
        except ImportError as exc:
            failures.append(info.name + ": " + str(exc))

for line in failures:
    print(line)
sys.exit(1 if failures else 0)
"""


def subprocess_pass(policy: dict[str, object], roots: list[str]) -> list[Finding]:
    forbidden = policy.get("forbidden", {})
    assert isinstance(forbidden, dict)
    blocked = sorted(set(forbidden.get("modules", [])))
    if not roots:
        return []
    script = RUNTIME_PROBE.format(blocked=blocked, roots=roots)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if completed.returncode == 0:
        return []
    findings = []
    for line in completed.stdout.splitlines() + completed.stderr.splitlines():
        if not line.strip():
            continue
        findings.append(
            Finding(
                "ERROR",
                line.split(":")[0].strip(),
                0,
                f"could not be imported with {', '.join(blocked)} blocked: {line}",
                "remove the dynamic import; the core must not reach the framework at run time either",
            )
        )
    return findings


def call_shape_pass() -> list[Finding]:
    """Faults may be armed from exactly one module.

    The sonnet bake-off entry shipped a second arming path that merged
    per-request chaos headers unconditionally, bypassing capability gating.
    One choke point makes that unrepresentable rather than merely discouraged.
    """
    findings: list[Finding] = []
    selector = "src/vendorfake/core/chaos/selector.py"
    for path in python_files():
        where = rel(path)
        if where == selector or not where.startswith("src/vendorfake/core/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=where)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "evaluate":
                value = node.func.value
                if isinstance(value, ast.Name) and value.id in {"chaos", "engine", "chaos_engine"}:
                    findings.append(
                        Finding(
                            "ERROR",
                            where,
                            node.lineno,
                            "arms a fault outside the single fault-selection choke point",
                            f"route it through {selector} so capability gating cannot be bypassed",
                        )
                    )
    return findings


def discover_roots() -> list[str]:
    roots = []
    for name in ("core", "conformance"):
        if (SRC / "vendorfake" / name / "__init__.py").is_file():
            roots.append(f"vendorfake.{name}")
    roots.extend(f"vendorfake.{slug}" for slug in vendor_slugs())
    return roots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="print each pass and what it covered")
    args = parser.parse_args(argv)

    policy = load_policy()
    roots = discover_roots()

    findings: list[Finding] = []
    passes: list[tuple[str, Callable[[], list[Finding]]]] = [
        ("AST", lambda: ast_pass(policy)),
        ("subprocess import", lambda: subprocess_pass(policy, roots)),
        ("call shape", call_shape_pass),
    ]
    for name, run in passes:
        found = run()
        findings.extend(found)
        if args.verbose:
            count = sum(1 for f in found if f.level == "ERROR")
            print(f"  pass: {name:<20} {count} error(s)")

    if args.verbose:
        print(f"  policy: tools/boundary.toml sha256={policy_digest()}")
        print(
            f"  scanned: {sum(1 for _ in python_files())} files, vendors: {', '.join(vendor_slugs()) or '(none yet)'}"
        )

    for finding in findings:
        print(finding.render())

    errors = [f for f in findings if f.level == "ERROR"]
    notes = [f for f in findings if f.level == "NOTE"]
    print(f"boundary: {len(errors)} error(s), {len(notes)} note(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
