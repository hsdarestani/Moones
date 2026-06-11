from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.memory import MemoryItem
from app.models.message import Message

EMOTIONAL_MARKERS = (
    "feel", "miss", "sad", "happy", "lonely", "love", "حس", "دلتنگ", "غمگین", "خوشحال", "تنها", "دوست دارم", "عاشق"
)
MILESTONE_MARKERS = ("first time", "trust you", "i love you", "اسمم", "یادت باشه", "بهت اعتماد", "دوست دارم")


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


def memory_summary(db: Session, user_id: int, limit: int = 6) -> str:
    items = db.scalars(
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id)
        .order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc())
        .limit(limit)
    ).all()
    return "\n".join(item.content for item in items) or "No memory yet."


def update_memory_cadence(db: Session, user_id: int, user_message: str, emotion: str) -> list[MemoryItem]:
    created: list[MemoryItem] = []
    lowered = user_message.lower()
    user_count = db.scalar(select(func.count(Message.id)).where(Message.user_id == user_id, Message.role == "user")) or 0
    if (user_count + 1) % 4 == 0:
        item = remember_if_important(db, user_id, user_message, "event", minimum=0.30)
        if item:
            created.append(item)
    if emotion != "neutral" or any(marker in lowered for marker in EMOTIONAL_MARKERS):
        item = remember_if_important(db, user_id, f"Emotional event: {user_message}", "emotional_event", minimum=0.30)
        if item:
            created.append(item)
    if any(marker in lowered for marker in MILESTONE_MARKERS):
        item = remember_if_important(db, user_id, f"Relationship milestone: {user_message}", "relationship_milestone", minimum=0.30)
        if item:
            created.append(item)
    return created


def remember_if_important(db: Session, user_id: int, content: str, memory_type: str = "event", minimum: float = 0.45) -> MemoryItem | None:
    importance = _importance(content)
    if importance < minimum:
        return None
    item = MemoryItem(user_id=user_id, type=memory_type, content=content, importance_score=importance)
    db.add(item)
    return item


def _importance(content: str) -> float:
    lowered = content.lower()
    if any(marker in lowered for marker in ("i love", "my name", "remember", "favorite", "دوست دارم", "اسمم", "یادت باشه")):
        return 0.9
    if any(marker in lowered for marker in EMOTIONAL_MARKERS):
        return 0.7
    if len(content) > 80:
        return 0.5
    return 0.35
