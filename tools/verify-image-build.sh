#!/usr/bin/env bash
# Verify the Dockerfile's build and runtime stages WITHOUT a container runtime.
#
# `docker build` is the real check and tools/self-test.sh runs it when a runtime
# is present. This script is the fallback for a machine that has none: it
# executes the same commands the Dockerfile runs, over the same file set the
# Dockerfile copies, and then assembles the runtime stage's layout by hand —
# including the node_modules symlink the runtime stage creates — and boots it.
#
# What it therefore proves: the copied file set is sufficient, `npm ci` resolves
# from the committed lockfile in a clean tree, the TypeScript build succeeds
# from source alone, the runtime layout starts, and the HEALTHCHECK command
# returns 0 against it.
#
# What it does NOT prove: that the image layers build under a container engine,
# or that node:22-alpine behaves like the host's Node. Both are stated in
# EVIDENCE.md rather than implied.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

BUILD="$WORK/build"
RUNTIME="$WORK/runtime"
mkdir -p "$BUILD" "$RUNTIME"

step() { printf '\n=== %s ===\n' "$1"; }

step "builder stage: copy the file set the Dockerfile copies"
# Mirrors the COPY lines in Dockerfile's builder stage.
cp "$ROOT/package.json" "$ROOT/package-lock.json" "$ROOT/tsconfig.base.json" "$ROOT/tsconfig.json" "$BUILD/"
mkdir -p "$BUILD/packages"
for pkg in core square; do
  # --exclude mirrors .dockerignore: build outputs and dependencies never ship.
  rsync -a --exclude node_modules --exclude dist --exclude '*.tsbuildinfo' "$ROOT/packages/$pkg" "$BUILD/packages/"
done
find "$BUILD" -type f | sed "s|$BUILD/||" | sort | sed 's/^/  /'

step "builder stage: npm ci --no-audit --no-fund --ignore-scripts"
(cd "$BUILD" && npm ci --no-audit --no-fund --ignore-scripts 2>&1 | tail -3)

step "builder stage: npm run build"
(cd "$BUILD" && npm run build)
echo "  built:"
ls -1 "$BUILD/packages/core/dist/index.js" "$BUILD/packages/square/dist/index.js" "$BUILD/packages/square/dist/bin/serve.js" | sed "s|$BUILD/|  |"

step "runtime stage: assemble the layout the Dockerfile assembles"
mkdir -p "$RUNTIME/packages/core" "$RUNTIME/packages/square"
cp "$BUILD/package.json" "$RUNTIME/package.json"
cp "$BUILD/packages/core/package.json" "$RUNTIME/packages/core/package.json"
cp -R "$BUILD/packages/core/dist" "$RUNTIME/packages/core/dist"
cp "$BUILD/packages/square/package.json" "$RUNTIME/packages/square/package.json"
cp -R "$BUILD/packages/square/dist" "$RUNTIME/packages/square/dist"
cp -R "$BUILD/packages/square/profiles" "$RUNTIME/packages/square/profiles"
cp -R "$BUILD/packages/square/seed" "$RUNTIME/packages/square/seed"
mkdir -p "$RUNTIME/node_modules/@vendor-unit"
ln -s ../../packages/core "$RUNTIME/node_modules/@vendor-unit/core"
echo "  runtime tree: $(find "$RUNTIME" -type f | wc -l | tr -d ' ') files, $(du -sh "$RUNTIME" | cut -f1) on disk"
echo "  node_modules holds only the workspace link:"
ls -l "$RUNTIME/node_modules/@vendor-unit/" | sed 's/^/    /'

step "runtime stage: start the CMD and run the HEALTHCHECK"
PORT="$(node -e 'const s=require("net").createServer();s.listen(0,()=>{console.log(s.address().port);s.close()})')"
# Output goes to a file, not the inherited stdout: a backgrounded process
# holding the script's pipe open would stop the caller from ever seeing EOF.
(cd "$RUNTIME" && UNIT_PORT="$PORT" UNIT_HOST=127.0.0.1 UNIT_PROFILE=full node packages/square/dist/bin/serve.js) \
  > "$WORK/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$WORK"' EXIT

for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:$PORT/__unit/health" >/dev/null 2>&1; then break; fi
  sleep 0.2
done

echo "  HEALTHCHECK command:"
UNIT_PORT="$PORT" node -e "fetch('http://127.0.0.1:'+(process.env.UNIT_PORT||8080)+'/__unit/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
echo "    exit 0"
echo "  GET /__unit/health:"
curl -fsS "http://127.0.0.1:$PORT/__unit/health" | sed 's/^/    /'
echo ""
echo "  GET /__unit/info (excerpt):"
curl -fsS "http://127.0.0.1:$PORT/__unit/info" | node -e "
let s='';process.stdin.on('data',c=>s+=c).on('end',()=>{const j=JSON.parse(s);
console.log('    vendor:      '+j.vendor.displayName+' ('+j.vendor.apiVersion+')');
console.log('    profile:     '+j.profile);
console.log('    capabilities:'+j.capabilities.map(c=>' '+c.name+(c.enabled?'':' (off)')).join(''));
console.log('    entities:    '+JSON.stringify(j.state.entities));
console.log('    digest:      '+j.state.digest.slice(0,32)+'…');});"

step "result"
echo "  server log:"
sed 's/^/    /' "$WORK/server.log"
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
echo "  the Dockerfile's build steps and runtime layout are verified natively."
