"""Process-isolated QuickJS with a strict JSON-only Bedrock API bridge."""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

from .diagnostics import Unsupported
from .world import vector

SERVER_EXPORTS = "world system Block BlockPermutation Entity Player Dimension ItemStack GameMode Direction EntityDamageCause ItemTypes".split()
UI_EXPORTS = "ActionFormData ModalFormData MessageFormData FormCancelationReason".split()
ASSETS = Path(__file__).resolve().parents[1] / "assets"


def terminate(process):
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream:
            stream.close()


def read_messages(stream, inbox):
    try:
        while True:
            line = stream.readline(32 * 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 32 * 1024 * 1024:
                raise ValueError("Worker message limit")
            inbox.put(json.loads(line))
    except (ValueError, OSError) as exc:
        inbox.put({"ok": False, "error": str(exc)})
    finally:
        inbox.put({"ok": False, "error": "Script worker exited"})


def bundle(catalog):
    node = shutil.which("node")
    candidates = [
        Path.cwd() / "runtime/node_modules/esbuild",
        Path(__file__).resolve().parents[3] / "runtime/node_modules/esbuild",
    ]
    if os.environ.get("MCBE_ESBUILD_MODULE"):
        candidates.insert(0, Path(os.environ["MCBE_ESBUILD_MODULE"]))
    module = next((p for p in candidates if (p / "package.json").is_file()), None)
    if node is None or module is None:
        raise Unsupported(
            "Script module compiler missing. Install Node 20+ and run npm ci --prefix runtime in the repository."
        )
    files = {
        name: raw.decode("utf-8-sig") for name, raw in catalog.files.items() if name.endswith((".js", ".mjs"))
    }
    if sum(len(s) for s in files.values()) > 8 * 1024 * 1024:
        raise Unsupported("8 MiB script source budget")
    builtins = {}
    for module_name, namespace, names in (
        ("@minecraft/server", "server", SERVER_EXPORTS),
        ("@minecraft/server-ui", "ui", UI_EXPORTS),
    ):
        builtins[module_name] = "\n".join(
            f"export const {name} = globalThis.__bedrock_api.{namespace}.{name};" for name in names
        )
    payload = {
        "entries": catalog.scripts,
        "files": files,
        "roots": [p["prefix"] for p in catalog.packs if p["kind"] == "behavior"],
        "builtins": builtins,
    }
    try:
        result = subprocess.run(
            [node, str(ASSETS / "bundle.cjs"), str(module.resolve())],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise Unsupported("Module compilation timed out") from exc
    if len(result.stdout) > 16 * 1024 * 1024:
        raise Unsupported("Compiled module size budget exceeded")
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise Unsupported("Module compiler failed: " + result.stderr[:1000]) from exc
    if result.returncode or value.get("error"):
        raise Unsupported("Module compilation: " + value.get("error", result.stderr)[:2000])
    for warning in value.get("warnings", []):
        catalog.findings.unsupported("module-warning", "scripts", warning.get("text", str(warning)))
    return value["code"]


class ScriptRuntime:
    def __init__(self, world):
        self.world, self.catalog = world, world.catalog
        self.process = None
        self.broken = False
        self.active = False
        self.deferred = []
        self.draining = False
        self.registry = {"block": {}, "item": {}}
        self.inbox = queue.Queue()
        self.finalizer = None

    def close(self):
        if self.finalizer and self.finalizer.alive:
            self.finalizer()
        self.process = None

    def fail(self, message, unsupported=False):
        self.broken = True
        self.catalog.findings.add(
            "unsupported" if unsupported else "error", "script-runtime", "QuickJS", message
        )
        try:
            self.world.log("script-blocked" if unsupported else "script-error", message=str(message)[:2000])
        except Unsupported:
            pass  # The persistent finding survives even when the event trace is full.
        self.close()

    def start(self):
        if not self.catalog.scripts:
            return
        try:
            code = bundle(self.catalog)
            self.process = subprocess.Popen(
                [sys.executable, "-u", "-m", "mcbe_tester.sim.js_worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            self.finalizer = weakref.finalize(self, terminate, self.process)
            threading.Thread(
                target=read_messages, args=(self.process.stdout, self.inbox), daemon=True
            ).start()
            self.call("boot", code=code, bootstrap=(ASSETS / "bedrock_api.js").read_text(encoding="utf-8"))
            if not self.broken:
                self.call("startup")
        except (Unsupported, ValueError, OSError) as exc:
            self.fail(str(exc), unsupported=isinstance(exc, Unsupported))

    def call(self, op, data=None, **extra):
        if self.broken or self.process is None:
            return None
        if self.active:
            self.deferred.append((op, data))
            return None
        self.active = True
        deadline = time.monotonic() + 3
        value = None
        try:
            self.send({"op": op, "data": data, **extra})
            calls = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Script execution exceeded its 3-second wall-clock budget")
                try:
                    message = self.inbox.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError("Script execution exceeded its 3-second wall-clock budget") from exc
                if "rpc" in message:
                    calls += 1
                    if calls > 4096:
                        raise TimeoutError("Script API call budget exceeded")
                    try:
                        value = self.rpc(message["rpc"], message["args"])
                        self.send({"value": value})
                    except (
                        ValueError,
                        KeyError,
                        TypeError,
                        ArithmeticError,
                        IndexError,
                        AttributeError,
                        RecursionError,
                    ) as exc:
                        if isinstance(exc, Unsupported):
                            self.catalog.findings.unsupported("script-api", message["rpc"], str(exc))
                        self.send(
                            {
                                "error": str(exc),
                                "name": "UnsupportedAPI" if isinstance(exc, Unsupported) else "Error",
                            }
                        )
                elif message.get("ok"):
                    value = message.get("value")
                    break
                else:
                    raise RuntimeError(message.get("error", "Unknown script worker failure"))
        except (TimeoutError, RuntimeError, BrokenPipeError, OSError, ValueError) as exc:
            self.fail(str(exc), unsupported="UnsupportedAPI" in str(exc))
        finally:
            self.active = False
        # Mutations are synchronous; their after-events drain after the current callback.
        if not self.draining:
            self.draining = True
            try:
                count = 0
                while self.deferred and not self.broken:
                    count += 1
                    if count > 256:
                        self.fail("Deferred event budget exceeded")
                        break
                    next_op, payload = self.deferred.pop(0)
                    self.call(next_op, payload)
            finally:
                self.draining = False
        return value

    def send(self, value):
        payload = json.dumps(value, allow_nan=False)
        if len(payload) > 32 * 1024 * 1024:
            raise ValueError("Bridge payload limit exceeded")
        self.process.stdin.write(payload + "\n")
        self.process.stdin.flush()

    def advance(self):
        self.call("tick", self.world.tick)

    def event(self, name, data):
        result = self.call("event", {"name": name, "payload": data})
        if isinstance(result, str):
            return {**data, **json.loads(result)}
        return data

    def custom(self, kind, hook, identifiers, payload):
        for identifier, params in identifiers.items():
            if identifier not in self.registry[kind]:
                self.catalog.findings.unsupported("unregistered-component", identifier)
                continue
            if hook in self.registry[kind][identifier]:
                self.call(
                    "custom",
                    {
                        "kind": kind,
                        "hook": hook,
                        "identifier": identifier,
                        "params": params,
                        "payload": payload,
                    },
                )

    def rpc(self, method, args):
        if not isinstance(args, list) or len(args) > 16:
            raise ValueError("Invalid API arguments")
        w = self.world
        if method == "unsupported":
            raise Unsupported(": ".join(map(str, args)))
        if method == "register":
            kind, identifier, hooks = args
            supported = {
                "block": {
                    "onPlace",
                    "onPlayerInteract",
                    "onPlayerBreak",
                    "onTick",
                    "onStepOn",
                    "onStepOff",
                },
                "item": {"onUse"},
            }
            for hook in hooks:
                if hook not in supported[kind]:
                    self.catalog.findings.unsupported("custom-hook", identifier, hook)
            self.registry[kind][identifier] = hooks
        elif method == "random":
            return w.random.random()
        elif method == "message":
            w.log("script-message", text=str(args[0])[:2000], recipient=args[1])
        elif method == "permutation":
            components, states = self.catalog.block(args[0], args[1])
            return {"states": states, "components": components}
        elif method == "getBlock":
            return w.blocks.get(
                vector(args[0], True),
                {
                    "identifier": "minecraft:air",
                    "states": {},
                    "components": {"minecraft:collision_box": False},
                },
            )
        elif method == "setBlock":
            w.put(args[0], args[1], args[2])
        elif method == "blockTags":
            components, _ = self.catalog.block(args[0])
            return [key[4:] for key in components if key.startswith("tag:")]
        elif method == "itemIds":
            return sorted(set(self.catalog.items) | set(self.catalog.blocks))
        elif method == "itemInfo":
            return w.inventory.item_info(args[0])
        elif method == "inventory":
            return w.inventory.api(*args)
        elif method == "entity":
            return w.entity_api(*args)
        elif method == "queryEntities":
            return w.query_entities(*args)
        elif method == "spawnEntity":
            return w.spawn(args[0], args[1])
        elif method == "spawnItem":
            return w.spawn_item(*args)
        elif method == "dynamic":
            op, owner, *rest = args
            target = w.dynamic if owner == "world" else w.entity_data(owner).setdefault("dynamic", {})
            if op == "keys":
                return list(target)
            if op == "clear":
                target.clear()
            elif op == "get":
                return target.get(rest[0])
            elif op == "delete":
                target.pop(rest[0], None)
            elif op == "set":
                key, value = rest
                if not isinstance(key, str) or len(key) > 256 or len(target) >= 1024:
                    raise ValueError("Dynamic property limit")
                if (
                    not isinstance(value, (int, float, str, bool, dict))
                    or len(json.dumps(value, allow_nan=False)) > 32767
                ):
                    raise ValueError("Invalid dynamic property")
                if isinstance(value, dict):
                    vector(value)
                target[key] = value
            else:
                raise Unsupported("Dynamic property operation: " + op)
        elif method == "scoreboard":
            return w.scoreboard.api(*args)
        elif method == "command":
            from .commands import execute

            return execute(w, args[0], args[1])
        elif method == "form":
            return w.forms.show(*args)
        elif method == "display":
            _, kind, value, *_ = args
            w.player.setdefault("display", {})[kind] = value
            w.log("display", kind_name=kind, value=value)
        elif method == "time":
            if args[0] == "get":
                return (w.tick + w.time_offset) % 24000
            w.time_offset = int(args[1]) % 24000 - w.tick
        else:
            raise Unsupported("Script API operation not implemented: " + method)
        return None
