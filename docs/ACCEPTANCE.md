# Live Windows acceptance

This checklist verifies the tester itself before trusting it with an add-on. It has not
been executed against a licensed, running Minecraft client in the Linux development
environment. GitHub's Windows CI only checks native API loading/discovery, not gameplay.

Use Windows 10/11, a clean exported test world, an unlocked desktop and retail Bedrock.
Keep run artifacts locally. Record OS, GPU, graphics mode, resolution, GUI scale, language,
Minecraft UI version, executable file version, and Python/tool versions.

## Controller checks

- [ ] `doctor` finds the actual Minecraft window and exact file version.
- [ ] A deliberately different target file version prevents binding.
- [ ] `focus_game` focuses Minecraft; screenshots show its client area without unexpected crops.
- [ ] Normalized clicks align at 100% and a second Windows DPI scale, including a second monitor if applicable.
- [ ] Opening inventory with `e`, closing with Escape, movement, jump, mouse look and placement work.
- [ ] Held keys/buttons are released when the action ends.
- [ ] Switching focus away during a hold stops input and releases held controls.
- [ ] Holding F8 or creating STOP during a hold stops input; subsequent actions remain blocked.
- [ ] Text appears correctly in chat/search, including non-ASCII text; Enter remains a separate action.
- [ ] A window recreation or game update requires rebinding/a new run.

## World and gameplay checks

- [ ] A copied template imports under the generated Addon Lab name; the original export is byte-identical.
- [ ] The demo block's behavior and resource packs are active in that world.
- [ ] All six block faces render with the intended green texture; inventory name/icon are readable.
- [ ] Real placement consumes the item in Survival; the actual block identifier matches.
- [ ] Mining, inventory interaction, saving and reopening work through actual controls.
- [ ] The optional observer emits `ready` and snapshots tagged with this run ID.
- [ ] Real item/block events appear as expected; unsupported event APIs are reported.
- [ ] A deliberately false state assertion fails; missing/stale observer data blocks.
- [ ] The same acceptance requirements are repeated without the observer and with intended experiments.

## Detection checks

- [ ] Review and save a known-good visual reference. A later identical fixed-camera scene passes.
- [ ] Changing the texture or moving a visible element causes an appropriate comparison failure.
- [ ] A missing/unapproved/changed reference blocks comparison.
- [ ] An intentionally malformed JSON file is flagged by the structural check/official validator.
- [ ] A deliberate script exception appears in collected logs and prevents a green report.
- [ ] A deliberately absent GameTest or wrong registered count blocks/fails the external runner.
- [ ] External timeout/STOP terminates its process tree; it is not reported as a pass.
- [ ] Missing requirements and modified/missing evidence keep the report incomplete.

Record results and retain screenshots, logs and diffs. Open issues for failed controller
checks before interpreting them as add-on faults. Passing these checks establishes basic
operability on that machine/build, not universal compatibility or a measured 95% detection rate.
