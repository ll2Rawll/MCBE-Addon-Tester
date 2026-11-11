# Test a supplied JSON UI screen

The interpreter loads resource-pack UI definitions and exercises a selected subset without Minecraft. The caller explicitly opens a fixture screen and provides any external binding values. It does not reproduce Bedrock's native screen manager or game handlers.

Run the original example:

```sh
bedrock-addon-tester test examples/json-ui-addon --scenario examples/json-ui-scenario.json --out .mcbe-sim/ui
```

The fixture supplies its own templates and a white texture tinted by the UI. Its scenario checks layout, typing, toggling, a mapped button event, binding changes and closing. The exported input, actions, assertions and three PNGs can be replayed using the usual `test …/input.zip --scenario …/replay.json` command.

## Load and operate through MCP

Call `load_addon`, read `catalog.ui_definitions`, then pass actions to `simulate_actions`:

```json
[
  {
    "type": "ui_open",
    "screen": "workshop.screen",
    "width": 640,
    "height": 400,
    "bindings": {
      "#heading": "Settings",
      "#show_notice": true,
      "#status": "Ready"
    }
  },
  {"type": "click", "x": 0.5, "y": 0.3},
  {"type": "text", "text": "Copper"},
  {"type": "key", "key": "down"},
  {"type": "key", "key": "space"},
  {"type": "ui_pointer", "x": 0.5, "y": 0.5},
  {"type": "click", "x": 0.5, "y": 0.5},
  {"type": "ui_bind", "bindings": {"#show_notice": false}},
  {"type": "key", "key": "escape"}
]
```

Use `render_view` between actions to inspect changes. All pointer coordinates are normalized to the entire output image. The logical viewport and PNG dimensions may differ; rendering and hit regions scale together. UI previews have no tool overlay obscuring controls. Image metadata and exported reports identify them as independent simulation.

`ui_open` also accepts `$`-prefixed `variables`. Explicit template variables override inherited defaults. `ui_bind` updates existing/provides new `#` values and rebuilds layout. `ui_pointer` accepts optional `pressed:true/false`; a held pointer produces one pressed event until released. `click` activates once using the primary button; other pointer buttons are explicitly unsupported. `key` supports Tab, declared directional focus links, Enter/Space and Escape. `text` replaces the focused edit box's value subject to `max_length`. `ui_close` closes explicitly. These actions do not send operating-system input.

Inspect and assert:

| State path | Meaning |
| --- | --- |
| `/json_ui/nodes` | Resolved controls with IDs, logical rectangles, clips, layer, visibility, enabled state, color, alpha and text |
| `/ui_regions` | Visible/enabled interactive rectangles, ordered from topmost to lowest |
| `/json_ui/state` | Edited text and toggle state by full control ID |
| `/json_ui/events` | Mapped button IDs and control/tick information |
| `/json_ui/bindings` | Explicit external fixture data |

Control IDs contain `/`; escape it as `~1` in JSON pointers. For example, `/json_ui/state/workshop.screen~1card~1stack~1name/text`. An emitted `button.workshop_apply` proves that this model resolved that mapping. It does not establish that a native game handler exists or ran.

## Implemented surface

- `_ui_defs.json`, `_global_variables.json`, namespaces, named `@` template inheritance, `$` variables and `|default`. Arrays in template overrides replace inherited arrays.
- `screen`, `panel`, `input_panel`, `stack_panel`, `label`, `image`, `button`, `toggle`, `edit_box`.
- Numeric/default/pixel/parent-percentage sizes and offsets, percentage plus/minus pixels, nine anchors, size constraints, vertical/horizontal stacks, ignored controls, layers, clipping, visibility, enabled state and alpha.
- Explicit global and view bindings with `always` conditions, property aliases, cross-control references, local `#toggle_state` and edit-box `#text_box_text`. Binding/variable cycles fail. Binding payload text is data, not an expression to execute.
- Bounded typed expressions using parentheses, `$`/`#` references, arithmetic, comparisons, boolean operators, string concatenation and subtraction. Unsupported coercions/syntax are reported.
- Supplied image textures, bounded UV crops, nearest/bilinear scaling, tint, grayscale and alpha. Labels use independent Pillow font metrics and selected size/alignment/shadow settings.
- Button child visual states, pressed mappings for keyboard/mouse, edit-box child text/placeholder controls, toggle state, explicit focus links and observable mapped events.

Missing templates, unavailable native bindings and unknown definition fields remain report findings. Unlisted UI files are flagged because automatic native base-screen loading is not implemented. Factories, collections, UI animations, modifications, native hardcoded renderers, content/sibling/fill sizing, native input timing, IME, controller/touch handling and native fonts remain gaps. The scanner also has limits: a field recognized by this interpreter is not proof of complete semantics in every context.

Definitions are based on the public [screen](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/jsonuireference/examples/jsonuicomponents/ui_screen?view=minecraft-bedrock-stable) and [element](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/jsonuireference/examples/jsonuicomponents/ui_element?view=minecraft-bedrock-stable) references, with supplementary [community documentation](https://wiki.bedrock.dev/json-ui/json-ui-documentation). These do not provide a complete, independently calibrated native UI specification. See [LIMITATIONS.md](LIMITATIONS.md) for execution bounds and fidelity limits.
