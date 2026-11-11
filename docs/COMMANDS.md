# Functions and command simulation

Behavior-pack `functions/**/*.mcfunction` files are indexed by their relative path without the extension. Blank lines and full-line comments are skipped. The tool never passes pack commands to a shell.

```sh
bedrock-addon-tester test examples/functions-addon --scenario examples/functions-scenario.json --out .mcbe-sim/functions
```

The original example initializes an objective, runs ordered tick functions, marks the third tick, and calls a nested function with an inherited execution position. All expected values are explicit in its scenario.

Use the MCP `simulate_actions` tool with a command action:

```json
{"type":"command","command":"execute positioned 10.0 2.0 10.0 as @s run function move"}
```

`as` changes the executor; `at` changes position and rotation. `positioned`, `positioned as`, `rotated`, `rotated as`, `facing <position>` and `align` update only their context fields. Relative `~` and local `^` coordinates use that context. `in` only accepts the overworld. Chained `if`/`unless` conditions support entity selection, block identifiers/states, score comparisons and `matches` ranges. Terminal conditions return a result without `run`. Function calls inherit the calling context.

Selectors include `@s`, `@a`, `@e`, `@p` and seeded `@r`. Supported filters are type, name, tag, family, game mode, radius/minimum radius, position, rectangular deltas, count and multiple score ranges. Quoted names, nested score filters and supported negations are parsed without shell quoting rules. Missing scores do not satisfy negated ranges. Negative counts select from the farthest candidates. Other selector filters remain unsupported. Native random sequences and exact tie/boundary behavior are unverified.

Implemented command forms:

| Family | Subset |
| --- | --- |
| `function` | Supplied name, including subdirectories |
| `execute` | The context/condition forms above and modeled `run` commands |
| `scoreboard` | Objective add/remove/list; player set/add/remove/reset/test/operation; named and entity holders |
| Score operations | `=`, `+=`, `-=`, `*=`, `/=`, `%=`, `<`, `>`, `><`; floor division/modulo; zero division leaves scores unchanged |
| `setblock`, `testforblock` | Supplied block, optional state list; setblock replace/keep |
| `fill` | Bounded box, optional states, replace/keep/outline/hollow |
| `testfor` | Selector existence/count |
| Other | Selected say, give, summon, coordinate teleport, kill, tag add/remove, event entity and gamemode forms |

Example state syntax is `setblock 1 0 1 workshop:lamp ["workshop:lit"=true]`. Blocks and entities require supplied content; vanilla assets/behavior are not fabricated.

`functions/tick.json` lists function names in `values`. Each advanced simulated tick runs that list in order with a shared budget and no player executor. Use explicit selectors such as `@a` in tick functions. Native pre-world-load execution and the ordering of native scripts versus tick functions are not reproduced.

A root call has a 10,000-command ceiling and depth limit of 32. Execute has a 128-context limit, function files are at most 256 KiB, there are at most 512 supplied functions, and fill touches at most 1,024 cells. Other session/world/event limits can stop a call sooner. Ordinary function-command errors are recorded while later lines continue. Unsupported syntax blocks that execution and remains in the report.

Command result counts are modeled evidence, not verified native return values. Absolute coordinates currently use literal numeric values; native whole-number coordinate centering and float32 behavior are unverified. Use explicit decimals when a fixture needs an exact position. Command blocks, permissions, other dimensions, teleport entity/rotation/check-for-blocks forms and the full command grammar are not implemented. The scanner flags unknown function roots and execute clauses even in uncalled files but is not a complete parser/validator for every unused argument form.

Primary references: [functions](https://learn.microsoft.com/en-us/minecraft/creator/documents/functionsintroduction?view=minecraft-bedrock-stable), [tick.json](https://learn.microsoft.com/en-us/minecraft/creator/documents/tickjsonintroduction?view=minecraft-bedrock-stable), [execute](https://learn.microsoft.com/en-us/minecraft/creator/commands/commands/execute?view=minecraft-bedrock-stable), [selectors](https://learn.microsoft.com/en-us/minecraft/creator/documents/targetselectors?view=minecraft-bedrock-stable), [scoreboard operations](https://learn.microsoft.com/en-us/minecraft/creator/documents/scoreboardoperationstutorial?view=minecraft-bedrock-stable).
