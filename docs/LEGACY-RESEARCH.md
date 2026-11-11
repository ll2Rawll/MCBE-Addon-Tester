# Research and design decision

Research date: **11 November 2026**. Source review and code inspection informed this
implementation; they are not evidence that the tester has completed live game acceptance.

## Use Minecraft's engine and client

A small independent simulator would need to reproduce version-specific add-on loading,
script execution, entity AI, tick scheduling, Molang, rendering, UI, inputs and platform
behavior. Any inaccurate part becomes a blind spot. Reusing the official server and actual
client avoids having to invent those semantics. This is an engineering conclusion from
the mechanisms below, not a published measured accuracy claim.

Mojang's **GameTest** already defines small in-world environments with starting conditions,
execution time and assertions. It is the closest existing equivalent to the requested
mini-simulation, except it runs in Minecraft itself. The official workflow requires cheats
and the Beta APIs experiment. Tests must describe what is supposed to happen.
[Microsoft: GameTest introduction](https://learn.microsoft.com/en-us/minecraft/creator/documents/gametestgettingstarted?view=minecraft-bedrock-stable)
and [official sample tests](https://github.com/microsoft/minecraft-gametests).

The official **Bedrock Dedicated Server** is available for Windows and Ubuntu, and supports
scripting. It supplies real server-side execution without the graphical client. Server-specific
experimental APIs are not automatically available in Realms.
[Official BDS download](https://www.minecraft.net/en-us/download/server/bedrock) ·
[Microsoft: BDS scripting](https://learn.microsoft.com/en-us/minecraft/creator/documents/bedrockserver/scripting?view=minecraft-bedrock-stable).

## Existing tools: useful, with specific limits

| Tool/mechanism | What was verified from its documentation/source | Consequence for this project |
| --- | --- | --- |
| Mojang Minecraft Creator Tools | CLI validation, MCP integration, packaging, test-world deployment | Reuse its static validator; do not build a competing partial schema database |
| MCT validation rules | Manifest, versions, scripts, textures, references, paths and other checks | Validation can catch file faults before engine execution |
| MCT `autotest` source | Deploys to BDS, waits for startup, allows 15 seconds, captures output and stops | Its name is not proof of exhaustive gameplay/UI testing |
| `@bedrock-core/bds-runner` | Runs explicit GameTest tags, reports per-test verdicts, expected count, script errors and infrastructure failures | Adapter pins version and test count; unknown/missing reports block |
| GameTest simulated players | APIs cover movement and interactions, with documented event differences | Test real-client interactions separately |
| Windows input automation | Existing Bedrock proof of concept uses Python/keyboard/mouse | Native Windows input is feasible, but our adapter still needs live acceptance |
| `mcpelauncher-agent` | Community bridge drives a real client through a modified launcher; its README notes GPU/GUI requirements | Evidence that AI real-client control exists; not a validated Windows 10 solution |
| PocketMine-MP | Its own documentation says behavior packs are unsupported | Not a substitute runtime for validating arbitrary Bedrock add-ons |

Primary references:

- [Mojang Creator Tools CLI README](https://github.com/Mojang/minecraft-creator-tools/blob/main/app/jsnode/README.md)
- [MCT validation rules](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/mctoolsvalreference/validationrulesindex?view=minecraft-bedrock-stable)
- [MCT AutotestCommand implementation](https://github.com/Mojang/minecraft-creator-tools/blob/main/app/src/cli/commands/content/AutotestCommand.ts)
- [BDS runner documentation](https://bedrock-core.drav.dev/docs/bds-runner/)
- [BDS runner command/error semantics](https://bedrock-core.drav.dev/docs/bds-runner/cli/)
- [BDS runner settings](https://bedrock-core.drav.dev/docs/bds-runner/config/)
- [Microsoft: SimulatedPlayer](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-gametest/simulatedplayer?view=minecraft-bedrock-experimental)
- [Windows Bedrock input proof of concept](https://github.com/lefkovitzj/Minecraft-BE-Input-Scripting)
- [mcpelauncher-agent's own README](https://github.com/bedrock-mc/mcpelauncher-agent)
- [PocketMine's behavior-pack limitation](https://doc.pmmp.io/en/rtfd/resourcepacks.html)

## Details that prevent misleading results

**Simulated input is not identical to real input.** Microsoft explicitly warns that some
events, including item-use events, may behave differently for simulated players. Its
`attackEntity` method can also attack without normal distance/line-of-sight constraints.
Use real Windows input for acceptance of player interactions; use simulation for the
assertions its semantics actually support. See the SimulatedPlayer reference above.

**Runner defaults affect the experiment.** The inspected BDS runner defaults to Peaceful,
Creative and modified watchdog behavior. These can hide relevant mob and performance
problems. This adapter chooses Normal difficulty and standard watchdog thresholds, while
the final client pass must retain the add-on's intended settings. See the runner settings above.

**Beta tests alter the target environment.** GameTest's experimental API does not establish
that an add-on also works with experiments disabled. Keep a separate ordinary client
acceptance pass, and do not mix Preview compatibility with retail compatibility.
[Microsoft: GameTest module status](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-gametest/minecraft-server-gametest?view=minecraft-bedrock-experimental).

**Client storage changed.** GDK builds use new Windows paths; hard-coding only the old UWP
folder misses logs and packs. The built-in log discovery covers the retail GDK and legacy
locations, with explicit overrides available.
[GDK migration](https://learn.microsoft.com/en-us/minecraft/creator/documents/gdkpcprojectfolder?view=minecraft-bedrock-stable)
and [current logging/profiler documentation](https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/debugging-scripts?view=minecraft-bedrock-stable).

**Game and API version numbers differ.** Use an exact installed game build plus the manifest's
actual API dependencies. Do not set every `format_version` to a marketing release number.
[Microsoft: Script module versioning](https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/versioning?view=minecraft-bedrock-stable).

## Latest-version finding

The official download-links service returned **BDS stable 1.26.63.1** and **Preview
1.26.70.24** during research on 11 November 2026:
[official download metadata](https://net-secondary.web.minecraft-services.net/api/v1.0/download/links).
The release-changelog index was still headed by 26.62 when checked, illustrating why a
cached article alone is insufficient for build selection.
[Official changelog index](https://feedback.minecraft.net/hc/en-us/sections/360001186971-Release-Changelogs).

These are dated findings, not forever-current constants. `mcbe-test versions` re-queries
the official service and fails visibly if the response cannot be understood. It does not
download anything or infer Windows client file-version equivalence. The current report
explicitly retains `latest_release_verified: false` because no client-store update check
has been automated.

## What a defensible 95% target would require

Define the supported scope first: specific versions/platforms, add-on features and fault
categories. Build an independently reviewed corpus containing working packs and known
defects. Examples: a missing texture, wrong script entry, broken dependency, omitted
event handler, incorrect recipe output, inverted cooldown condition, broken menu action,
lost saved state, and a visual state transition error.

Run the **same tester and predetermined requirements** on a held-out corpus. Count:

- **Bug detection:** buggy cases correctly flagged / all labelled buggy cases.
- **False alarms:** known-good cases incorrectly flagged / all known-good cases.
- **Blocked/inconclusive cases:** report separately; never silently exclude them to inflate detection.
- **Requirement coverage:** assessed requirements / declared requirements. This is a different metric.

The `benchmark` command consumes saved reports and labels:

```json
{
  "cases": [
    {"id":"good-1","buggy":false,"description":"Independently verified working pack","report":"good/report.json"},
    {"id":"broken-1","buggy":true,"description":"Missing texture used by the visible mob","report":"broken/report.json"}
  ]
}
```

```text
mcbe-test benchmark cases.json --out benchmark-result.json
```

Labels are inputs, not truths the tool can establish by itself. A failed report caused by
an unrelated test or environment fault must be adjudicated before counting it as detection
of the seeded bug. Include enough diverse cases and report uncertainty; a 95% score on a
small or cherry-picked corpus cannot justify a universal “95% accurate” claim. No such corpus
has been evaluated for this release.

Deliberately changing behavior to check whether a test detects the change is mutation
testing. Equivalent mutations and unrepresentative defects can distort its score.
[Stryker: mutation testing](https://stryker-mutator.io/docs/)
and [equivalent mutations](https://stryker-mutator.io/docs/mutation-testing-elements/equivalent-mutants/).

For a finite list of requirements, it is possible to test every listed case. For arbitrary
add-ons, worlds, inputs, future updates and device combinations, exhaustive correctness
cannot be established by this program. The intended product is a reproducible real-game
test lab with explicit gaps, rather than an unmeasured accuracy promise.
