# Simulation profile v1 boundaries

This is a partial independent emulator. It does **not** fully test every Bedrock add-on, recreate all graphics/menus/controls, or demonstrate 95% compatibility. Its built-in examples establish software conformance to the chosen model, not agreement with Minecraft.

## Runtime

The bounded world has a flat floor at y=0, coordinates within ±128, at most 2,048 blocks, 128 entities, 20,000 ticks, and 2,000 actions. Four MCP sessions may coexist. Sessions live in memory; export before closing. No world generation, dimensions other than the simulator overworld, fluids, redstone, networking, vanilla AI, full combat, persistence in LevelDB, resource-pack stacking, or platform-specific behavior is modeled.

Movement uses approximate speed, gravity, substeps, and axis-aligned collision. It does not model sneaking over edges, swimming, step-up, complex selection shapes, climbing, sprint timing, or engine float rounding. Semantic `place`, `break`, and `interact` target explicit positions for tests; they do not enforce native reach or survival inventory consumption. Crosshair clicks use a simplified ray. Mining is immediate and respects only the boolean unbreakable setting; mining duration is not simulated. Inventory stores item counts, not full slot/stack/NBT semantics. Recipe tags, custom stations and ingredient data variants are unsupported.

## Scripts

`esprima` parses ES2017 source into an AST; our interpreter evaluates only explicitly implemented nodes. There is no host `eval`, shell, Node runtime, network, filesystem API, or Python object reflection available to scripts. Operation/call/collection limits prevent common runaway inputs; this is a prototype interpreter, not an independently audited security sandbox. Avoid exposing a process with local file tools directly to untrusted remote users.

Implemented API objects: a subset of `world`, `system`, `BlockPermutation`, and `ItemStack`. Timers, selected events, scalar dynamic properties, block access/permutations, entity spawn/event/remove, and two custom block hooks (`onPlace`, `onPlayerInteract`) are modeled. Vector handles and component objects are snapshots, not all native live objects. API privilege restrictions, lifecycle ordering and all API-version differences are not reproduced. JavaScript coercion, scopes and hoisting are partial. Unsupported syntax/API use blocks the report when detected. Async/await, generators, classes, promises/forms, eval, the full standard library, server-ui, commands, scoreboard and most components are not implemented.

## Rendering and menus

CPU-rasterized textured cuboids, bone transforms, basic UVs, linear keyframes and limited animation controllers are previews. They do not implement native camera/render math, all texture orientation/version rules, native clipping, culling, translucency sorting, lighting, shaders, PBR, RTX/Vibrant Visuals, particles, sound, attachables or render-controller selection. Animation controller blend transitions and entry/exit side effects are unsupported. Some cube/UV mirroring details differ. The floor and interface are original simulator visuals.

The included inventory/pause screens can be operated by the AI. Bedrock's actual menus, custom JSON UI, screen bindings, controller/touch layouts, accessibility behavior and Script API forms are not implemented. Missing textures produce a visibly marked placeholder and an unresolved finding; no vanilla assets are bundled.

## Validation and results

The content scanner is not a full Bedrock schema implementation. Unknown top-level files/components are reported, but detection of unsupported nested fields is incomplete. A passed simulated assertion means exactly that; no native compatibility claim follows, even if the scanner found no gaps. Native performance, device compatibility and latest-build behavior are always unverified. `--target-version` records an intended target; it does not select a native engine.

Fixture setters and assertions can expose mistakes but cannot derive an author's intent. Auto-enumerating every block state does not establish that state behavior is correct. Images cannot decide whether a design is intended. A replay reproduces this model's result, not a native engine result. Image comparison uses a supplied reference and cannot validate all visual contexts.

A useful compatibility percentage would need an independent, representative, version-specific corpus with native observations or another justified oracle. No such corpus is included. We therefore publish no percentage; `accuracy_percent` is null, including in passing reports. Unsupported cases must remain in the denominator of any future benchmark.
