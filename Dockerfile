# Vendor unit image — template-owned. A fork changes nothing here.
#
# The unit has ZERO runtime dependencies (the whole server is node: builtins),
# which is why the runtime stage can be assembled without running a package
# manager: the only thing node_modules needs is the workspace link, and that is
# a symlink. Result: a small image, a fast cold start, and no supply chain to
# audit on every fork.

FROM node:22-alpine AS builder
WORKDIR /app

# Manifests first so a source-only change reuses the install layer.
COPY package.json package-lock.json tsconfig.base.json tsconfig.json ./
COPY packages/core/package.json packages/core/package.json
COPY packages/square/package.json packages/square/package.json
RUN npm ci --no-audit --no-fund --ignore-scripts

COPY packages/core packages/core
COPY packages/square packages/square
RUN npm run build


FROM node:22-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    UNIT_PROFILE=full \
    UNIT_PORT=8080 \
    UNIT_HOST=0.0.0.0 \
    UNIT_LOG_LEVEL=info

COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/packages/core/package.json packages/core/package.json
COPY --from=builder /app/packages/core/dist packages/core/dist
COPY --from=builder /app/packages/square/package.json packages/square/package.json
COPY --from=builder /app/packages/square/dist packages/square/dist
COPY --from=builder /app/packages/square/profiles packages/square/profiles
COPY --from=builder /app/packages/square/seed packages/square/seed

RUN mkdir -p node_modules/@vendor-unit \
 && ln -s ../../packages/core node_modules/@vendor-unit/core

USER node
EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=2s --retries=10 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.UNIT_PORT||8080)+'/__unit/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "packages/square/dist/bin/serve.js"]
