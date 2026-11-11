# Independent simulator acceptance

The default implementation must work without an installed Minecraft client/server, Microsoft login, game files, or AI API key. The tests and CI do not launch/download any game runtime.

Acceptance checks implemented in this repository:

- Known expected outcomes for permutation changes, event cancellation, entity properties, scheduling, collision, menu actions, recipes and animation sampling.
- Unsupported components, queries, JavaScript syntax/API calls and absent textures prevent an unqualified simulation result.
- Script execution uses a separate QuickJS process with a wall-clock deadline, heap/stack bounds, and promise/API-call budgets. Tests cover runaway execution, unhandled async errors, handled rejections, unavailable host globals/imports and dynamic-compilation blockers. This is not an independent security audit of QuickJS.
- Rendering changes when a texture permutation/animation changes. Repeated rendering at a fixed state remains deterministic.
- A real MCP client discovers tools, loads an add-on, runs an interaction, asserts the result, and receives a PNG. No game-launch tool is exposed by the default server.
- An evidence export preserves pack hashes and replay state. Existing evidence directories are not overwritten.
- Reports keep native compatibility unverified and accuracy unmeasured, even when every simulator assertion passes.
- Modern module linking, live bindings, classes, top-level await and generator jobs execute through the new runtime.
- Slot copies, stack metadata, overflow rollback, invalid handles, scoreboard mutations and damage/effect events have regression checks.
- Modal values, text input, pagination, busy dialogs and cancellation are tested through the simulator. A real MCP round trip opens a form, retrieves its PNG, submits it and observes a scoreboard update.
- Thin collision barriers cannot be crossed by a large movement delta; selection ray tests use selection geometry separately from collision geometry.
- V2 startup/before-event restrictions, late cancellation after await, waitTicks and per-pack declared imports are exercised with actual guest scripts.
- Item count/name/lore bounds, restricted setters, metadata-preserving use events and independent clone metadata have regression checks.
- Animation activation/re-entry clocks, stop/hold/loop behavior, keyframe discontinuities, entry/exit expressions, finished transitions and cross-fade weights have explicit expectations.
- Render-controller array selection changes pixels; ordered visibility and missing/unsupported shaders are checked. Near-plane intersection retains visible triangle fragments.
- An unexecuted state or unspawned entity still reports known unsupported nested fields and queries.
- The packaged 50-case documented-contract suite must have zero unexpected mismatches/errors and retain the declared gaps. Every case remains in its denominator; it never reports native accuracy. CLI exit codes and non-overwriting evidence output are tested.

Run `python -m unittest discover -v`, then the workshop scenario in the README. Windows and Linux CI execute the same checks. Passing these checks is a software release gate, **not** a native Bedrock acceptance gate.

Next fidelity milestones require independent reference fixtures for each target build: full block component schemas, complete Script API semantics, native movement observations, full resource/render controllers, custom JSON UI, native form layout/behavior, particles, vanilla assets under appropriate licensing, and platform graphics differences. Each subsystem should have declared coverage and an independent held-out regression corpus. Do not turn an untested implementation milestone into an accuracy score.
