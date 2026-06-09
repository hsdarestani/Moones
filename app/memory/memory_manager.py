from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory import MemoryItem


def retrieve_memory(db: Session, user_id: int, query: str, limit: int = 5) -> list[MemoryItem]:
    words = {word.lower() for word in query.split() if len(word) > 2}
    memories = db.scalars(
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id)
        .order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc())
        .limit(25)
    ).all()
    ranked = sorted(
        memories,
        key=lambda item: (len(words.intersection(item.content.lower().split())), item.importance_score),
        reverse=True,
    )
    return ranked[:limit]


def remember_if_important(db: Session, user_id: int, content: str, memory_type: str = "event") -> MemoryItem | None:
    importance = _importance(content)
    if importance < 0.45:
        return None
    item = MemoryItem(user_id=user_id, type=memory_type, content=content, importance_score=importance)
    db.add(item)
    return item


def _importance(content: str) -> float:
    lowered = content.lower()
    if any(marker in lowered for marker in ("i love", "my name", "remember", "favorite", "دوست دارم", "اسمم")):
        return 0.9
    if any(marker in lowered for marker in ("feel", "miss", "sad", "happy", "حس", "دلتنگ")):
        return 0.65
    return 0.35
