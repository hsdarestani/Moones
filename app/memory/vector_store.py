from dataclasses import dataclass, field
from math import sqrt


@dataclass(slots=True)
class InMemoryVectorStore:
    vectors: dict[int, list[float]] = field(default_factory=dict)

    def upsert(self, item_id: int, embedding: list[float]) -> None:
        self.vectors[item_id] = embedding

    def search(self, query_embedding: list[float], limit: int = 5) -> list[int]:
        scored = [(item_id, _cosine(query_embedding, vector)) for item_id, vector in self.vectors.items()]
        return [item_id for item_id, _ in sorted(scored, key=lambda pair: pair[1], reverse=True)[:limit]]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
