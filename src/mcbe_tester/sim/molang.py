"""Small Pratt parser. Never uses Python eval/exec; unknown queries fail closed."""

import math
import operator
import re

from .diagnostics import Unsupported

TOKEN = re.compile(
    r"\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)|"
    r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|"
    r"([A-Za-z_][A-Za-z_0-9.]*)|(==|!=|<=|>=|&&|\|\||[+*/%<>()!?:,;=\-]))"
)
OPS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "%": operator.mod,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
PREC = {
    "||": 1,
    "&&": 2,
    "==": 3,
    "!=": 3,
    "<": 4,
    "<=": 4,
    ">": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "%": 6,
}
MATH = {
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "sqrt": math.sqrt,
    "sin": lambda x: math.sin(math.radians(x)),
    "cos": lambda x: math.cos(math.radians(x)),
    "min": min,
    "max": max,
    "clamp": lambda x, a, b: max(a, min(b, x)),
    "lerp": lambda a, b, t: a + (b - a) * t,
    "round": lambda x: math.floor(x + 0.5),
    "trunc": math.trunc,
    "pow": lambda a, b: math.pow(a, b),
}


def canonical(name):
    for short, long in (("q.", "query."), ("v.", "variable."), ("t.", "temp."), ("c.", "context.")):
        if name.startswith(short):
            return long + name[len(short) :]
    return name


class Parser:
    def __init__(self, text):
        if len(text) > 8192:
            raise Unsupported("Molang expression exceeds 8 KiB")
        self.tokens = []
        at = 0
        while at < len(text.rstrip()):
            match = TOKEN.match(text, at)
            if not match:
                raise Unsupported(f"Unsupported Molang syntax near {text[at : at + 40]!r}")
            self.tokens.append(next(g for g in match.groups() if g is not None))
            at = match.end()
        self.tokens.append("<end>")
        self.at = 0
        self.depth = 0

    def peek(self):
        return self.tokens[self.at]

    def pop(self, expected=None):
        tok = self.peek()
        if expected and tok != expected:
            raise Unsupported(f"Expected {expected}, found {tok}")
        self.at += 1
        return tok

    def expression(self, minimum=0):
        self.depth += 1
        if self.depth > 64:
            raise Unsupported("Molang nesting limit")
        tok = self.pop()
        if tok in ("!", "-", "+"):
            left = ("unary", tok, self.expression(7))
        elif tok == "(":
            left = self.expression()
            self.pop(")")
        elif tok[0:1] in ("'", '"'):
            left = ("literal", tok[1:-1])
        elif re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", tok):
            left = ("literal", float(tok))
        elif tok in ("true", "false"):
            left = ("literal", tok == "true")
        elif re.fullmatch(r"[a-zA-Z_][\w.]*", tok):
            name = canonical(tok.lower())
            if self.peek() == "(":
                self.pop()
                args = []
                while self.peek() != ")":
                    args.append(self.expression())
                    if self.peek() != ",":
                        break
                    self.pop()
                self.pop(")")
                left = ("call", name, args)
            else:
                left = ("name", name)
        else:
            raise Unsupported(f"Unexpected Molang token {tok}")
        while self.peek() in PREC and PREC[self.peek()] >= minimum:
            op = self.pop()
            left = ("binary", op, left, self.expression(PREC[op] + 1))
        if minimum == 0 and self.peek() == "?":
            self.pop()
            yes = self.expression()
            self.pop(":")
            left = ("conditional", left, yes, self.expression())
        self.depth -= 1
        return left


def evaluate(value, context=None, variables=None):
    if isinstance(value, (int, float, bool)):
        if not math.isfinite(value):
            raise Unsupported("Nonfinite Molang result")
        return value
    if not isinstance(value, str):
        raise Unsupported("Molang requires a number, boolean, or expression string")
    context = context or {}
    variables = variables if variables is not None else {}
    temporary = {}
    parser = Parser(value)

    def visit(node):
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "name":
            name = node[1]
            if name == "math.pi":
                return math.pi
            if name.startswith("variable."):
                return variables.get(name[9:], 0)
            if name.startswith("temp."):
                return temporary.get(name[5:], 0)
            if name in context:
                return context[name]
            raise Unsupported(f"Molang query/context not modeled: {name}")
        if kind == "call":
            name, args = node[1], [visit(a) for a in node[2]]
            if name.startswith("math.") and name[5:] in MATH:
                return MATH[name[5:]](*args)
            if name in {"query.block_state", "query.property"}:
                group = "states" if name.endswith("block_state") else "properties"
                if len(args) != 1 or args[0] not in context.get(group, {}):
                    raise Unsupported(f"Unknown {name}: {args}")
                return context[group][args[0]]
            if name in context and callable(context[name]):
                return context[name](*args)
            raise Unsupported(f"Molang function not modeled: {name}")
        if kind == "unary":
            value = visit(node[2])
            return not value if node[1] == "!" else -value if node[1] == "-" else +value
        if kind == "conditional":
            return visit(node[2] if visit(node[1]) else node[3])
        if kind == "binary":
            op, left = node[1], visit(node[2])
            if op == "&&":
                return bool(left) and bool(visit(node[3]))
            if op == "||":
                return bool(left) or bool(visit(node[3]))
            return OPS[op](left, visit(node[3]))
        raise Unsupported(f"Unknown Molang node {kind}")

    result = 0
    try:
        while parser.peek() != "<end>":
            returned = parser.peek() == "return"
            if returned:
                parser.pop()
            name = canonical(parser.peek())
            if name.startswith(("variable.", "temp.")) and parser.tokens[parser.at + 1] == "=":
                parser.pop()
                parser.pop("=")
                result = visit(parser.expression())
                (variables if name.startswith("variable.") else temporary)[name.split(".", 1)[1]] = result
            else:
                result = visit(parser.expression())
            if parser.peek() == ";":
                parser.pop()
            elif parser.peek() != "<end>":
                raise Unsupported(f"Trailing Molang syntax: {parser.peek()}")
            if returned:
                break
        if isinstance(result, (float, int)) and not math.isfinite(result):
            raise Unsupported("Nonfinite Molang result")
        return result
    except (TypeError, ZeroDivisionError, OverflowError, IndexError) as exc:
        raise Unsupported(f"Molang evaluation failed: {exc}") from exc
