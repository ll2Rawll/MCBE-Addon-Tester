# Measuring documented contracts

Version 0.5 ships 58 original, explicit reference cases in [conformance.json](../src/mcbe_tester/assets/conformance.json). Each links to a primary documentation source. Expected values were authored from those contracts, not captured from the emulator's output. They cover Molang expressions, animation keyframe sampling, Script API v2 execution phases, deferred work, modal results, function context/ticks, command selectors and scores, JSON UI inheritance/layout, and UI bindings/input.

Current result: **55 matched, 0 mismatches, 3 unsupported**. The known gaps are `math.ease_in_quad`, Molang `math.random`, and Catmull–Rom animation interpolation. Their cases remain in the denominator. The suite deliberately includes gaps; deleting a case to improve its score is not a fidelity improvement.

These are project-authored, selected examples. They are neither independent native observations nor a representative sample of all Minecraft behavior, add-ons, graphics or devices. The reported 94.83% **documented-contract pass rate** is not Minecraft accuracy. `accuracy_percent` remains null; `native_reference_cases` remains zero. Passing this suite cannot establish 95–100% compatibility.

## Run it

```sh
bedrock-addon-tester conformance --out .mcbe-sim/contracts.json
```

The default exit code is 2 while unsupported cases remain. Exit 1 means a mismatch or error; exit 0 means all contracts match. CI can use `--allow-unsupported`: it changes only the exit policy, keeps the report incomplete and still fails on mismatches/errors. Regression tests separately require the exact declared set of three gaps.

The MCP tool is `run_conformance_suite()`. It needs no loaded add-on session. It runs in the same environment as the simulator, with no Minecraft, network requests or model API. The seven script cases need the usual installed Node/esbuild compiler. The other eight scenarios exercise commands and JSON UI without scripts. The real stdio protocol test invokes the suite and also types into a UI fixture, clicks its mapped button and requests its PNG.

Reports include the simulator version/profile, canonical suite SHA-256, source URLs, results by subsystem, total/matched/mismatch/unsupported/error counts, expected/actual values, and script assertions/findings. Every case is retained. A malformed whole suite is rejected; a malformed individual case produces an error row. Empty suites and duplicate IDs are rejected. Output files are created exclusively and never overwrite existing evidence.

## Supply separate expectations

`--suite path/to/references.json` or `run_conformance_suite(suite=...)` accepts this structure:

```json
{
  "schema_version": 1,
  "name": "My separately reviewed reference cases",
  "sources": {"math": "https://learn.microsoft.com/en-us/minecraft/creator/reference/content/molangreference/examples/molangconcepts/mathfunctions?view=minecraft-bedrock-stable"},
  "cases": [
    {
      "id": "degree-sine",
      "kind": "molang",
      "source": "math",
      "expression": "math.sin(90)",
      "expected": 1,
      "absolute_tolerance": 0.000001
    }
  ]
}
```

Supported kinds:

- `molang`: expression, expected value, optional context and absolute tolerance.
- `animation_channel`: value/keyframe map, time in seconds, expected three-component vector, optional default vector/tolerance.
- `scenario`: inline pack `files`, optional seed, and a standard scenario with named requirements, actions and independent assertions. A scenario without assertions cannot match.

Suites are limited to 2 MiB and 128 distinct cases, with at most 16 scenarios. A reference scenario has at most 100 steps and a 1,000-tick declared action budget; normal runtime limits also apply. No reference case executes a host command or reads an arbitrary external file. Pack scripts use the bounded QuickJS worker.

User-supplied expectations have unverified provenance. Metadata claiming an engine version or a native capture does not verify that claim. Those suites report counts, preserve mismatches and gaps, and never assign a native accuracy percentage. Version-specific independently collected references can be added without requiring the end user to install a game, but that dataset is not included here.
