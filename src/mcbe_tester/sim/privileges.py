"""Execution-phase rules for the modeled Script API v2 surface."""


def check(phase, operation, method, args):
    if method in {"unsupported", "random", "console", "_endRestricted"}:
        return
    if method == "register":
        if operation != "startup":
            raise ValueError("Custom components can only be registered during system.beforeEvents.startup")
        return
    if phase == "early":
        raise ValueError(
            f"{method} is unavailable during early execution; defer world access until worldLoad/system.run"
        )
    if phase != "restricted":
        return
    read_only = {
        "worldReady",
        "getBlock",
        "blockTags",
        "permutation",
        "itemInfo",
        "itemIds",
        "queryEntities",
        "message",
        "scriptEvent",
    }
    if method in read_only:
        return
    reads = {
        "entity": {"read", "valid"},
        "inventory": {"get", "size", "empty"},
        "dynamic": {"get", "keys"},
        "scoreboard": {"list", "get", "score", "scores"},
        "time": {"get"},
    }
    if method in reads and args and args[0] in reads[method]:
        return
    raise ValueError(f"{method} cannot modify world state during restricted execution; defer with system.run")
