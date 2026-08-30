# syntax=docker/dockerfile:1
#
# One image, every installed vendor. Which one it serves is decided at run
# time by VENDORFAKE_VENDOR (or `serve --vendor <name>` as the command), never
# at build time: nothing below names a vendor, and tools/verify_image_build.sh
# fails if that ever changes. Adding a vendor is adding a package under
# src/vendorfake/ -- this file does not change.
#
# The build stage produces a WHEEL and installs that, rather than copying the
# source tree into the runtime. A data file that hatch does not package -- a
# profile, a seed document -- is then missing from the image the same way it
# would be missing from `pip install`, and the healthcheck says so, instead
# of the container working from a source tree that pip never ships.

FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim AS build

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
RUN uv build --wheel --out-dir /dist \
 && uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-cache /dist/*.whl

FROM python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="vendorfake" \
      org.opencontainers.image.description="High-fidelity fakes of third-party vendor APIs: stateful flows, signed webhooks, deterministic fault injection." \
      org.opencontainers.image.source="https://github.com/konyklabs/vendorfake" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN groupadd --system --gid 10001 vendorfake \
 && useradd --system --uid 10001 --gid vendorfake --home-dir /home/vendorfake --create-home vendorfake

COPY --from=build --chown=vendorfake:vendorfake /opt/venv /opt/venv

# Loopback is the CLI's default and the right one on a laptop; inside a
# container it would make the port unreachable from the host, so this is the
# one place the bind address is widened deliberately (see asgi/serve.py).
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    VENDORFAKE_HOST=0.0.0.0 \
    VENDORFAKE_PORT=8080

USER vendorfake
WORKDIR /home/vendorfake
EXPOSE 8080

# /__unit/info is served by every vendor on every profile and is built from
# the unit's own tables, so a 200 here means the unit constructed, hydrated
# its seed and is answering -- not merely that a socket is open.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('VENDORFAKE_PORT', '8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/__unit/info', timeout=2).status == 200 else 1)"]

ENTRYPOINT ["vendorfake"]
CMD ["serve"]
