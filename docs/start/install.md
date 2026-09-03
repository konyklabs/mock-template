# Install

Python 3.11 or newer. Not on PyPI yet — install from the tag:

```sh
pip install "vendorfake @ git+https://github.com/konyklabs/vendorfake@v0.1.0"
# or, in a uv project:
uv add "vendorfake @ git+https://github.com/konyklabs/vendorfake@v0.1.0"

vendorfake vendors            # -> clover, square, toast
vendorfake serve --vendor square
```

Drop the `@v0.1.0` to track `main`. From a checkout of this repository:
`uv sync && uv run vendorfake serve --vendor square`.

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
# -> {"status":"ok","vendor":"square","profile":"full","uptime_ms":221,"framework_answered":0}
```

Publish the port on loopback (`-p 127.0.0.1:...`), as above: the control
plane is deliberately unauthenticated — it hands out the seeded credentials
and will POST webhooks at any URL it is told — so a fake published to the
network is an outbound-request primitive for anyone who can route to your
host. When another container or machine must reach it deliberately, put both
on a Docker network (or use Testcontainers, as the [docker compose
recipe](../recipes/docker-compose.md) does) rather than widening the host
bind.

The image runs as a non-root user, listens on 8080, and carries a
`HEALTHCHECK` on `/__unit/info` so `docker ps` (and any orchestrator) reports
`healthy` only once the unit has hydrated its seed and is answering. With no
vendor set it refuses and lists what it found — it never guesses.
`tools/verify_image_build.sh` is the build's own proof: it builds, serves each
vendor, waits for the healthcheck, and fails if the Dockerfile names a vendor.
