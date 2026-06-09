from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.emotion_engine import detect_emotion
from app.engine.policy_engine import compute_policy
from app.engine.relationship_engine import ensure_relationship, update_state
from app.llm.client import LLMClient
from app.llm.prompt_builder import build_prompt
from app.llm.response_processor import post_process_response
from app.memory.memory_manager import remember_if_important, retrieve_memory
from app.models.message import Message
from app.models.user import User


class ConversationOrchestrator:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    async def handle_message(self, db: Session, telegram_id: int, display_name: str | None, user_message: str) -> str:
        user = self._get_or_create_user(db, telegram_id, display_name)
        previous_seen = user.last_seen_at
        emotion = detect_emotion(user_message)
        state = ensure_relationship(user.id, user.relationship_state)
        if state.id is None:
            db.add(state)
            db.flush()

        memories = retrieve_memory(db, user.id, user_message)
        message_count = db.scalar(select(func.count(Message.id)).where(Message.user_id == user.id)) or 0
        policy = compute_policy(state, emotion)
        history = self._recent_history(db, user.id)
        prompt = build_prompt(user_message, state, emotion, policy, memories, history)
        raw_response = await self.llm_client.complete(prompt)
        response = post_process_response(raw_response)

        db.add(Message(user_id=user.id, role="user", content=user_message, emotion=emotion.value))
        db.add(Message(user_id=user.id, role="assistant", content=response))
        remember_if_important(db, user.id, user_message, "emotion" if emotion.value != "neutral" else "event")
        update_state(state, message_count + 1, emotion, previous_seen)
        user.last_seen_at = datetime.utcnow()
        db.commit()
        return response

    def _get_or_create_user(self, db: Session, telegram_id: int, display_name: str | None) -> User:
        user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user:
            user.display_name = display_name or user.display_name
            return user
        user = User(telegram_id=telegram_id, display_name=display_name)
        db.add(user)
        db.flush()
        return user

    def _recent_history(self, db: Session, user_id: int, limit: int = 8) -> list[str]:
        rows = db.scalars(
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
        return [f"{message.role}: {message.content}" for message in reversed(rows)]
