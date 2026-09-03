# Changelog

## Unreleased

<!-- Placeholder heading for release-please; the conductor folds this into the PR that lands stream C. -->

### Features

* **vendorfake:** discover profiles and routes by code — `registry.available_profiles`, `registry.routes`, `Driver.route_for`/`path_for`, and a per-vendor `paths` module of hand-written path constants kept honest against the router by `tests/unit/test_paths_drift.py` (konyklabs/roadmap#70)
* **vendorfake:** add `VendorDefinition.roles`, the neutral capability-role vocabulary (`auth`, `orders`, `webhooks`, `chaos`) every vendor maps to its own capability names, published at `GET /__unit/info` under `vendor.roles` (konyklabs/roadmap#70)
* **vendorfake:** `create_unit`/`unit()` accept `capabilities=[...]` — role names or a vendor's own capability names — and resolve to the narrowest shipped profile that is a superset, or `full` plus an absolute list when none qualifies; passing `profile=` and `capabilities=` together, or an empty `capabilities=[]`, is a `ValueError`. `GET /__unit/info` echoes the request back under `requested_capabilities` (konyklabs/roadmap#70)
* **cli:** add `--json`, accepted both before and after the subcommand (`vendorfake --json profiles` and `vendorfake profiles --json` are the same request; a no-op where a subcommand already prints JSON) and three subcommands — `vendorfake profiles`, `vendorfake routes`, `vendorfake faults` — plus `vendorfake vendors --json` (konyklabs/roadmap#70)
* **conformance:** add C34 (every vendor maps all four capability roles to a declared capability) and C35 (the profile-name contract holds: every vendor ships all six of `full`, `oauth-only`, `orders-only`, `no-chaos`, `no-faults`, `chaos-demo`, published at `GET /__unit/info` under `vendor.profiles`, and the profile a unit was built on honours what its name promises) (konyklabs/roadmap#70)

### Bug Fixes

* **vendorfake:** `unit()`'s `profile` argument now resolves the same three-step precedence `create_unit` documents — an explicit `profile=` argument, then `VENDORFAKE_PROFILE` in the `env=` mapping given to that call, then `full` — where v0.1.0 passed the literal string `"full"` and so never read `VENDORFAKE_PROFILE` from an `env=` mapping passed to `unit()` at all. **Behaviour change:** a caller who builds one `env` mapping for a whole test module and passes it to both `served()` (a real environment) and `unit()` will now see `unit()` honour `VENDORFAKE_PROFILE` in it too, where v0.1.0 silently ignored the variable for this call (konyklabs/roadmap#70)

### Documentation

* **vendorfake:** the profile-name contract, as it actually holds across all three shipped vendors: `orders-only` does NOT enable role `auth` (every shipped profile of that name promises "no OAuth dance, authenticate with a seeded token", pinned by each vendor's own tests) and `no-chaos` keeps role `chaos` enabled, switching off only `webhooks.chaos` (`no-faults` is the profile that switches off both). Documented in `src/vendorfake/conformance/checks/discovery.py` and the README's new "Discovering profiles and routes" section (konyklabs/roadmap#70)

## 0.1.0 (2026-09-01)


### Features

* **asgi:** add the FastAPI transport adapter as the only framework importer (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#10](https://github.com/konyklabs/vendorfake/issues/10)) ([cc138f2](https://github.com/konyklabs/vendorfake/commit/cc138f2af90305062a69e8366eb7914aa2114fd4))
* **clover:** add the Clover vendor foundation and OAuth v2 surface (konyklabs/roadmap[#34](https://github.com/konyklabs/vendorfake/issues/34)) ([#22](https://github.com/konyklabs/vendorfake/issues/22)) ([58f5e32](https://github.com/konyklabs/vendorfake/commit/58f5e32d62d6858989d3bb23b4eeec3169ffdd8d))
* **clover:** ship the Clover vendor — orders, inventory, customers, payments, webhooks, seed and profiles (konyklabs/roadmap[#34](https://github.com/konyklabs/vendorfake/issues/34)) ([#25](https://github.com/konyklabs/vendorfake/issues/25)) ([dc95be2](https://github.com/konyklabs/vendorfake/commit/dc95be236f664421945aace86310e47d81b746c4))
* **core:** add the control plane and the file-drop binding (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#9](https://github.com/konyklabs/vendorfake/issues/9)) ([41c8c8c](https://github.com/konyklabs/vendorfake/commit/41c8c8cfe8d9fd8e1e3c5fd3ec84fd58455afe56))
* **core:** add the router, the request pipeline and the in-process binding (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#7](https://github.com/konyklabs/vendorfake/issues/7)) ([69ff62e](https://github.com/konyklabs/vendorfake/commit/69ff62eb3c0475c0d127be9657fb4f73a1a1be59))
* **core:** add the webhook dispatcher with vendor-neutral delivery metadata (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#8](https://github.com/konyklabs/vendorfake/issues/8)) ([ca29fa2](https://github.com/konyklabs/vendorfake/commit/ca29fa235b3621dca597ed4d5140fbc689927c72))
* **square:** add the OAuth surface and retire the first test's xfail (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#12](https://github.com/konyklabs/vendorfake/issues/12)) ([b3d0c96](https://github.com/konyklabs/vendorfake/commit/b3d0c963779bbe4a7b81dab68e735b2af3dc9e5b))
* **square:** add the orders surface with sparse-update semantics (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#13](https://github.com/konyklabs/vendorfake/issues/13)) ([68917bb](https://github.com/konyklabs/vendorfake/commit/68917bb57f8bce8207d13b161799d2041f1db99e))
* **square:** add the vendor foundation, error table and order model (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#11](https://github.com/konyklabs/vendorfake/issues/11)) ([2e7c7a6](https://github.com/konyklabs/vendorfake/commit/2e7c7a635274491f9805ac59d3703f0901445bcc))
* **square:** close the consumer-driven endpoint gaps — merchants, catalog, payments, loyalty, inventory (konyklabs/roadmap[#36](https://github.com/konyklabs/vendorfake/issues/36)) ([#24](https://github.com/konyklabs/vendorfake/issues/24)) ([a6038d6](https://github.com/konyklabs/vendorfake/commit/a6038d69bb29375645b3237a4007239ee7c199a5))
* **testing:** ship the consumer path — fixtures, container and runnable examples (konyklabs/roadmap[#14](https://github.com/konyklabs/vendorfake/issues/14), [#17](https://github.com/konyklabs/vendorfake/issues/17)) ([#29](https://github.com/konyklabs/vendorfake/issues/29)) ([d701995](https://github.com/konyklabs/vendorfake/commit/d70199535ceec3d17c5541bdc77aa3754e99c572))
* **testing:** ship Toast's conformance target and seed, and correct drain's contract (konyklabs/roadmap[#14](https://github.com/konyklabs/vendorfake/issues/14)) ([#31](https://github.com/konyklabs/vendorfake/issues/31)) ([d2df5b6](https://github.com/konyklabs/vendorfake/commit/d2df5b692b1c58d17d1148f1a3195017a7170fdf))
* **toast:** add the Toast vendor — auth, menus, orders, payments, stock and webhooks (konyklabs/roadmap[#39](https://github.com/konyklabs/vendorfake/issues/39)) ([#30](https://github.com/konyklabs/vendorfake/issues/30)) ([eca40ad](https://github.com/konyklabs/vendorfake/commit/eca40addf551beed568e170b01987c2c894149ba))
* **vendorfake:** add the capability registry and deterministic chaos engine (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#6](https://github.com/konyklabs/vendorfake/issues/6)) ([33ccba0](https://github.com/konyklabs/vendorfake/commit/33ccba0f2b8f7d5813d5a570e05eddcce140294b))
* **vendorfake:** add the conformance suite, hardened against an adversarial pass (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#17](https://github.com/konyklabs/vendorfake/issues/17)) ([e56e42f](https://github.com/konyklabs/vendorfake/commit/e56e42f049501d89033562817ce1442246c4ed21))
* **vendorfake:** add the core foundation and journal-backed state engine (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#5](https://github.com/konyklabs/vendorfake/issues/5)) ([1ae5be0](https://github.com/konyklabs/vendorfake/commit/1ae5be03d4ece5bfae04ad1a02ef975c24f0c302))
* **vendorfake:** add the Square seed, profiles and end-to-end wiring (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#19](https://github.com/konyklabs/vendorfake/issues/19)) ([88ebaae](https://github.com/konyklabs/vendorfake/commit/88ebaae04c53ce7a5259632ef286b9764f8812b0))
* **vendorfake:** add the Square webhook surface, signer and retry schedule (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#14](https://github.com/konyklabs/vendorfake/issues/14)) ([a04ec17](https://github.com/konyklabs/vendorfake/commit/a04ec17c7477563cfe39048a2d0e2ea728d5e1b0))


### Bug Fixes

* **core:** expose repeated query parameters to handlers (konyklabs/roadmap[#37](https://github.com/konyklabs/vendorfake/issues/37)) ([#23](https://github.com/konyklabs/vendorfake/issues/23)) ([8c393d5](https://github.com/konyklabs/vendorfake/commit/8c393d57f9f2a298dfae5ffe5b73b96ff74b4a60))


### Documentation

* **readme:** add verified quickstart and release plumbing (konyklabs/roadmap[#33](https://github.com/konyklabs/vendorfake/issues/33)) ([#20](https://github.com/konyklabs/vendorfake/issues/20)) ([8615347](https://github.com/konyklabs/vendorfake/commit/86153478f1c03ff206d94c41110b9a25285e6422))
* **vendorfake:** add README with unofficial disclaimer and design pointers (konyklabs/roadmap[#7](https://github.com/konyklabs/vendorfake/issues/7)) ([#2](https://github.com/konyklabs/vendorfake/issues/2)) ([dd292c9](https://github.com/konyklabs/vendorfake/commit/dd292c934dafe919f58227a2fa39bb06c66c2f27))
