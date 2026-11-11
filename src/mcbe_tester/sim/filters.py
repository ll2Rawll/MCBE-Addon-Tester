"""Public entity filter operators; unknown tests are never interpreted as false."""

import operator

from .diagnostics import Unsupported


def matches(world, spec, entity, other=None):
    if isinstance(spec, list):
        return all(matches(world, item, entity, other) for item in spec)
    if not isinstance(spec, dict):
        raise ValueError("Filter must be an object")
    for key, aggregate in (("all_of", all), ("any_of", any), ("none_of", lambda values: not any(values))):
        if key in spec:
            return aggregate(matches(world, item, entity, other) for item in spec[key])
    subject = spec.get("subject", "self")
    if subject == "other":
        if other is None:
            raise Unsupported("Filter requires an other subject")
        entity = other
    elif subject != "self":
        raise Unsupported("Filter subject: " + subject)
    test, value = spec["test"], spec.get("value", True)
    if test == "is_family":
        actual, value = (
            value in entity.get("components", {}).get("minecraft:type_family", {}).get("family", []),
            True,
        )
    elif test == "has_tag":
        actual, value = value in entity.get("tags", []), True
    elif test == "has_component":
        actual, value = value in entity.get("components", {}), True
    elif test in {"bool_property", "int_property", "float_property", "enum_property"}:
        actual = entity.get("properties", {}).get(spec.get("domain"))
    elif test == "is_alive":
        actual = entity.get("health", 0) > 0
    elif test == "is_on_ground":
        actual = entity.get("on_ground", False)
    elif test == "is_sneaking":
        actual = entity.get("sneaking", False)
    elif test == "health":
        actual = entity.get("health", 0)
    else:
        raise Unsupported("Entity filter test: " + test)
    ops = {
        "equals": operator.eq,
        "==": operator.eq,
        "=": operator.eq,
        "not": operator.ne,
        "!=": operator.ne,
        "<": operator.lt,
        "less": operator.lt,
        ">": operator.gt,
        "greater": operator.gt,
        "<=": operator.le,
        ">=": operator.ge,
    }
    op = spec.get("operator", "equals")
    if op not in ops:
        raise Unsupported("Filter operator: " + op)
    return ops[op](actual, value)
