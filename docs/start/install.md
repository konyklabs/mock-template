# Install

Python 3.11 or newer. Not on PyPI yet — install from the tag:

```sh
pip install "vendorfake[serve] @ git+https://github.com/konyklabs/vendorfake@v0.5.0"  # x-release-please-version
# or, in a uv project:
uv add "vendorfake[serve] @ git+https://github.com/konyklabs/vendorfake@v0.5.0"  # x-release-please-version

vendorfake vendors            # -> clover, lightspeed, square, toast
vendorfake serve --vendor square
```

The `serve` extra pulls in the ASGI stack (`fastapi`, `uvicorn`) that `vendorfake
serve` and the served/container bindings need; the in-process bindings
(`unit()`, `async_unit()`) never import it, so a plain `pip install vendorfake`
is enough for a test suite that only uses those. The extra exists from 0.6.0;
at an earlier tag the ASGI stack installs unconditionally and pip warns that
the extra does not exist, which is harmless. Drop the `@v0.5.0` <!-- x-release-please-version --> to track
`main`. From a checkout of this repository: `uv sync && uv run vendorfake
serve --vendor square` (`uv sync`'s `dev` group carries the extra's packages
too, so nothing extra to ask for there).

The pin lines above carry a release-please marker (`x-release-please-version`)
and `release-please-config.json` lists the three pages as extra files, so a
release bumps them; `tests/unit/test_docs_pins.py` asserts every pin equals
`vendorfake.__version__`.

## Pinning a commit instead of a tag

A tag is the only pin whose version string means what it says. Release-please
bumps `pyproject.toml` at release time, so a commit on `main` between two tags
still reports the *previous* release from `importlib.metadata.version("vendorfake")`
and `vendorfake.__version__` — `8b199f1`, the 0.2 integration head, said
`0.1.0` on a tree with three breaking changes. If you pin a commit for early
access, the discriminator is the changelog's `Unreleased` heading at that
commit, not the version; and a runtime branch on `__version__` (say, on where
the `unit_error` sidecar rides) will take the wrong arm. Releases are cut
often enough that a tag is usually days behind `main` at most; prefer it.

## As a container

One image serves every vendor; the vendor is chosen when the container
starts, by `VENDORFAKE_VENDOR`, and the profile by `VENDORFAKE_PROFILE`
(default `full`). Nothing is published to a registry yet, so build it:

```sh
docker build -t vendorfake .

docker run --rm -p 127.0.0.1:8080:8080 -e VENDORFAKE_VENDOR=square vendorfake
docker run --rm -p 127.0.0.1:8081:8080 -e VENDORFAKE_VENDOR=clover -e VENDORFAKE_PROFILE=chaos-demo vendorfake
# same unit as the first line, as CLI arguments instead of environment:
docker run --rm -p 127.0.0.1:8080:8080 vendorfake serve --vendor square

curl -s http://localhost:8080/__unit/health
# -> {"status":"ok","vendor":"square","profile":"full","uptime_ms":221,"version":"0.5.0"}
```

Publish the port on loopback (`-p 127.0.0.1:...`), as above: the control
plane is deliberately unauthenticated — it hands out the seeded credentials
and will POST webhooks at any URL it is told — so a fake published to the
network is an outbound-request primitive for anyone who can route to your
host. When another container or machine must reach it deliberately, put both
on a Docker network (or use Testcontainers, as the [docker compose
section](bindings.md#docker-compose) does) rather than widening the host
bind.

The image runs as a non-root user, listens on 8080, and carries a
`HEALTHCHECK` on `/__unit/info` so `docker ps` (and any orchestrator) reports
`healthy` only once the unit has hydrated its seed and is answering. With no
vendor set it refuses and lists what it found — it never guesses.
`tools/verify_image_build.sh` is the build's own proof: it builds, serves each
vendor, waits for the healthcheck, and fails if the Dockerfile names a vendor.
