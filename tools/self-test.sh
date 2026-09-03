#!/usr/bin/env bash
# The one command CI and a human both run.
#
# Deliberately does NOT use `set -e`: every step runs and reports, so one
# failure does not hide the state of the rest. The summary table is the
# evidence a reviewer reads; the exit code is what the gate reads.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

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
)

step "ruff check"        uv run ruff check .
step "ruff format"       uv run ruff format --check .
step "mypy --strict"     uv run mypy
step "import-linter"     uv run lint-imports
step "boundary check"    uv run python tools/boundary_check.py -v
step "pytest"            uv run pytest
step "wheel data"        uv run python tools/check_wheel_data.py
step "docs"              _docs_step

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
