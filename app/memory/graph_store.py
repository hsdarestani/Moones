from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(slots=True)
class RelationshipGraph:
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def connect(self, source: str, target: str) -> None:
        self.edges[source].add(target)

    def related_to(self, source: str) -> set[str]:
        return self.edges.get(source, set())
