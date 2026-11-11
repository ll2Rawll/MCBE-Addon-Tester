"""Bounded ES2017 AST interpreter and explicit Script API model.

Addon JavaScript is data: it is never passed to eval, exec, Node, a shell, or a
host JavaScript engine. Unsupported nodes/APIs block results. This is not a full
QuickJS or @minecraft/server implementation.
"""

import math
import operator
from dataclasses import dataclass

import esprima

from .diagnostics import Unsupported
from .world import vector

NODES = {
    "Program",
    "ImportDeclaration",
    "ImportSpecifier",
    "ImportNamespaceSpecifier",
    "Identifier",
    "Literal",
    "VariableDeclaration",
    "VariableDeclarator",
    "ExpressionStatement",
    "CallExpression",
    "MemberExpression",
    "ArrowFunctionExpression",
    "FunctionExpression",
    "FunctionDeclaration",
    "BlockStatement",
    "ReturnStatement",
    "IfStatement",
    "BinaryExpression",
    "LogicalExpression",
    "UnaryExpression",
    "AssignmentExpression",
    "UpdateExpression",
    "ObjectExpression",
    "Property",
    "ArrayExpression",
    "ConditionalExpression",
    "ForStatement",
    "ForOfStatement",
    "WhileStatement",
    "EmptyStatement",
    "BreakStatement",
    "ContinueStatement",
    "ExportNamedDeclaration",
    "ExportSpecifier",
    "NewExpression",
    "TemplateLiteral",
    "TemplateElement",
    "SequenceExpression",
}
BIN = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "%": math.fmod,
    "===": lambda a, b: strict_equal(a, b),
    "!==": lambda a, b: not strict_equal(a, b),
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def truthy(value):
    return True if isinstance(value, (dict, list, Function, HostFunction)) else bool(value)


def strict_equal(left, right):
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if isinstance(left, (dict, list, Function, HostFunction)):
        return left is right
    return type(left) is type(right) and left == right


def js_string(value):
    if isinstance(value, (dict, list, Function, HostFunction)):
        return "[object Object]"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "undefined"
    return str(value)


class Env(dict):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent

    def owner(self, name):
        if name in self:
            return self
        if self.parent is not None:
            return self.parent.owner(name)
        raise Unsupported(f"JavaScript global not modeled: {name}")

    def get_value(self, name):
        return self.owner(name)[name]


@dataclass
class HostFunction:
    call: object


@dataclass
class Function:
    node: dict
    env: Env


class Returned(Exception):
    def __init__(self, value):
        self.value = value


class Flow(Exception):
    def __init__(self, kind):
        self.kind = kind


class Interpreter:
    def __init__(self, world):
        self.world = world
        self.catalog = world.catalog
        self.steps = 0
        self.depth = 0
        self.modules = {}
        self.subscribers = {}
        self.jobs = {}
        self.next_job = 1
        self.custom = {}
        self.broken = False
        self.globals = Env()
        self.globals.update(
            {
                "undefined": None,
                "NaN": float("nan"),
                "console": {
                    "warn": HostFunction(self.message),
                    "log": HostFunction(self.message),
                    "error": HostFunction(self.message),
                },
                "Math": {
                    name: HostFunction(fn)
                    for name, fn in {
                        "floor": math.floor,
                        "ceil": math.ceil,
                        "abs": abs,
                        "min": min,
                        "max": max,
                        "sqrt": math.sqrt,
                    }.items()
                },
            }
        )
        self.globals["Math"]["random"] = HostFunction(world.random.random)
        self.api = self.build_api()

    def message(self, *values):
        self.world.log("script-message", text=" ".join(map(js_string, values))[:2000])

    def budget(self):
        self.steps += 1
        if self.steps > 50000:
            raise Unsupported("JavaScript 50,000-operation budget exhausted")

    def audit(self, node):
        if isinstance(node, list):
            for child in node:
                self.audit(child)
        elif isinstance(node, dict):
            kind = node.get("type")
            if kind and kind not in NODES:
                raise Unsupported(f"JavaScript syntax not modeled: {kind}")
            if node.get("async") or node.get("generator"):
                raise Unsupported("async/await and generators are not modeled")
            if kind == "Literal" and "regex" in node:
                raise Unsupported("JavaScript regular expressions are not modeled")
            for child in node.values():
                if isinstance(child, (dict, list)):
                    self.audit(child)

    def load(self, name, importer=None):
        name, source = self.catalog.script_source(name, importer)
        if name in self.modules:
            exports = self.modules[name]
            if exports is None:
                raise Unsupported("Circular JavaScript modules are not modeled")
            return exports
        if len(self.modules) >= 128:
            raise Unsupported("128-script-module budget exhausted")
        self.modules[name] = None
        try:
            tree = esprima.parseModule(source).toDict()
        except Exception as exc:
            raise Unsupported(f"Unsupported/invalid JavaScript in {name}: {exc}") from exc
        self.audit(tree)
        env = Env(self.globals)
        exports = {}
        for stmt in tree["body"]:
            if stmt["type"] == "ImportDeclaration":
                path = stmt["source"]["value"]
                module = self.api if path == "@minecraft/server" else self.load(path, name)
                for spec in stmt["specifiers"]:
                    if spec["type"] == "ImportNamespaceSpecifier":
                        env[spec["local"]["name"]] = module
                    elif spec["type"] == "ImportSpecifier":
                        imported = spec["imported"]["name"]
                        if imported not in module:
                            raise Unsupported(f"Script API/export not modeled: {path}.{imported}")
                        env[spec["local"]["name"]] = module[imported]
                    else:
                        raise Unsupported("Default imports are not modeled")
            elif stmt["type"] == "ExportNamedDeclaration":
                declaration = stmt.get("declaration")
                if declaration:
                    self.run(declaration, env)
                    if declaration["type"] == "VariableDeclaration":
                        for variable in declaration["declarations"]:
                            key = variable["id"]["name"]
                            exports[key] = env.get_value(key)
                    else:
                        key = declaration["id"]["name"]
                        exports[key] = env.get_value(key)
                else:
                    for spec in stmt["specifiers"]:
                        exports[spec["exported"]["name"]] = env.get_value(spec["local"]["name"])
            else:
                self.run(stmt, env)
        self.modules[name] = exports
        return exports

    def start(self):
        if not self.catalog.scripts:
            return
        self.catalog.findings.approximate(
            "script-api",
            "profile",
            "ES2017 interpreter and explicit API subset; scheduling, execution privileges and JS coercion are not native parity",
        )
        try:
            self.steps = 0
            for name in self.catalog.scripts:
                self.load(name)
            startup = {"blockComponentRegistry": {"registerCustomComponent": HostFunction(self.register)}}
            self.emit("startup", startup)
            self.emit("worldLoad", {})
            self.emit("playerSpawn", {"player": self.player(), "initialSpawn": True})
        except (Unsupported, ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as exc:
            self.fail(exc)

    def fail(self, exc):
        self.broken = True
        self.catalog.findings.unsupported("script-execution", "runtime", str(exc))
        self.world.log("script-blocked", message=str(exc))

    def member(self, obj, key):
        if isinstance(obj, dict):
            if key not in obj:
                raise Unsupported(f"JavaScript/API member not modeled: {key}")
            return obj[key]
        if isinstance(obj, (list, str)):
            if key == "length":
                return len(obj)
            if isinstance(key, (int, float)) and int(key) == key:
                return obj[int(key)] if 0 <= int(key) < len(obj) else None
            if key == "includes":
                return HostFunction(lambda value: value in obj)
            if key == "slice":
                return HostFunction(
                    lambda start=0, end=None: obj[int(start) : None if end is None else int(end)]
                )
            if isinstance(obj, list):
                if key == "push":

                    def push(*items):
                        if len(obj) + len(items) > 4096:
                            raise Unsupported("JavaScript array limit")
                        obj.extend(items)
                        return len(obj)

                    return HostFunction(push)
                if key in {"forEach", "map", "filter"}:

                    def loop(callback):
                        out = []
                        for i, value in enumerate(list(obj)):
                            result = self.call(callback, [value, i])
                            if key == "map":
                                out.append(result)
                            elif key == "filter" and result:
                                out.append(value)
                        return out if key != "forEach" else None

                    return HostFunction(loop)
        raise Unsupported(f"JavaScript member not modeled: {key}")

    def assign(self, target, value, env):
        if target["type"] == "Identifier":
            env.owner(target["name"])[target["name"]] = value
        elif target["type"] == "MemberExpression":
            obj = self.run(target["object"], env)
            key = self.run(target["property"], env) if target["computed"] else target["property"]["name"]
            if isinstance(obj, dict):
                if len(obj) >= 4096 and key not in obj:
                    raise Unsupported("JavaScript object limit")
                obj[key] = value
            elif (
                isinstance(obj, list)
                and isinstance(key, (int, float))
                and int(key) == key
                and 0 <= key < len(obj)
            ):
                obj[int(key)] = value
            else:
                raise Unsupported("Unsupported assignment target")
        else:
            raise Unsupported("Destructuring assignment is not modeled")
        return value

    def call(self, fn, args):
        self.budget()
        self.depth += 1
        if self.depth > 48:
            self.depth -= 1
            raise Unsupported("JavaScript call-depth limit")
        try:
            if isinstance(fn, HostFunction):
                return fn.call(*args)
            if not isinstance(fn, Function):
                raise Unsupported("Value is not a modeled callable")
            local = Env(fn.env)
            for i, param in enumerate(fn.node["params"]):
                if param["type"] != "Identifier":
                    raise Unsupported("Destructured/default parameters are not modeled")
                local[param["name"]] = args[i] if i < len(args) else None
            try:
                value = self.run(fn.node["body"], local)
                return value if fn.node["body"]["type"] != "BlockStatement" else None
            except Returned as ret:
                return ret.value
        finally:
            self.depth -= 1

    def run(self, node, env):
        self.budget()
        if node is None:
            return None
        kind = node["type"]
        if kind == "Literal":
            return node.get("value")
        if kind == "Identifier":
            return env.get_value(node["name"])
        if kind == "VariableDeclaration":
            for declaration in node["declarations"]:
                if declaration["id"]["type"] != "Identifier":
                    raise Unsupported("Destructuring declarations are not modeled")
                env[declaration["id"]["name"]] = self.run(declaration.get("init"), env)
        elif kind in {"BlockStatement", "Program"}:
            for stmt in node["body"]:
                self.run(stmt, env)
        elif kind in {"ArrowFunctionExpression", "FunctionExpression"}:
            return Function(node, env)
        elif kind == "FunctionDeclaration":
            env[node["id"]["name"]] = Function(node, env)
        elif kind == "ExpressionStatement":
            return self.run(node["expression"], env)
        elif kind in {"CallExpression", "NewExpression"}:
            return self.call(self.run(node["callee"], env), [self.run(arg, env) for arg in node["arguments"]])
        elif kind == "MemberExpression":
            return self.member(
                self.run(node["object"], env),
                self.run(node["property"], env) if node["computed"] else node["property"]["name"],
            )
        elif kind == "ArrayExpression":
            return [self.run(value, env) for value in node["elements"]]
        elif kind == "ObjectExpression":
            out = {}
            for prop in node["properties"]:
                if prop["kind"] != "init":
                    raise Unsupported("JavaScript accessors are not modeled")
                key = (
                    self.run(prop["key"], env)
                    if prop["computed"] or prop["key"]["type"] == "Literal"
                    else prop["key"]["name"]
                )
                out[key] = self.run(prop["value"], env)
            return out
        elif kind == "ReturnStatement":
            raise Returned(self.run(node.get("argument"), env))
        elif kind == "IfStatement":
            return self.run(
                node["consequent"] if truthy(self.run(node["test"], env)) else node.get("alternate"), env
            )
        elif kind == "ConditionalExpression":
            return self.run(
                node["consequent"] if truthy(self.run(node["test"], env)) else node["alternate"], env
            )
        elif kind == "LogicalExpression":
            left = self.run(node["left"], env)
            return (
                (self.run(node["right"], env) if truthy(left) else left)
                if node["operator"] == "&&"
                else (left if truthy(left) else self.run(node["right"], env))
            )
        elif kind == "BinaryExpression":
            op = node["operator"]
            if op not in BIN:
                raise Unsupported(f"JavaScript operator not modeled: {op}")
            left, right = self.run(node["left"], env), self.run(node["right"], env)
            if op == "+" and (isinstance(left, str) or isinstance(right, str)):
                value = js_string(left) + js_string(right)
                if len(value) > 65536:
                    raise Unsupported("JavaScript string limit")
                return value
            if op in {"+", "-", "*", "/", "%"} and (
                type(left) not in (int, float) or type(right) not in (int, float)
            ):
                raise Unsupported("JavaScript arithmetic coercion is not modeled")
            value = BIN[op](left, right)
            if isinstance(value, (int, float)) and (not math.isfinite(value) or abs(value) > 1e100):
                raise Unsupported("JavaScript numeric limit")
            return value
        elif kind == "UnaryExpression":
            value = self.run(node["argument"], env)
            if node["operator"] == "!":
                return not truthy(value)
            if node["operator"] in {"-", "+"}:
                return -value if node["operator"] == "-" else +value
            raise Unsupported(f"Unary operator not modeled: {node['operator']}")
        elif kind == "AssignmentExpression":
            value = self.run(node["right"], env)
            op = node["operator"]
            if op != "=":
                if op not in {"+=", "-=", "*=", "/="}:
                    raise Unsupported(f"Assignment operator not modeled: {op}")
                left = self.run(node["left"], env)
                if type(left) not in (int, float) or type(value) not in (int, float):
                    raise Unsupported("Compound assignment supports numbers only")
                value = BIN[op[:-1]](left, value)
                if not math.isfinite(value) or abs(value) > 1e100:
                    raise Unsupported("JavaScript numeric limit")
            return self.assign(node["left"], value, env)
        elif kind == "UpdateExpression":
            old = self.run(node["argument"], env)
            value = old + (1 if node["operator"] == "++" else -1)
            self.assign(node["argument"], value, env)
            return value if node["prefix"] else old
        elif kind in {"ForStatement", "WhileStatement", "ForOfStatement"}:
            if kind == "ForOfStatement":
                values = self.run(node["right"], env)
                if not isinstance(values, (list, str)) or len(values) > 4096:
                    raise Unsupported("for-of requires a bounded array or string")
                declaration = node["left"]
                if declaration["type"] != "VariableDeclaration":
                    raise Unsupported("for-of needs a variable declaration")
                name = declaration["declarations"][0]["id"]["name"]
                for value in values:
                    env[name] = value
                    try:
                        self.run(node["body"], env)
                    except Flow as flow:
                        if flow.kind == "BreakStatement":
                            break
                return None
            self.run(node.get("init"), env)
            while node.get("test") is None or truthy(self.run(node["test"], env)):
                self.budget()
                try:
                    self.run(node["body"], env)
                except Flow as flow:
                    if flow.kind == "BreakStatement":
                        break
                self.run(node.get("update"), env)
        elif kind in {"BreakStatement", "ContinueStatement"}:
            raise Flow(kind)
        elif kind == "TemplateLiteral":
            out = node["quasis"][0]["value"]["cooked"]
            for i, expression in enumerate(node["expressions"]):
                out += js_string(self.run(expression, env)) + node["quasis"][i + 1]["value"]["cooked"]
                if len(out) > 65536:
                    raise Unsupported("JavaScript string limit")
            return out
        elif kind == "SequenceExpression":
            result = None
            for expression in node["expressions"]:
                result = self.run(expression, env)
            return result
        elif kind != "EmptyStatement":
            raise Unsupported(f"JavaScript node not modeled: {kind}")
        return None

    def subscribe(self, name, callback):
        if not isinstance(callback, Function):
            raise Unsupported("Event subscription requires a script function")
        if sum(map(len, self.subscribers.values())) >= 256:
            raise Unsupported("Event subscription limit")
        self.subscribers.setdefault(name, []).append(callback)
        return callback

    def emit(self, name, value):
        for callback in list(self.subscribers.get(name, [])):
            self.call(callback, [value])

    def events(self, names, prefix=""):
        return {
            name: {"subscribe": HostFunction(lambda cb, name=name: self.subscribe(prefix + name, cb))}
            for name in names
        }

    def schedule(self, callback, delay=1, interval=False):
        if (
            not isinstance(callback, Function)
            or not isinstance(delay, (int, float))
            or not math.isfinite(delay)
            or delay > 20000
        ):
            raise ValueError("Invalid scheduled callback")
        if len(self.jobs) >= 256:
            raise Unsupported("256 scheduled callback limit")
        number = self.next_job
        self.next_job += 1
        delay = max(1, int(delay))
        self.jobs[number] = {
            "at": self.world.tick + delay,
            "interval": delay if interval else 0,
            "callback": callback,
        }
        return number

    def advance(self):
        if self.broken:
            return
        try:
            self.steps = 0
            self.api["system"]["currentTick"] = self.world.tick
            for number, job in list(self.jobs.items()):
                if number not in self.jobs or job["at"] > self.world.tick:
                    continue
                if job["interval"]:
                    job["at"] += job["interval"]
                else:
                    del self.jobs[number]
                self.call(job["callback"], [])
        except (Unsupported, ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as exc:
            self.fail(exc)

    def event(self, name, data):
        if self.broken:
            return data
        try:
            self.steps = 0
            payload = {**data, "player": self.player(), "source": self.player()}
            if "position" in data:
                payload["block"] = self.block(data["position"])
            if "entity_id" in data:
                payload["entity"] = self.entity(data["entity_id"])
            if "identifier" in data:
                payload["itemStack"] = {"typeId": data["identifier"], "amount": 1}
            self.emit(name, payload)
            if name in {"playerInteractWithBlock", "playerPlaceBlock"}:
                block = self.world.blocks[tuple(data["position"])]
                components = block["components"]
                custom = components.get("minecraft:custom_components", []) + [
                    key for key in components if not key.startswith("minecraft:")
                ]
                hook = "onPlayerInteract" if name == "playerInteractWithBlock" else "onPlace"
                for identifier in custom:
                    if identifier not in self.custom:
                        raise Unsupported(f"Custom component not registered: {identifier}")
                    callback = self.custom[identifier].get(hook)
                    if callback:
                        self.call(callback, [payload, {"params": components.get(identifier, {})}])
            return {**data, "cancel": payload.get("cancel", False)}
        except (Unsupported, ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as exc:
            self.fail(exc)
            return data

    def register(self, name, callbacks):
        if not isinstance(callbacks, dict) or len(self.custom) >= 256:
            raise Unsupported("Invalid/oversized custom component registry")
        for hook in callbacks:
            if hook not in {"onPlayerInteract", "onPlace"}:
                self.catalog.findings.unsupported("custom-component-hook", name, hook)
        self.custom[name] = callbacks

    def permutation(self, identifier, states=None):
        _, chosen = self.catalog.block(identifier, states)
        return {
            "type": {"id": identifier},
            "_identifier": identifier,
            "_states": chosen,
            "getState": HostFunction(lambda key: chosen[key]),
            "getAllStates": HostFunction(lambda: dict(chosen)),
            "withState": HostFunction(
                lambda key, value: self.permutation(identifier, {**chosen, key: value})
            ),
        }

    def block(self, position):
        pos = vector(position, True)
        block = self.world.blocks.get(pos, {"identifier": "minecraft:air", "states": {}})
        identifier = block["identifier"]
        return {
            "typeId": identifier,
            "isAir": identifier == "minecraft:air",
            "location": dict(zip(("x", "y", "z"), pos, strict=True)),
            "permutation": self.permutation(identifier, block["states"]),
            "setType": HostFunction(lambda value: self.world.put(pos, value)),
            "setPermutation": HostFunction(
                lambda value: self.world.put(pos, value["_identifier"], value["_states"])
            ),
        }

    def player(self):
        return {
            "id": "player",
            "typeId": "minecraft:player",
            "name": "SimulatorPlayer",
            "location": dict(zip(("x", "y", "z"), self.world.player["position"], strict=True)),
            "sendMessage": HostFunction(self.message),
            "dimension": self.dimension(),
            "teleport": HostFunction(
                lambda pos: self.world.action({"type": "teleport", "position": list(vector(pos))})
            ),
            "getDynamicProperty": HostFunction(lambda key: self.world.dynamic.get("player:" + key)),
            "setDynamicProperty": HostFunction(lambda key, value: self.set_dynamic("player:" + key, value)),
        }

    def entity(self, entity_id):
        entity = self.world.entities[entity_id]
        return {
            "id": entity_id,
            "typeId": entity["identifier"],
            "location": dict(zip(("x", "y", "z"), entity["position"], strict=True)),
            "triggerEvent": HostFunction(lambda name: self.world.entity_event(entity_id, name)),
            "getProperty": HostFunction(lambda key: entity["properties"][key]),
            "remove": HostFunction(lambda: self.world.entities.pop(entity_id, None)),
            "applyImpulse": HostFunction(lambda pos: entity.update(velocity=list(vector(pos)))),
        }

    def dimension(self, name="overworld"):
        if name not in {"overworld", "minecraft:overworld"}:
            raise Unsupported("Only the simulator overworld is implemented")
        return {
            "id": "minecraft:overworld",
            "getBlock": HostFunction(self.block),
            "spawnEntity": HostFunction(lambda name, pos: self.entity(self.world.spawn(name, pos))),
            "getEntities": HostFunction(lambda: [self.entity(e) for e in self.world.entities]),
        }

    def set_dynamic(self, key, value):
        if not isinstance(key, str) or len(key) > 256 or len(self.world.dynamic) >= 1024:
            raise Unsupported("Dynamic property limit")
        if (
            not isinstance(value, (str, bool, int, float, type(None)))
            or isinstance(value, str)
            and len(value) > 1024
        ):
            raise Unsupported("Only bounded scalar dynamic properties are modeled")
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise Unsupported("Nonfinite dynamic property value")
        self.world.dynamic[key] = value

    def build_api(self):
        return {
            "world": {
                "getDimension": HostFunction(self.dimension),
                "getAllPlayers": HostFunction(lambda: [self.player()]),
                "sendMessage": HostFunction(self.message),
                "getDynamicProperty": HostFunction(lambda key: self.world.dynamic.get(key)),
                "setDynamicProperty": HostFunction(self.set_dynamic),
                "beforeEvents": self.events(["playerBreakBlock", "itemUse"], "before:"),
                "afterEvents": self.events(
                    [
                        "worldLoad",
                        "playerSpawn",
                        "playerBreakBlock",
                        "playerPlaceBlock",
                        "playerInteractWithBlock",
                        "itemUse",
                        "entitySpawn",
                    ]
                ),
            },
            "system": {
                "beforeEvents": self.events(["startup"]),
                "currentTick": 0,
                "run": HostFunction(self.schedule),
                "runTimeout": HostFunction(self.schedule),
                "runInterval": HostFunction(lambda callback, delay=1: self.schedule(callback, delay, True)),
                "clearRun": HostFunction(lambda number: self.jobs.pop(number, None)),
            },
            "BlockPermutation": {"resolve": HostFunction(self.permutation)},
            "ItemStack": HostFunction(lambda name, amount=1: {"typeId": name, "amount": amount}),
        }
