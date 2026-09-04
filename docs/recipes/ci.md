# CI

Most consumer suites need nothing beyond installing vendorfake and running
pytest — [in-process bindings](../start/bindings.md) need no
extra CI service, no port, no container. Reach for the patterns below only
when a suite specifically needs a real URL or a non-Python consumer.

## The common case: nothing extra

```yaml
# .github/workflows/test.yml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --frozen
      - run: uv run pytest
```

If every test uses `unit()`/`async_unit()` (or the pytest plugin's
`vendorfake_unit` / `vendorfake_async_unit` fixtures), this is the whole
job — the fake exists only inside the test process for the length of one
`with` block. This repository's own CI
(`.github/workflows/self-test.yml`) is exactly this shape, matrixed over
the Python versions `requires-python` promises.

## When the suite needs a real URL

For `served()`, run it exactly like any other CLI your suite depends on —
no separate service, since `served()` starts and stops the child process
itself around the test that needs it. Nothing in the workflow changes.

## When the consumer is not Python

Build the image once and run it as a service container, or use
[docker compose](docker-compose.md) directly:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          repository: konyklabs/vendorfake
          path: vendorfake
      - run: docker build -t vendorfake ./vendorfake
      - run: docker run -d --rm -p 127.0.0.1:8080:8080 -e VENDORFAKE_VENDOR=square vendorfake
      - run: for i in $(seq 1 30); do curl -sf http://127.0.0.1:8080/__unit/health && break; sleep 1; done
      - name: Test
        env:
          SQUARE_BASE_URL: http://127.0.0.1:8080
        run: <your suite's command>
```

The suite under test needs nothing from vendorfake but a base URL; the
control plane (`/__unit/*`) is plain HTTP for the setup and teardown a test
needs.

## Caching

`uv sync --frozen` with `astral-sh/setup-uv`'s `enable-cache: true` (as this
repository's own workflow does) is the fast path for a Python consumer.
Docker layer caching (`docker/build-push-action`'s `cache-from`/`cache-to`,
or a registry-backed cache) is worth adding once the image build itself
becomes the slow step in a matrix job — not before.
