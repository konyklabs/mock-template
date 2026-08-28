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

step "ruff check"        uv run ruff check .
step "ruff format"       uv run ruff format --check .
step "mypy --strict"     uv run mypy
step "import-linter"     uv run lint-imports
step "boundary check"    uv run python tools/boundary_check.py -v
step "pytest"            uv run pytest
step "wheel data"        uv run python tools/check_wheel_data.py

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
