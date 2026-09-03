"""vendorfake.agent.rules_template -- the Claude Code rules-file body.

FOR: the text ``vendorfake agent-setup`` writes to
``<dir>/.claude/rules/vendorfake.md``. Kept separate from
:mod:`vendorfake.agent.setup` so the words a consuming agent reads are
reviewable -- and diffable -- on their own, without the filesystem and
``.mcp.json``-merging code sitting next to them.

Claude Code loads a rules file only for a session touching a path its
``paths:`` frontmatter names (see
https://code.claude.com/docs/en/claude-code/memory as read on 2026-09-03),
which is why this is scoped to the consumer's test glob rather than written to
the project's own ``CLAUDE.md``: a session editing application code never
pays for a contract it cannot use.
"""

from __future__ import annotations

__all__ = ["DEFAULT_TESTS_GLOB", "render_rules_file"]

DEFAULT_TESTS_GLOB = "tests/**"

_BODY = """\
# vendorfake -- working contract for an agent writing or fixing tests

This file loads only for paths under `{tests_glob}`. It is the compact
version; `vendorfake explain <kind> <name>` answers a specific question
without opening the source, and the full contract is
https://github.com/konyklabs/vendorfake/blob/main/docs/for-agents.md (see
also https://github.com/konyklabs/vendorfake/blob/main/README.md for install
and quickstarts).

## Start a unit, one of four ways

Sync in-process -- the default. No socket; builds in milliseconds:

```python
from vendorfake.testing import unit


def test_something():
    with unit("square") as square:
        resp = square.client.post("/v2/orders", headers=square.seed.auth, json={{...}})
```

Async in-process, for an `async def` test or fixture:

```python
from vendorfake.testing import async_unit


async def test_something():
    async with async_unit("square") as square:
        resp = await square.async_client.post("/v2/orders", headers=square.seed.auth, json={{...}})
```

Served -- a real server in a child process, for a service under test that
needs a base URL rather than an in-process client:

```python
from vendorfake.testing import served


def test_something():
    with served("square") as square:
        configure_the_service_under_test(base_url=square.base_url)
```

Container -- one image, every vendor; the vendor is chosen at container start
by `VENDORFAKE_VENDOR`:

```sh
docker run --rm -p 127.0.0.1:8080:8080 -e VENDORFAKE_VENDOR=square vendorfake
```

`vendorfake.testing.serve_in_thread(started)` puts a real server on a
background thread in front of a unit `unit()` already built, for a test that
needs both an in-process driver and a URL onto the *same* state.

## Or via the pytest plugin

Installing vendorfake also registers a marker and three fixtures --
`vendorfake_unit`, `vendorfake_async_unit`, `vendorfake_webhook_receiver` --
for a suite that would rather ask for a unit as a fixture than write a `with`
block. Same arguments as `unit()`, read off the marker:

```python
import pytest


@pytest.mark.vendorfake("square", profile="oauth-only")
def test_something(vendorfake_unit):
    resp = vendorfake_unit.client.post("/v2/orders", headers=vendorfake_unit.seed.auth, json={{...}})
```

`vendorfake_async_unit` is the same, for an `async def` test;
`vendorfake_webhook_receiver` gives the other half of a webhook test, a real
HTTP receiver on loopback. Requesting a fixture on a test with no
`@pytest.mark.vendorfake(...)` is a loud failure at setup, not a skip.

## Vocabulary

- **vendor** -- which API is faked: `square`, `clover`, `toast`
  (`vendorfake vendors` lists what is installed).
- **profile** -- a named JSON document choosing which capabilities are on,
  the seed, the clock mode, retry timing, and more (`vendorfake profiles
  --vendor <name>` lists what a vendor ships; `full` is the default).
- **capability** -- one named slice of a vendor's surface (`orders`,
  `webhooks.chaos`, ...) a profile can switch on or off; a disabled one
  answers a documented refusal rather than 404. **Role** is the
  vendor-neutral spelling four roles map to (`auth`, `orders`, `webhooks`,
  `chaos`), so a request for `capabilities=["orders"]` works whichever
  vendor a parametrized test is currently running against.
- **seed** -- the scenario a unit starts with: ids, credentials, an order or
  two, already there, no setup call needed. `driver.seed` (a `SquareSeed`,
  `CloverSeed` or `ToastSeed`) is where the fields live;
  `vendorfake.testing.Seed` is the structural type all three share.
- **driver** -- the object `unit()` / `async_unit()` / `served()` yield:
  `.client` (or `.async_client`) speaks the vendor surface, `.seed` names the
  scenario, and its methods (`subscribe`, `drain`, `reset`, the chaos-rule
  helpers) wrap the `/__unit/*` control plane so a test says what it means
  instead of which route does it.
- **journal** -- the append-only record of committed *mutations*
  (`GET /__unit/journal`). A read, or a request that was refused, leaves no
  entry -- that is what the request log is for.
- **request log** -- every request the unit handled, matched or not, 2xx or
  4xx (`driver.requests(...)`, `GET /__unit/requests`). Read this, not the
  journal, when a call never committed anything.
- **clock** -- real or virtual (a profile's `clock.mode`, or
  `VENDORFAKE_CLOCK`). Virtual time only moves on `POST
  /__unit/clock/advance`, which is how a token-expiry or a webhook-retry test
  runs without a real sleep.
- **chaos rule** -- a document saying which requests (by route, method,
  header, event type, ...) get which fault, how often, deterministically (a
  seeded RNG), so the same suite fires the same faults on every run.
- **fault** -- one specific failure a chaos rule can arm: `rate_limit`,
  `timeout`, `server_error`, `webhook.duplicate`, a handful of
  transport-fidelity faults, and more (`vendorfake faults`, or `vendorfake
  explain fault <name>`, lists every one with its parameters).
- **provenance** -- on nearly everything this project asserts, a tag saying
  whether the behaviour is `documented` (the vendor's own docs say so) or a
  `judgment`/`transport` call this project made because no vendor page
  settles it. Read it before treating an assertion as a fact about the real
  vendor rather than about this fake.

## When a request matches nothing

In process, the default is a raised `vendorfake.testing.UnmatchedRequest` (an
`AssertionError`, so pytest reports it as a failure rather than an error)
naming the closest routes -- read the message, it says what the unit *does*
serve. Served and container units never raise; they answer as the vendor
would and set `Vendorfake-Near-Miss`, a compact JSON array of the same
candidates (`route`, `score`, `operation_id`), on the response.
`GET /__unit/routes` (or `vendorfake explain route <operation_id>`) is the
ground truth for what a profile actually serves.

## The evidence habit

Paste the command and its output; do not write "tests pass". `tools/self-test.sh`
is the one command this project's own contributors run and is exactly what a
consumer's CI should run against its own suite -- see this project's own
`CLAUDE.md` (imported via `@AGENTS.md`) if working inside vendorfake itself.

## What not to import

`vendorfake.asgi` and `vendorfake.core` are internal. A test imports
`vendorfake.testing`, `vendorfake.registry`, or a vendor's public surface
(`vendorfake.square.signer`, ...) -- never those two. `vendorfake explain
<route|fault|profile|error|header> <name>` answers "what is this" without
opening the source at all.
"""


def render_rules_file(tests_glob: str = DEFAULT_TESTS_GLOB) -> str:
    """The rules file's full text, frontmatter included.

    ``paths:`` scopes the file to ``tests_glob`` so an agent editing
    application code never loads a contract about a test double it is not
    touching. The body is a fixed template parameterised only by the glob --
    everything else in it is a fact about vendorfake itself, not about the
    consumer, so nothing else varies per invocation.
    """
    body = _BODY.format(tests_glob=tests_glob)
    return f'---\npaths:\n  - "{tests_glob}"\n---\n\n{body}'
