"""Independent scoreboard state with integer scores and named participants."""

from .diagnostics import Unsupported


class Scoreboard:
    def __init__(self):
        self.objectives = {}

    def api(self, op, identifier=None, *args):
        if op == "list":
            return list(self.objectives)
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128:
            raise ValueError("Invalid objective ID")
        if op == "create":
            if identifier in self.objectives:
                raise ValueError("Objective already exists")
            if len(self.objectives) >= 128:
                raise ValueError("Objective limit")
            self.objectives[identifier] = {"id": identifier, "displayName": str(args[0]), "scores": {}}
            return self.objectives[identifier]
        if op == "get":
            return self.objectives.get(identifier)
        if op == "remove":
            return self.objectives.pop(identifier, None) is not None
        if identifier not in self.objectives:
            raise ValueError("Unknown objective")
        scores = self.objectives[identifier]["scores"]
        if op == "scores":
            return [
                {"participant": {"id": key, "displayName": key}, "score": value}
                for key, value in scores.items()
            ]
        name = args[0]
        if not isinstance(name, str) or len(name) > 128:
            raise ValueError("Invalid participant")
        if op == "score":
            return scores.get(name)
        if op == "removeParticipant":
            return scores.pop(name, None) is not None
        if op not in {"set", "add"}:
            raise Unsupported("Scoreboard operation: " + op)
        value = args[1]
        if type(value) is not int:
            raise ValueError("Score must be an integer")
        if op == "add":
            value += scores.get(name, 0)
        if not -(2**31) <= value < 2**31 or len(scores) >= 4096 and name not in scores:
            raise ValueError("Score/participant limit")
        scores[name] = value
        return value
