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

# The targets the conformance steps below drive, one per built-in vendor. They
# live on the test side of the tree because the `http` transport needs a real
# server and the suite may never import one; see tests/conformance/harness.py.
# An external vendor names its own here, or exports VENDORFAKE_CONFORMANCE_TARGET.
TARGETS=(
  "square=tests.conformance.harness:target"
  "clover=tests.conformance.harness:clover_target"
  "toast=tests.conformance.harness:toast_target"
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
)
FIDELITY_FETCH_TARGETS=(
  "toast=vendorfake.testing.fidelity:toast_target"
)

step "ruff check"        uv run ruff check .
step "ruff format"       uv run ruff format --check .
step "mypy --strict"     uv run mypy
step "import-linter"     uv run lint-imports
step "boundary check"    uv run python tools/boundary_check.py -v
for entry in "${FIDELITY_FETCH_TARGETS[@]}"; do
  vendor="${entry%%=*}"
  TARGET="${entry#*=}"
  step "fidelity fetch ($vendor)" \
    uv run python -m vendorfake.fidelity fetch --target "$TARGET"
done
step "pytest"            uv run pytest
step "wheel data"        uv run python tools/check_wheel_data.py

# The conformance suite through its own entry points, which pytest does not
# exercise: the framework-free CLI a container healthcheck calls, and the
# pytest plugin an installed wheel exposes. `--strict` makes any skip that
# conformance/manifest.json does not declare a failure, and the matrix run is
# the only place the aggregate rule -- every contract passed on at least one
# profile -- is answerable at all. Every target runs every step: a vendor
# whose matrix only ever ran on a laptop would ship a regression green.
for entry in "${TARGETS[@]}"; do
  vendor="${entry%%=*}"
  TARGET="${entry#*=}"
  step "matrix ($vendor)" \
    uv run python -m vendorfake.conformance --target "$TARGET" --transport inprocess --strict
  step "http ($vendor)" \
    uv run python -m vendorfake.conformance --target "$TARGET" --transport http --profile full
  step "plugin ($vendor)" \
    uv run python -m pytest --pyargs vendorfake.conformance -q \
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
