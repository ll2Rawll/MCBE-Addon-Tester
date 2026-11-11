"""Bounded Molang parser/evaluator, including control flow and resource arrays.

The supported language follows the current public syntax contract. Unknown world
queries remain Unsupported; specified numeric errors evaluate to zero and can be
observed through context['_molang_error']. No Python eval/exec is used.
"""

import math
import operator
import re
from functools import lru_cache

from .diagnostics import Unsupported

TOKEN = re.compile(
    r"\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)|"
    r"('[^']*')|([A-Za-z_][A-Za-z_0-9.]*)|"
    r"(\?\?|==|!=|<=|>=|&&|\|\||[+*/%<>()!?:,;=\-{}\[\]]))"
)
MISSING = object()
OPS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "%": math.fmod,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
PREC = {
    "??": 1,
    "||": 2,
    "&&": 3,
    "==": 4,
    "!=": 4,
    "<": 5,
    "<=": 5,
    ">": 5,
    ">=": 5,
    "+": 6,
    "-": 6,
    "*": 7,
    "/": 7,
    "%": 7,
}


def min_angle(value):
    return (value + 180) % 360 - 180


MATH = {
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "sqrt": math.sqrt,
    "sin": lambda x: math.sin(math.radians(x)),
    "cos": lambda x: math.cos(math.radians(x)),
    "acos": lambda x: math.degrees(math.acos(x)),
    "asin": lambda x: math.degrees(math.asin(x)),
    "atan": lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "min": min,
    "max": max,
    "clamp": lambda x, a, b: max(a, min(b, x)),
    "lerp": lambda a, b, t: a + (b - a) * t,
    "lerprotate": lambda a, b, t: a + min_angle(b - a) * t,
    "round": lambda x: math.copysign(math.floor(abs(x) + 0.5), x),
    "trunc": math.trunc,
    "pow": math.pow,
    "exp": math.exp,
    "ln": math.log,
    "mod": math.fmod,
    "min_angle": min_angle,
    "hermite_blend": lambda x: 3 * x * x - 2 * x * x * x,
    "copy_sign": math.copysign,
}


def canonical(name):
    name = name.lower()
    for short, long in (("q.", "query."), ("v.", "variable."), ("t.", "temp."), ("c.", "context.")):
        if name.startswith(short):
            return long + name[len(short) :]
    return name


class Parser:
    def __init__(self, text):
        if len(text) > 8192:
            raise Unsupported("Molang expression exceeds 8 KiB")
        self.tokens, at = [], 0
        while at < len(text.rstrip()):
            match = TOKEN.match(text, at)
            if not match:
                raise Unsupported(f"Unsupported Molang syntax near {text[at : at + 40]!r}")
            tok = next(g for g in match.groups() if g is not None)
            self.tokens.append(tok if tok.startswith("'") else tok.lower())
            at = match.end()
        self.tokens.append("<end>")
        self.at = self.depth = 0

    def peek(self):
        return self.tokens[self.at]

    def pop(self, expected=None):
        tok = self.peek()
        if expected and tok != expected:
            raise Unsupported(f"Expected {expected}, found {tok}")
        self.at += 1
        return tok

    def statements(self, end="<end>"):
        result = []
        while self.peek() != end:
            if self.peek() == "<end>":
                raise Unsupported("Unclosed Molang scope")
            if self.peek() in {"break", "continue"}:
                result.append((self.pop(),))
            elif self.peek() == "return":
                self.pop()
                result.append(("return", self.expression()))
            else:
                result.append(self.expression())
            if self.peek() == ";":
                self.pop()
            elif self.peek() != end:
                raise Unsupported(f"Expected statement terminator, found {self.peek()}")
        return tuple(result)

    def expression(self, minimum=0):
        self.depth += 1
        if self.depth > 64:
            raise Unsupported("Molang nesting limit")
        tok = self.pop()
        if tok in {"!", "-", "+"}:
            left = ("unary", tok, self.expression(8))
        elif tok == "(":
            left = self.expression()
            self.pop(")")
        elif tok == "{":
            left = ("scope", self.statements("}"))
            self.pop("}")
        elif tok.startswith("'"):
            left = ("literal", tok[1:-1])
        elif re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", tok):
            left = ("literal", float(tok))
        elif tok in {"true", "false"}:
            left = ("literal", tok == "true")
        elif re.fullmatch(r"[a-z_][\w.]*", tok):
            name = canonical(tok)
            if self.peek() == "(":
                self.pop()
                args = []
                while self.peek() != ")":
                    args.append(self.expression())
                    if self.peek() != ",":
                        break
                    self.pop()
                self.pop(")")
                left = ("call", name, tuple(args))
            else:
                left = ("name", name)
        else:
            raise Unsupported(f"Unexpected Molang token {tok}")
        while self.peek() == "[":
            self.pop()
            left = ("index", left, self.expression())
            self.pop("]")
        while self.peek() in PREC and PREC[self.peek()] >= minimum:
            op = self.pop()
            left = ("binary", op, left, self.expression(PREC[op] + 1))
        if minimum == 0 and self.peek() == "?":
            self.pop()
            yes = self.expression()
            no = ("literal", 0)
            if self.peek() == ":":
                self.pop()
                no = self.expression()
            left = ("conditional", left, yes, no)
        if minimum == 0 and self.peek() == "=":
            if left[0] != "name" or not left[1].startswith(("variable.", "temp.")):
                raise Unsupported("Molang assignment requires variable/temp storage")
            self.pop()
            left = ("assign", left[1], self.expression())
        self.depth -= 1
        return left


@lru_cache(maxsize=512)
def compile_expression(value):
    parser = Parser(value)
    nodes = parser.statements()
    return nodes, ";" in parser.tokens or len(nodes) != 1


class Flow(Exception):
    def __init__(self, kind, value=0):
        self.kind, self.value = kind, value


def evaluate(value, context=None, variables=None):
    if isinstance(value, (int, float, bool)):
        if not math.isfinite(value):
            raise Unsupported("Nonfinite Molang input")
        return value
    if not isinstance(value, str):
        raise Unsupported("Molang requires a number, boolean, or expression string")
    context = context or {}
    variables = variables if variables is not None else {}
    temporary, budget = {}, [0]
    nodes, complex_expression = compile_expression(value)

    def numeric(v):
        if type(v) not in {int, float, bool}:
            raise Unsupported("Molang operation requires a numeric value")
        return v

    def number_operation(callback):
        try:
            result = callback()
            if isinstance(result, (int, float)) and not math.isfinite(result):
                raise ValueError("Nonfinite arithmetic")
            return result
        except Unsupported:
            raise
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            if "_molang_error" in context:
                context["_molang_error"](str(exc))
            return 0

    def read(name, optional=False):
        if name == "math.pi":
            return math.pi
        if name.startswith(("variable.", "temp.")):
            data = variables if name.startswith("variable.") else temporary
            for part in name.split(".")[1:]:
                data = data.get(part, MISSING) if isinstance(data, dict) else MISSING
            return data if data is not MISSING else MISSING if optional else 0
        if name in context:
            return context[name]
        raise Unsupported(f"Molang query/context not modeled: {name}")

    def visit(node):
        budget[0] += 1
        if budget[0] > 32768:
            raise Unsupported("Molang evaluation budget exceeded")
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "name":
            return read(node[1])
        if kind == "scope":
            for statement in node[1]:
                visit(statement)
            return 0
        if kind in {"return", "break", "continue"}:
            raise Flow(kind, visit(node[1]) if kind == "return" else 0)
        if kind == "assign":
            result = visit(node[2])
            data = variables if node[1].startswith("variable.") else temporary
            parts = node[1].split(".")[1:]
            for part in parts[:-1]:
                if not isinstance(data.get(part), dict):
                    data[part] = {}
                data = data[part]
            data[parts[-1]] = result
            return result
        if kind == "index":
            array, index = visit(node[1]), numeric(visit(node[2]))
            if not isinstance(array, (list, tuple)) or not array:
                raise Unsupported("Molang array must be supplied and nonempty")
            return array[max(0, int(index)) % len(array)]
        if kind == "call":
            name = node[1]
            if name == "loop":
                if len(node[2]) != 2:
                    raise Unsupported("loop requires a count and expression")
                count = min(1024, max(0, int(numeric(visit(node[2][0])))))
                for _ in range(count):
                    try:
                        visit(node[2][1])
                    except Flow as flow:
                        if flow.kind == "break":
                            break
                        if flow.kind != "continue":
                            raise
                return 0
            args = [visit(a) for a in node[2]]
            if name.startswith("math.") and name[5:] in MATH:
                return number_operation(lambda: MATH[name[5:]](*(numeric(a) for a in args)))
            if name in {"query.block_state", "query.property", "query.has_property"}:
                group = "states" if name.endswith("block_state") else "properties"
                if name == "query.has_property" and len(args) == 1:
                    return args[0] in context.get(group, {})
                if len(args) != 1 or args[0] not in context.get(group, {}):
                    raise Unsupported(f"Unknown {name}: {args}")
                return context[group][args[0]]
            if name in context and callable(context[name]):
                return context[name](*args)
            raise Unsupported(f"Molang function not modeled: {name}")
        if kind == "unary":
            result = numeric(visit(node[2]))
            return not result if node[1] == "!" else -result if node[1] == "-" else result
        if kind == "conditional":
            return visit(node[2] if numeric(visit(node[1])) else node[3])
        if kind == "binary":
            op = node[1]
            if op == "??":
                left = read(node[2][1], optional=True) if node[2][0] == "name" else visit(node[2])
                return visit(node[3]) if left is MISSING or left is None else left
            left = visit(node[2])
            if op == "&&":
                return bool(numeric(left)) and bool(numeric(visit(node[3])))
            if op == "||":
                return bool(numeric(left)) or bool(numeric(visit(node[3])))
            right = visit(node[3])
            if op in {"==", "!="}:
                return OPS[op](left, right)
            return number_operation(lambda: OPS[op](numeric(left), numeric(right)))
        raise Unsupported(f"Unknown Molang node {kind}")

    result = 0
    try:
        for node in nodes:
            result = visit(node)
        return 0 if complex_expression else result
    except Flow as flow:
        if flow.kind == "return":
            return flow.value
        raise Unsupported(f"{flow.kind} outside Molang loop") from None
    except (TypeError, IndexError, RecursionError) as exc:
        raise Unsupported(f"Molang evaluation failed: {exc}") from exc
