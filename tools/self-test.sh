#!/usr/bin/env bash
# The whole gate, in one command — template-owned.
#
# CI runs this and nothing else, so "green in CI" and "green on my machine" are
# the same claim. Every step runs even after a failure, because a summary that
# stops at the first red tells you least when you need it most.
#
#   --no-network   skip the freshness job (it reaches developer.squareup.com)
#   --skip-python  skip the pytest suite (it needs uv)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NO_NETWORK=0
SKIP_PYTHON=0
for arg in "$@"; do
  case "$arg" in
    --no-network) NO_NETWORK=1 ;;
    --skip-python) SKIP_PYTHON=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

STEP_NAMES=()
STEP_RESULTS=()
FAILED=0

run_step() {
  local name="$1"; shift
  printf '\n\033[1m=== %s ===\033[0m\n' "$name"
  if "$@"; then
    STEP_NAMES+=("$name"); STEP_RESULTS+=("pass")
  else
    local code=$?
    STEP_NAMES+=("$name"); STEP_RESULTS+=("FAIL (exit $code)")
    FAILED=1
  fi
}

have_container_runtime() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

install_deps() {
  if [ -d node_modules ]; then
    echo "node_modules present; skipping install"
    return 0
  fi
  npm ci --no-audit --no-fund
}

package_image() {
  if have_container_runtime; then
    echo "container runtime detected: building the image for real"
    docker build -t vendor-unit-square:test . || return 1
    docker run --rm -d --name vendor-unit-selftest -p 18080:8080 vendor-unit-square:test || return 1
    local ok=1
    for _ in $(seq 1 60); do
      if curl -fsS http://127.0.0.1:18080/__unit/health >/dev/null 2>&1; then ok=0; break; fi
      sleep 1
    done
    curl -fsS http://127.0.0.1:18080/__unit/health || true
    echo ""
    docker stop vendor-unit-selftest >/dev/null 2>&1 || true
    return $ok
  fi
  echo "no container runtime on this machine: verifying the Dockerfile's steps natively instead"
  echo "(see EVIDENCE.md, 'Packaging')"
  bash tools/verify-image-build.sh
}

run_step "install"                install_deps
run_step "build"                  npm run build
run_step "template purity"        node tools/template-check.mjs
run_step "conformance: full"      node packages/square/dist/bin/conformance.js full
run_step "conformance: oauth-only" node packages/square/dist/bin/conformance.js oauth-only
run_step "conformance: orders-only" node packages/square/dist/bin/conformance.js orders-only
run_step "unit tests (vitest)"    npx vitest run packages/square/test
run_step "integration (vitest)"   npx vitest run tests/vitest
if [ "$SKIP_PYTHON" -eq 0 ]; then
  # -s so the backend announcement reaches the log; a green run must say
  # whether it exercised the container or the spawned process.
  run_step "integration (pytest)" bash tests/pytest/run.sh -s
fi
run_step "packaging"              package_image
if [ "$NO_NETWORK" -eq 0 ]; then
  run_step "freshness"            node tools/spec-freshness.mjs
fi

printf '\n\033[1m=== self-test summary ===\033[0m\n'
for i in "${!STEP_NAMES[@]}"; do
  printf '  %-24s %s\n' "${STEP_NAMES[$i]}" "${STEP_RESULTS[$i]}"
done
printf '\n'

if [ "$FAILED" -ne 0 ]; then
  echo "self-test FAILED"
  exit 1
fi
echo "self-test passed"
