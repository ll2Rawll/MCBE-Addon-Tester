# Scope and known limitations

## Implemented, not yet live-certified

The Python packaging, world serialization, log parsing, visual diff, report rules and MCP
protocol are tested using fixtures. Native Win32 structures/discovery are checked in Windows
CI. There is no live Minecraft client in the development environment; Windows input, capture,
import and the JavaScript observer need the acceptance run on the user's machine.

The example add-on is a fixture. Static validation does not establish that it renders,
responds correctly or persists in a real world. GameTest execution requires authored tests;
the tester does not automatically know the intended behavior of arbitrary content.

## Coverage boundaries

- Only Windows keyboard/mouse client input is implemented. Console, phone, controller,
  touch, accessibility and remote/locked-desktop scenarios require separate adapters/tests.
- Images capture visible screen pixels. An overlay, exclusive fullscreen capture problem,
  minimized window, different graphics mode or scaling can invalidate visual observations.
- There is no automatic audio recording, acoustic classifier, renderer frame-time capture
  or GPU benchmark. The AI can issue Minecraft profiler commands, but importing/interpreting
  profiler artifacts is not currently an integrated tool.
- Animation inspection is a timestamped sequence of PNGs, not continuous video or exhaustive
  coverage of every animation controller state. It includes screenshot overhead.
- No multiplayer client orchestration or network impairment simulation is included.
- No semantic proof of Molang/script correctness, exhaustive fuzzing, mutation generator,
  automatic fix-and-retest loop or complete add-on schema engine is implemented locally.
  Use Mojang validation and authored tests. AI clients can edit code with their own tools.
- The optional observer runs a separate behavior pack, samples state and may affect timing.
  It cannot expose every internal engine state. Missing or ambiguous observations must block.
- It supports unpacked add-ons and ordinary ZIP-based `.mcpack`/`.mcaddon` inputs, not encrypted
  Marketplace content, arbitrary archive formats or optimized `.brarchive` decoding. Currently
  manifests must have numeric three-part pack versions and behavior/resource module types.
- A `.mcworld` export with real LevelDB data is needed. Templates with active/embedded packs
  are rejected. No existing Minecraft folder is edited by the pack builder.
- Add-on assets can depend on platform caches, load order, settings and other packs. These
  combinations must be added to the test plan; they are not inferred exhaustively.
- The current release query resolves official BDS builds. Client store update verification is
  not implemented; reports state that explicitly. File-version comparison is exact, not a
  guessed conversion between marketing, package, engine and Script API versions.
- MCP is local stdio. GitHub/cloud access alone does not run it on your computer. A compatible
  local AI client is required; the project contains no hosted service, AI subscription or model.

## Reports and measurement

The tool preserves plan and evidence files with hashes and audit records, but it is not a
tamper-proof attestation system against someone who can edit all local files. AI review may
misinterpret a screenshot. A baseline can also be wrong. Results identify review provenance.

A finite test suite can cover every requirement explicitly listed in its plan. It cannot
prove every possible Minecraft state. A benchmark score only applies to the selected,
independently labelled examples. Known-good controls, defect diversity, held-out cases and
manual adjudication of unrelated failures matter. No measured 95% claim has been established.

## Recovery

After an interrupted/crashed operation, ensure the controller/external server is stopped
before removing `busy.lock`. Leave `STOP` in place until ready to resume. Start a fresh run
after changing a pack/build; failed external checks intentionally remain in the old report.
All run data is under the configured root. Imported disposable worlds remain in Minecraft
until you delete those named test worlds through its UI; the program does not silently delete them.
