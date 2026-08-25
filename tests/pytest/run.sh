#!/usr/bin/env bash
# Run the Python consumer tests in a reproducible virtual environment.
#
# Requires the unit to be built (npm run build) because the process backend
# spawns packages/square/dist/bin/serve.js. Set UNIT_TEST_BACKEND=docker to
# force Testcontainers, or UNIT_IMAGE=<tag> to reuse an image CI already built.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$ROOT/.venv"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

# --allow-existing so a repeated run reuses the environment instead of failing.
uv venv --python 3.12 --allow-existing "$VENV" >/dev/null
VIRTUAL_ENV="$VENV" uv pip install --quiet -r "$ROOT/tests/pytest/requirements.txt"

cd "$ROOT/tests/pytest"
exec "$VENV/bin/python" -m pytest "$@"
