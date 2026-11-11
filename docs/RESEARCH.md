# Independent Bedrock simulation research

Research checked 2026-11-11. The product requirement is a simulator with no game runtime and no AI API. This document distinguishes published specifications, third-party implementations, implemented behavior, and unmeasured fidelity. Earlier real-client research is preserved in [LEGACY-RESEARCH.md](LEGACY-RESEARCH.md); that workflow is not the active design.

## Findings and design decisions

| Primary source | What it establishes | Decision for this project |
| --- | --- | --- |
| [Microsoft Molang syntax](https://learn.microsoft.com/en-us/minecraft/creator/documents/molang/syntax-guide?view=minecraft-bedrock-stable) and [query reference](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/molangreference/examples/molangconcepts/queryfunctions) | Expressions, variables, conditions and named context queries are specified publicly | Implement a parser and explicit query subset; unknown queries block rather than returning an invented default |
| [Microsoft block geometry](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/blockreference/examples/blockcomponents/minecraftblock_geometry?view=minecraft-bedrock-stable) | Geometry selection has version-dependent rules and optional properties | Render supported pack cuboids; report options not implemented. Do not equate a preview with native graphics |
| [Microsoft animation controllers](https://learn.microsoft.com/en-us/minecraft/creator/documents/animations/animationcontroller?view=minecraft-bedrock-stable) and [animation overview](https://learn.microsoft.com/en-us/minecraft/creator/documents/animations/animationsoverview) | Bones, channels, timed keyframes, expressions and ordered transitions have documented semantics | Implement linear sampling and a bounded controller subset. Blend transitions, all queries and side effects remain gaps |
| [Script V2 overview](https://learn.microsoft.com/en-us/minecraft/creator/documents/scriptingv2.0.0overview) and [custom components](https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/custom-components) | Startup registration and custom-component dispatch use defined APIs; lifecycle behavior changes across API versions | Implement selected startup/hooks/timers explicitly, label scheduling and privileges approximate |
| [System run guide](https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/system-run-guide) and [execution privileges](https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/execution-privilege?view=minecraft-bedrock-stable) | Tick timing and permitted API use depend on execution phase | Our timer is an independent model, not proof of exact engine ordering or privilege compliance |
| [JSON UI definitions](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/jsonuireference/examples/jsonuicomponents/ui_defs?view=minecraft-bedrock-stable) | Loading UI files is only one part of a larger screen/binding system | Custom JSON UI remains unsupported; simulator menus are clearly distinguished |
| [Mojang Creator Tools](https://github.com/Mojang/minecraft-creator-tools) and [validation rules](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/mctoolsvalreference/validationrulesindex?view=minecraft-bedrock-stable) | Official static validation is available | Useful independent validation layer; it does not supply a full client renderer or gameplay engine. The simulator does not invoke MCT's BDS autotest |
| [BedSim source and documentation](https://github.com/oomph-ac/bedsim) | A standalone movement model can cover substantial mechanics, with provider requirements and documented deviations | Supports feasibility of subsystem simulation. It is not an entire add-on runtime and is not integrated into v0.2 |
| [Cinnabar source and documentation](https://github.com/cinnabar-mc/cinnabar) | An independent Rust client exists; its README says player worlds use BDS | It does not meet this request's no-BDS runtime requirement as a complete solution. Its license is PolyForm Noncommercial, so no source is copied into this MIT project |
| [MCP stdio transport](https://ts.sdk.modelcontextprotocol.io/v2/serving/stdio) and [client integration](https://ts.sdk.modelcontextprotocol.io/v2/get-started/real-host) | An existing assistant can call tools in a separate local process | Expose simulation operations and PNGs through MCP; the tester itself makes no AI calls |
| [OpenAI custom MCP connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) | ChatGPT connects to remote MCP servers; local processes require a supported connection path | Do not claim a GitHub repository or stdio process is automatically a callable ChatGPT web tool |

## Proposed route to broader fidelity

The architecture is an independent, executable model of the add-on surface: pack loader → feature inventory → virtual state and script interpreter → animation/renderer → input/menu model → assertions and evidence. The host AI selects scenarios, inspects output and interprets the report. Deterministic traces allow the same inputs to be rechecked after a model improvement.

This makes simulation without Minecraft possible for implemented features. It does **not** make native compatibility automatic. The public content specifications omit some engine behavior; implementing one subsystem does not establish all its interactions. A simulator can reproduce its own mistake consistently.

A credible path toward high measured coverage requires versioned feature contracts, independently sourced reference observations, adversarial add-ons, held-out tests, and per-subsystem mismatch reports. Reference datasets can be consumed without installing the game on an end user's machine. This project does not yet include a native reference corpus or a measured compatibility percentage. No empirical basis for “95% of all add-ons” was found in this research.

Useful next increments are native JSON UI/bindings and forms, broader Script API semantics and scheduling, full render-controller evaluation, more complete movement physics, and systematic tests of component combinations. They remain work to implement and calibrate, not features of the current release.

## Version handling

The earlier metadata check on 2026-11-11 returned stable BDS `1.26.63.1` and preview `1.26.70.24` from [Minecraft's official download metadata](https://net-secondary.web.minecraft-services.net/api/v1.0/download/links). That is a server-build observation, not proof of the Windows client's version or simulator support. Marketing, binary, content-format, and Script API version numbers are distinct.

The simulator currently has one named profile, `independent-bedrock-subset-v1`. `target_version` records the user's intended build. It does not select engine binaries, claim latest-version parity, or update component semantics automatically.

## Verification performed

The included original workshop pack runs with no game files. The scenario exercises ten explicit expectations and produces PNGs, assertions and a replay. Automated tests include a real MCP protocol round trip, missing assets, unsupported APIs, runaway scripts, state changes, image changes and deterministic replay. The 60-test suite passed on Linux with one Windows-only check skipped. Mojang Creator Tools 0.21.0 static validation of the workshop fixture returned zero errors, zero warnings and four format-version recommendations. These are simulator/software and static checks. Native gameplay, GPU fidelity and complete UI compatibility were not verified.
