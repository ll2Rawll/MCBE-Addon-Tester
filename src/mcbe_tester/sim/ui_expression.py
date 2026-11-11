"""Typed, bounded JSON UI expressions. No Python or JavaScript evaluation."""

import math
import operator
import re
from functools import lru_cache

from .diagnostics import Unsupported

TOKEN = re.compile(
    r"\s*(?:(\d+(?:\.\d*)?|\.\d+)|('[^']*')|([$#][A-Za-z_][\w.]*)|(true\b|false\b|and\b|or\b|not\b)|(<=|>=|!=|==|[()+*/<>=!\-]))"
)
PREC = {
    "or": 1,
    "and": 2,
    "=": 3,
    "==": 3,
    "!=": 3,
    "<": 4,
    ">": 4,
    "<=": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
}
OPS = {
    "=": operator.eq,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
}


@lru_cache(maxsize=256)
def parse(text):
    if len(text) > 8192:
        raise Unsupported("UI expression exceeds 8 KiB")
    tokens = []
    at = 0
    while at < len(text.rstrip()):
        found = TOKEN.match(text, at)
        if not found:
            raise Unsupported("UI expression syntax near " + repr(text[at : at + 40]))
        tokens.append(next(value for value in found.groups() if value is not None))
        at = found.end()
    tokens.append("<end>")
    index = 0

    def expression(minimum=0, depth=0):
        nonlocal index
        if depth > 48 or index >= len(tokens):
            raise Unsupported("UI expression nesting limit")
        token = tokens[index]
        index += 1
        if token in {"not", "!", "-", "+"}:
            left = ("unary", token, expression(7, depth + 1))
        elif token == "(":
            left = expression(0, depth + 1)
            if index >= len(tokens) or tokens[index] != ")":
                raise Unsupported("Unclosed UI expression")
            index += 1
        elif token.startswith("'"):
            left = ("value", token[1:-1])
        elif token.startswith(("$", "#")):
            left = ("name", token)
        elif token in {"true", "false"}:
            left = ("value", token == "true")
        elif re.fullmatch(r"\d+(?:\.\d*)?|\.\d+", token):
            left = ("value", float(token))
        else:
            raise Unsupported("Unexpected UI expression token " + token)
        while index < len(tokens) and tokens[index] in PREC and PREC[tokens[index]] >= minimum:
            op = tokens[index]
            index += 1
            left = ("binary", op, left, expression(PREC[op] + 1, depth + 1))
        return left

    result = expression()
    if tokens[index] != "<end>":
        raise Unsupported("Trailing UI expression input")
    return result


def evaluate(value, lookup):
    if not isinstance(value, str) or not (
        value.startswith("(") or re.fullmatch(r"[$#][A-Za-z_][\w.]*", value)
    ):
        return value
    root = parse(value)
    count = 0

    def visit(node):
        nonlocal count
        count += 1
        if count > 2048:
            raise Unsupported("UI expression operation budget")
        kind = node[0]
        if kind == "value":
            return node[1]
        if kind == "name":
            return lookup(node[1])
        if kind == "unary":
            value = visit(node[2])
            if node[1] in {"not", "!"}:
                if type(value) not in (bool, int, float):
                    raise Unsupported("UI logical operators require boolean/numeric values")
                return not value
            if type(value) not in (int, float):
                raise Unsupported("UI sign operators require numbers")
            return -value if node[1] == "-" else value
        op = node[1]
        left = visit(node[2])
        if op in {"and", "or"}:
            if type(left) not in (bool, int, float):
                raise Unsupported("UI logical operators require boolean/numeric values")
            if op == "and" and not left or op == "or" and left:
                return bool(left)
            right = visit(node[3])
            if type(right) not in (bool, int, float):
                raise Unsupported("UI logical operators require boolean/numeric values")
            return bool(right)
        right = visit(node[3])
        if op in {"=", "==", "!="}:
            return OPS[op](left, right)
        if isinstance(left, str) and isinstance(right, str) and op in {"+", "-"}:
            return left + right if op == "+" else left.replace(right, "")
        if type(left) not in (int, float) or type(right) not in (int, float):
            raise Unsupported("UI implicit string/number conversion is not modeled")
        try:
            result = OPS[op](left, right)
        except ArithmeticError as exc:
            raise ValueError("Invalid UI arithmetic") from exc
        if isinstance(result, float) and not math.isfinite(result):
            raise ValueError("UI arithmetic must be finite")
        return result

    return visit(root)
