#!/usr/bin/env bash
# The one command CI and a human both run.
#
# Deliberately does NOT use `set -e`: every step runs and reports, so one
# failure does not hide the state of the rest. The summary table is the
# evidence a reviewer reads; the exit code is what the gate reads.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

# `--quick` is what a pull request runs in CI (konyklabs/roadmap#103): every
# static check, the wheel and the docs -- about half a minute -- and neither
# the pytest suite (several thousand tests, minutes on a runner)
# nor the conformance runs below. Those the full script keeps for a push
# to main, a dispatch, and a laptop before a push, where they belong: the
# evidence a PR carries is the self-test output pasted from that laptop run.
QUICK=0
if [ "${1:-}" = "--quick" ]; then QUICK=1; fi

names=()
codes=()

step() {
  local name="$1"; shift
  printf '\n\033[1m== %s ==\033[0m\n' "$name"
  "$@"
  local code=$?
  names+=("$name")
  codes+=("$code")
  return 0
}

# The docs site, in one step: (1) regenerate docs/reference/*.md and fail if
# that changes the working tree -- the pattern konyklabs/.github's arch/
# uses for arch/generated/VIEWS.md, so a route or a fault added without
# re-running the generator is a red self-test rather than a stale page; (2)
# `mkdocs build --strict`, which fails on a broken nav entry or a dead
# internal link. `git status --porcelain`, not `git diff`, so a *new* vendor
# whose routes-<vendor>.md was never generated at all (untracked, not
# modified) fails the same way a stale one does. `uv run --group docs`
# (not plain `uv run`): CI's own `Sync` step runs `uv sync --frozen` with no
# group flags, which -- like a plain `uv sync` anywhere -- installs the
# `dev` group by default but not `docs`, so this step syncs its own
# dependency rather than depending on how the caller synced.
_docs_step() {
  uv run python tools/gen_reference.py || return 1
  local dirty
  dirty="$(git status --porcelain -- docs/reference)"
  if [ -n "$dirty" ]; then
    printf 'docs/reference is stale: `uv run python tools/gen_reference.py` changed the working tree.\n' >&2
    printf 'Re-run it and commit the diff:\n%s\n' "$dirty" >&2
    return 1
  fi
  uv run --group docs mkdocs build --strict
}

# The targets the conformance steps below drive, one per built-in vendor. They
# live on the test side of the tree because the `http` transport needs a real
# server and the suite may never import one; see tests/conformance/harness.py.
# An external vendor names its own here, or exports VENDORFAKE_CONFORMANCE_TARGET.
TARGETS=(
  "square=tests.conformance.harness:target"
  "clover=tests.conformance.harness:clover_target"
  "toast=tests.conformance.harness:toast_target"
  "lightspeed=tests.conformance.harness:lightspeed_target"
)

# Fidelity to the vendor (D-006), the targets. Only vendors with a fidelity
# declaration are listed; a vendor without one is reported by `fidelity
# report` as undeclared rather than skipped here. A vendor whose terms keep
# its specification out of the repository (`vendored: false`,
# konyklabs/roadmap#56) is ALSO listed in FIDELITY_FETCH_TARGETS: its extract
# is cut at run time into ~/.cache/vendorfake/fidelity, and `fetch` below
# populates that cache before pytest so the first thing to hit the network is
# a named step and never a unit test.
FIDELITY_TARGETS=(
  "square=vendorfake.testing.fidelity:square_target"
  "toast=vendorfake.testing.fidelity:toast_target"
  # lightspeed is deliberately NOT in FIDELITY_FETCH_TARGETS below: its extract
  # is committed (`vendored: true` -- api-2026-07.yaml is published under
  # Apache 2.0), so both steps run offline, on `--quick` as well as on a full
  # run, and neither depends on the vendor's site answering.
  "lightspeed=vendorfake.testing.fidelity:lightspeed_target"
)
FIDELITY_FETCH_TARGETS=(
  "toast=vendorfake.testing.fidelity:toast_target"
)

step "ruff check"        uv run ruff check .
step "ruff format"       uv run ruff format --check .
step "mypy --strict"     uv run mypy
step "import-linter"     uv run lint-imports
step "boundary check"    uv run python tools/boundary_check.py -v
step "prose ratio"       uv run python tools/prose_ratio.py src --max-total 15 --top 0
if [ "$QUICK" -eq 0 ]; then
  for entry in "${FIDELITY_FETCH_TARGETS[@]}"; do
    vendor="${entry%%=*}"
    TARGET="${entry#*=}"
    step "fidelity fetch ($vendor)" \
      uv run python -m vendorfake.fidelity fetch --target "$TARGET"
  done
fi
if [ "$QUICK" -eq 0 ]; then
  step "pytest"          uv run pytest
fi
step "wheel data"        uv run python tools/check_wheel_data.py
step "docs"              _docs_step

# Security scanners, full run only (konyklabs/roadmap#105): pip-audit over the
# runtime dependencies as the lockfile resolves them, bandit over the package
# at medium severity and up. Both run from their published packages via uvx,
# so neither is a dependency of vendorfake itself, and both are pinned so a
# tool release cannot turn main red with an empty diff -- pip-audit's advisory
# database stays live, which is the point; the tool that reads it does not
# move on its own. A finding that is a deliberate choice is annotated at the
# site with its reason, never silenced by configuration.
BANDIT_VERSION="1.9.4"
PIP_AUDIT_VERSION="2.10.1"
_pip_audit_step() {
  local requirements
  requirements="$(mktemp)"
  if ! uv export --frozen --no-dev --no-hashes --no-emit-project > "$requirements"; then
    rm -f "$requirements"
    return 1
  fi
  uvx "pip-audit==$PIP_AUDIT_VERSION" --strict -r "$requirements"
  local code=$?
  rm -f "$requirements"
  return $code
}
if [ "$QUICK" -eq 0 ]; then
  step "pip-audit"         _pip_audit_step
  step "bandit"            uvx "bandit==$BANDIT_VERSION" -q -r src/vendorfake -ll
fi

# The pytest consumer example, run as its own uv project against THIS
# checkout (konyklabs/roadmap#105). It is what a consumer copies, and until
# this step existed a documented behaviour change (a paid Toast check answers
# CLOSED, not PAID) broke both examples on main with every other step green.
_example_step() {
  (cd examples/pytest-consumer && uv sync -q && uv run pytest -q -p no:randomly)
}
if [ "$QUICK" -eq 0 ]; then
  step "example (pytest)"  _example_step
fi

# The conformance suite through its own entry points, which pytest does not
# exercise: the framework-free CLI a container healthcheck calls, and the
# pytest plugin an installed wheel exposes. `--strict` makes any skip that
# conformance/manifest.json does not declare a failure, and the matrix run is
# the only place the aggregate rule -- every contract passed on at least one
# profile -- is answerable at all. Every target runs every step: a vendor
# whose matrix only ever ran on a laptop would ship a regression green.
#
# `-p vendorfake.conformance.plugin` loads the conformance plugin explicitly:
# since konyklabs/roadmap#71 it is no longer a `pytest11` entry point that
# installing the wheel auto-loads into every consumer's pytest run -- only
# `vendorfake.pytest` (the `vendorfake` marker and its three fixtures) is.
for entry in "${TARGETS[@]}"; do
  if [ "$QUICK" -eq 1 ]; then break; fi
  vendor="${entry%%=*}"
  TARGET="${entry#*=}"
  step "matrix ($vendor)" \
    uv run python -m vendorfake.conformance --target "$TARGET" --transport inprocess --strict
  step "http ($vendor)" \
    uv run python -m vendorfake.conformance --target "$TARGET" --transport http --profile full
  step "plugin ($vendor)" \
    uv run python -m pytest --pyargs vendorfake.conformance -q \
      -p vendorfake.conformance.plugin \
      --conformance-target "$TARGET" --conformance-strict
done

# Fidelity to the vendor (D-006): the extract (committed, or cached by the
# `fetch` step above) and the pin agree with each other and the declaration
# (offline -- whether UPSTREAM moved is the scheduled drift job's question,
# never a pull request's), and the documented corpus passes with every
# response schema-validated.
for entry in "${FIDELITY_TARGETS[@]}"; do
  vendor="${entry%%=*}"
  TARGET="${entry#*=}"
  # A vendor whose extract is fetched rather than committed has nothing to
  # check under --quick, where the fetch step above did not run: a pull
  # request's check must not depend on a vendor's documentation site
  # answering. The full run (main, a laptop) fetches first and checks both.
  if [ "$QUICK" -eq 1 ] && printf '%s\n' "${FIDELITY_FETCH_TARGETS[@]}" | grep -qx "$entry"; then
    printf '\n\033[1m== fidelity (%s) ==\033[0m\nskipped under --quick: the extract is fetched, never committed\n' "$vendor"
    continue
  fi
  step "fidelity pin ($vendor)" \
    uv run python -m vendorfake.fidelity pin --check --offline --target "$TARGET"
  step "fidelity report ($vendor)" \
    uv run python -m vendorfake.fidelity report --target "$TARGET"
done

printf '\n\033[1m== summary ==\033[0m\n'
failed=0
for i in "${!names[@]}"; do
  if [ "${codes[$i]}" -eq 0 ]; then
    printf '  %-20s PASS\n' "${names[$i]}"
  else
    printf '  %-20s FAIL (exit %s)\n' "${names[$i]}" "${codes[$i]}"
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  printf '\nself-test: FAILED\n'
  exit 1
fi
printf '\nself-test: PASSED\n'
