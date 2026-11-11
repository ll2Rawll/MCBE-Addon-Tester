"""Explicit fidelity accounting; simulated success never means native compatibility."""

from dataclasses import asdict, dataclass


class Unsupported(ValueError):
    pass


@dataclass(frozen=True)
class Finding:
    severity: str
    feature: str
    location: str
    message: str


class Findings:
    def __init__(self):
        self._items = {}

    def add(self, severity, feature, location, message):
        item = Finding(severity, feature, location, str(message)[:1000])
        if len(self._items) < 5000:
            self._items[(severity, feature, location, item.message)] = item

    def unsupported(self, feature, location, message="Not implemented in this simulation profile"):
        self.add("unsupported", feature, location, message)

    def error(self, feature, location, message):
        self.add("error", feature, location, message)

    def approximate(self, feature, location, message):
        self.add("approximate", feature, location, message)

    def rows(self):
        return [
            asdict(v) for v in sorted(self._items.values(), key=lambda v: (v.severity, v.location, v.feature))
        ]

    def blockers(self):
        return [row for row in self.rows() if row["severity"] in {"unsupported", "error"}]
