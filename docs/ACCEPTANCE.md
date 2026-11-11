# Independent simulator acceptance

The default implementation must work without an installed Minecraft client/server, Microsoft login, game files, or AI API key. The tests and CI do not launch/download any game runtime.

Acceptance checks implemented in this repository:

- Known expected outcomes for permutation changes, event cancellation, entity properties, scheduling, collision, menu actions, recipes and animation sampling.
- Unsupported components, queries, JavaScript syntax/API calls and absent textures prevent an unqualified simulation result.
- Infinite script loops terminate at an operation budget; script reflection and host imports cannot access the host runtime.
- Rendering changes when a texture permutation/animation changes. Repeated rendering at a fixed state remains deterministic.
- A real MCP client discovers tools, loads an add-on, runs an interaction, asserts the result, and receives a PNG. No game-launch tool is exposed by the default server.
- An evidence export preserves pack hashes and replay state. Existing evidence directories are not overwritten.
- Reports keep native compatibility unverified and accuracy unmeasured, even when every simulator assertion passes.

Run `python -m unittest discover -v`, then the workshop scenario in the README. Windows and Linux CI execute the same checks. Passing these checks is a software release gate, **not** a native Bedrock acceptance gate.

Next fidelity milestones require independent reference fixtures for each target build: full block component schemas, complete Script API semantics, native movement observations, full resource/render controllers, custom JSON UI, forms, particles, vanilla assets under appropriate licensing, and platform graphics differences. Each subsystem should have declared coverage and an independent held-out regression corpus. Do not turn an untested implementation milestone into an accuracy score.
