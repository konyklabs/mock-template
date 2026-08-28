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

# The target the conformance steps below drive. It lives on the test side of
# the tree because the `http` transport needs a real server and the suite may
# never import one; see tests/conformance/harness.py. An external vendor names
# its own here, or exports VENDORFAKE_CONFORMANCE_TARGET.
TARGET="tests.conformance.harness:target"

step "ruff check"        uv run ruff check .
step "ruff format"       uv run ruff format --check .
step "mypy --strict"     uv run mypy
step "import-linter"     uv run lint-imports
step "boundary check"    uv run python tools/boundary_check.py -v
step "pytest"            uv run pytest
step "wheel data"        uv run python tools/check_wheel_data.py

# The conformance suite through its own entry points, which pytest does not
# exercise: the framework-free CLI a container healthcheck calls, and the
# pytest plugin an installed wheel exposes. `--strict` makes any skip that
# conformance/manifest.json does not declare a failure, and the matrix run is
# the only place the aggregate rule -- every contract passed on at least one
# profile -- is answerable at all.
step "conformance matrix" \
  uv run python -m vendorfake.conformance --target "$TARGET" --transport inprocess --strict
step "conformance http" \
  uv run python -m vendorfake.conformance --target "$TARGET" --transport http --profile full
step "conformance plugin" \
  uv run python -m pytest --pyargs vendorfake.conformance -q \
    --conformance-target "$TARGET" --conformance-strict

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
