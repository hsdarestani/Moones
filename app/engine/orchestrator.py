import json
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.emotion_engine import detect_emotion
from app.engine.persona_voice_engine import generate_voice_profile
from app.engine.policy_engine import compute_policy
from app.engine.relationship_engine import ensure_relationship, update_state
from app.llm.client import LLMClient
from app.llm.model_router import detect_intent, detect_language, select_model
from app.llm.prompt_builder import build_prompt
from app.llm.response_processor import post_process_response
from app.engine.response_quality_gate import apply_quality_gate
from app.memory.memory_manager import retrieve_memory, update_memory_cadence
from app.models.message import Message
from app.models.user import User
from app.services.onboarding_service import OnboardingService
from app.services.subscription_service import FREE_LIMIT_MESSAGE, PAID_LIMIT_MESSAGE, SubscriptionService
from app.services.settings_service import SettingsService


class ConversationOrchestrator:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()
        self.onboarding = OnboardingService()
        self.subscriptions = SubscriptionService()
        self.settings = SettingsService()

    async def handle_message(self, db: Session, user: User, user_message: str) -> str:
        allowed, _limit, _usage = self.subscriptions.can_send_message(db, user)
        active_sub = self.subscriptions.get_active_subscription(db, user)
        if not allowed:
            db.commit()
            return FREE_LIMIT_MESSAGE if not active_sub or active_sub.plan == "free" else PAID_LIMIT_MESSAGE

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
        partner_profile = self.onboarding.partner_profile(user)
        voice_profile = generate_voice_profile(partner_profile, state, memories, user_message)
        detected_language = detect_language(user_message)
        intent = detect_intent(user_message, emotion.value)
        prompt = build_prompt(user_message, state, emotion, policy, memories, partner_profile, history, voice_profile, detected_language=detected_language)
        user.last_prompt = "\n\n".join(f"{item['role']}: {item['content']}" for item in prompt)
        llm_model = select_model(
            user_message,
            detected_language,
            state.stage,
            intent,
            previous_output_quality_failed=bool(user.last_quality_gate_rejected),
            allow_persian_uncensored_roleplay=self.settings.get_bool(db, "llm.allow_persian_uncensored_roleplay", False),
            primary_persian_model=self.settings.get_str(db, "llm.primary_persian_model", "zai-org-glm-5-1"),
            roleplay_model=self.settings.get_str(db, "llm.roleplay_model", "venice-uncensored-role-play"),
        )
        parameters = {"temperature": 0.55, "top_p": 0.85, "frequency_penalty": 0.8, "presence_penalty": 0.2, "max_tokens": 180} if llm_model == "zai-org-glm-5-1" else None
        result = await self.llm_client.complete_result(prompt, model=llm_model, parameters=parameters)
        raw_response = result.text
        recent_assistant_messages = self._recent_assistant_messages(db, user.id)
        response, response_flags = post_process_response(raw_response, voice_profile, recent_assistant_messages, user_message)
        quality = apply_quality_gate(response, intent, recent_assistant_messages) if self.settings.get_bool(db, "quality_gate.enabled", True) else None
        if quality:
            response = quality.final_text
        user.last_llm_response = raw_response
        user.last_processed_response = response
        user.last_detected_language = detected_language
        user.last_quality_gate_result = "rejected" if quality and quality.rejected else "accepted"
        user.last_quality_gate_reason = quality.reason if quality else "disabled"
        user.last_quality_gate_rejected = bool(quality and quality.rejected)
        user.last_voice_profile = json.dumps(voice_profile, ensure_ascii=False)
        user.last_garbage_filter_triggered = response_flags["garbage_filter_triggered"]
        user.last_repetition_filter_triggered = response_flags["repetition_filter_triggered"]
        user.last_llm_provider = result.provider
        user.last_llm_model = result.model
        user.last_llm_status_code = result.status_code
        user.last_llm_error = result.error
        user.last_input_tokens = result.input_tokens
        user.last_output_tokens = result.output_tokens

        db.add(Message(user_id=user.id, role="user", content=user_message, emotion=emotion.value))
        db.add(Message(user_id=user.id, role="assistant", content=response))
        self.subscriptions.record_successful_llm_response(db, user, result.input_tokens, result.output_tokens)
        update_memory_cadence(db, user.id, user_message, emotion.value)
        old_stage = state.stage
        update_state(state, message_count + 1, emotion, previous_seen)
        if state.stage != old_stage:
            from app.models.memory import MemoryItem
            db.add(MemoryItem(user_id=user.id, type="relationship_milestone", content=f"Relationship stage changed from {old_stage} to {state.stage}.", importance_score=0.85))
        user.last_seen_at = datetime.utcnow()
        db.commit()
        return response

    def _recent_history(self, db: Session, user_id: int, limit: int = 8) -> list[str]:
        rows = db.scalars(
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
        return [f"{message.role}: {message.content}" for message in reversed(rows)]

    def _recent_assistant_messages(self, db: Session, user_id: int, limit: int = 10) -> list[str]:
        rows = db.scalars(
            select(Message)
            .where(Message.user_id == user_id, Message.role == "assistant")
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
        return [message.content for message in reversed(rows)]
