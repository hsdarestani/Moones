import json
import logging
import time
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.emotion_engine import detect_emotion
from app.engine.persona_voice_engine import generate_voice_profile
from app.engine.policy_engine import compute_policy
from app.engine.context_aware_fallback import context_aware_fallback
from app.engine.fast_response_engine import fast_response
from app.engine.safety_handler import safety_response
from app.engine.situation_detector import SIMPLE_INTENTS, PROFILE_INTENTS, detect_situation
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

logger = logging.getLogger(__name__)


class ConversationOrchestrator:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()
        self.onboarding = OnboardingService()
        self.subscriptions = SubscriptionService()
        self.settings = SettingsService()

    async def handle_message(self, db: Session, user: User, user_message: str) -> str:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        llm_called = False
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

        db_load_started = time.perf_counter()
        recent_user_messages = self._recent_user_messages(db, user.id, limit=5)
        timings["db_load_ms"] = _ms(db_load_started)
        detect_started = time.perf_counter()
        situation = detect_situation(user_message, recent_user_messages)
        timings["situation_detection_ms"] = _ms(detect_started)
        partner_profile = self.onboarding.partner_profile(user)
        safety_flag = str(situation.get("intent")) == "self_harm_signal"
        simple_reply = safety_response(user_message, user.display_name) if safety_flag else fast_response(user_message, situation, partner_profile)
        simple_intent_bypass = simple_reply is not None and (safety_flag or str(situation.get("intent")) in SIMPLE_INTENTS or str(situation.get("intent")) in PROFILE_INTENTS)
        logger.info(
            "PIPELINE raw_user_message=%r detected_intent=%s confidence=%s why=%s matched_keywords=%s context_used=%s context_reset=%s fast_response_used=%s llm_called=false fallback_replaced_llm=false",
            user_message,
            situation.get("intent"),
            situation.get("confidence"),
            situation.get("why"),
            situation.get("matched_keywords"),
            situation.get("context_used"),
            situation.get("context_reset"),
            bool(simple_intent_bypass),
        )
        memory_started = time.perf_counter()
        memories = [] if simple_intent_bypass else retrieve_memory(db, user.id, user_message)
        timings["memory_retrieval_ms"] = _ms(memory_started)
        message_count = db.scalar(select(func.count(Message.id)).where(Message.user_id == user.id)) or 0
        policy = compute_policy(state, emotion)
        history = self._recent_history(db, user.id, limit=3 if simple_intent_bypass else 5)
        if simple_reply:
            response = simple_reply
            user.last_llm_response = "[safety_response]" if safety_flag else "[deterministic_fast_path]"
            user.last_processed_response = response
            user.last_detected_language = detect_language(user_message)
            user.last_quality_gate_result = "accepted"
            user.last_quality_gate_reason = "safety_response" if safety_flag else "deterministic_fast_path"
            user.last_quality_gate_rejected = False
            user.last_detected_situation = json.dumps(situation, ensure_ascii=False)
            user.last_context_reset = bool(situation.get("context_should_reset"))
            user.last_safety_flag = safety_flag
            user.last_fallback_used = False
            user.last_fallback_reason = ""
            user.last_context_messages_used = json.dumps(recent_user_messages[-3:], ensure_ascii=False)
            user.last_simple_intent_bypass = True
            user.last_latency_breakdown = json.dumps({**timings, "total_request_ms": _ms(started), "llm_ms": 0, "quality_gate_ms": 0, "humanizer_ms": 0}, ensure_ascii=False)
            user.last_llm_called = False
            logger.info(
                "PIPELINE_FINAL user_id=%s message=%r intent=%s confidence=%s llm_called=%s model=%s http_status=%s raw_text_len=%s processed_text_len=%s retry_used=%s quality_rejected=%s fallback_used=%s fallback_reason=%s final_response=%r",
                user.id, user_message, situation.get("intent"), situation.get("confidence"), False, None, None, 0, len(response or ""), False, False, False, "", response,
            )
            db.add(Message(user_id=user.id, role="user", content=user_message, emotion=emotion.value))
            db.add(Message(user_id=user.id, role="assistant", content=response))
            update_memory_cadence(db, user.id, user_message, emotion.value)
            update_state(state, message_count + 1, emotion, previous_seen)
            user.last_seen_at = datetime.utcnow()
            db.commit()
            _log_perf(user.id, str(situation.get("intent")), False, timings, started, llm_ms=0, quality_ms=0)
            return response
        voice_profile = generate_voice_profile(partner_profile, state, memories, user_message)
        detected_language = detect_language(user_message)
        intent = detect_intent(user_message, emotion.value)
        prompt = build_prompt(user_message, state, emotion, policy, memories, partner_profile, history, voice_profile, detected_language=detected_language, situation=situation)
        user.last_prompt = "\n\n".join(f"{item['role']}: {item['content']}" for item in prompt)
        llm_model = select_model(
            user_message,
            detected_language,
            state.stage,
            intent,
            previous_output_quality_failed=bool(user.last_quality_gate_rejected),
            allow_persian_uncensored_roleplay=self.settings.get_bool(db, "llm.allow_persian_uncensored_roleplay", False),
            primary_persian_model=self.settings.get_str(db, "llm.primary_persian_model", "qwen-3-6-plus"),
            roleplay_model=self.settings.get_str(db, "llm.roleplay_model", "venice-uncensored-role-play"),
        )
        parameters = {"temperature": 0.55, "top_p": 0.85, "frequency_penalty": 0.8, "presence_penalty": 0.2, "max_tokens": 120}
        llm_started = time.perf_counter()
        result = await self.llm_client.complete_result(prompt, model=llm_model, parameters=parameters, timeout=9)
        timings["llm_ms"] = _ms(llm_started)
        llm_called = True
        retry_used = False
        raw_response = result.text or ""
        recent_assistant_messages = self._recent_assistant_messages(db, user.id)
        if result.status_code == 200 and not raw_response.strip():
            logger.warning("LLM_EMPTY_HTTP_200 user_id=%s model=%s extraction_error=%s raw=%s", user.id, result.model, result.extraction_error or result.error, (result.raw_response_text or "")[:2000])
            retry_prompt = [
                {"role": "system", "content": "You are a natural Persian digital partner. Reply in casual Iranian Persian, short and human. Do not mention being an AI. Do not return empty output."},
                {"role": "user", "content": user_message},
            ]
            retry_started = time.perf_counter()
            retry_result = await self.llm_client.complete_result(retry_prompt, model=llm_model, parameters={"temperature": 0.65, "top_p": 0.9, "max_tokens": 160}, timeout=9)
            timings["llm_retry_ms"] = _ms(retry_started)
            retry_used = True
            if retry_result.text.strip():
                result = retry_result
                raw_response = retry_result.text
        response, response_flags = post_process_response(raw_response, voice_profile, recent_assistant_messages, user_message, allow_fallback=False)
        forced_fallback_reason = ""
        fallback_replaced_llm = False
        quality_started = time.perf_counter()
        quality = apply_quality_gate(response, intent, recent_assistant_messages, situation, user_message, recent_user_messages, partner_profile) if self.settings.get_bool(db, "quality_gate.enabled", True) else None
        if quality and quality.rejected and response.strip() and quality.reason not in {"empty", "foreign_fragments", "repeated_phrase_loop", "incomplete_sentence", "not_persian"}:
            quality = None
        if quality and quality.rejected and raw_response.strip() and response.strip():
            # Preserve successful LLM output; quality gate may clean, not replace.
            response = quality.final_text or response
        if (not raw_response.strip()) or (quality and quality.rejected and quality.reason in {"empty", "foreign_fragments", "repeated_phrase_loop", "not_persian"}):
            if not retry_used:
                retry_prompt = [
                    {"role": "system", "content": "You are a natural Persian digital partner. Reply in casual Iranian Persian, short and human. Do not mention being an AI. Do not return empty output."},
                    {"role": "user", "content": user_message},
                ]
                retry_started = time.perf_counter()
                retry_result = await self.llm_client.complete_result(retry_prompt, model=llm_model, parameters={"temperature": 0.65, "top_p": 0.9, "max_tokens": 160}, timeout=9)
                timings["llm_retry_ms"] = _ms(retry_started)
                retry_used = True
                if retry_result.text.strip():
                    result = retry_result
                    raw_response = retry_result.text
                    response, response_flags = post_process_response(raw_response, voice_profile, recent_assistant_messages, user_message, allow_fallback=False)
                    quality = apply_quality_gate(response, intent, recent_assistant_messages, situation, user_message, recent_user_messages, partner_profile) if self.settings.get_bool(db, "quality_gate.enabled", True) else None
            if not raw_response.strip() or (quality and quality.rejected and quality.reason in {"empty", "foreign_fragments", "repeated_phrase_loop", "not_persian"}):
                response = context_aware_fallback(situation, user_message, recent_user_messages, partner_profile, recent_assistant_messages)
                if response in recent_assistant_messages[-5:]:
                    response = "یه لحظه قاطی کردم، ولی هستم. دوباره بگو ببینم چی می‌خواستی؟"
                forced_fallback_reason = f"llm_error:{result.extraction_error or result.error or (quality.reason if quality else 'empty_after_retry')}"
                fallback_replaced_llm = True
        timings["quality_gate_ms"] = _ms(quality_started)
        user.last_llm_response = raw_response
        if hasattr(user, "last_raw_llm_response"):
            user.last_raw_llm_response = result.raw_response_text
        if hasattr(user, "last_llm_extraction_path"):
            user.last_llm_extraction_path = result.extraction_path
        if hasattr(user, "last_llm_retry_used"):
            user.last_llm_retry_used = retry_used
        user.last_processed_response = response
        user.last_detected_language = detected_language
        user.last_quality_gate_result = "rejected" if forced_fallback_reason or (quality and quality.rejected) else "accepted"
        user.last_quality_gate_reason = forced_fallback_reason or (quality.reason if quality else "disabled")
        user.last_quality_gate_rejected = bool(forced_fallback_reason or (quality and quality.rejected))
        user.last_detected_situation = json.dumps(situation, ensure_ascii=False)
        user.last_context_reset = bool(situation.get("context_should_reset"))
        user.last_safety_flag = False
        user.last_fallback_used = bool(forced_fallback_reason)
        user.last_fallback_reason = forced_fallback_reason
        user.last_context_messages_used = json.dumps(recent_user_messages[-5:], ensure_ascii=False)
        user.last_voice_profile = json.dumps(voice_profile, ensure_ascii=False)
        user.last_garbage_filter_triggered = response_flags["garbage_filter_triggered"]
        user.last_repetition_filter_triggered = response_flags["repetition_filter_triggered"]
        user.last_llm_provider = result.provider
        user.last_llm_model = result.model
        user.last_llm_status_code = result.status_code
        user.last_llm_error = result.extraction_error or result.error
        user.last_input_tokens = result.input_tokens
        user.last_output_tokens = result.output_tokens
        user.last_simple_intent_bypass = simple_intent_bypass
        user.last_latency_breakdown = json.dumps({**timings, "total_request_ms": _ms(started), "humanizer_ms": timings.get("quality_gate_ms", 0)}, ensure_ascii=False)
        user.last_llm_called = llm_called

        logger.info(
            "PIPELINE_FINAL user_id=%s message=%r intent=%s confidence=%s llm_called=%s model=%s http_status=%s raw_text_len=%s processed_text_len=%s retry_used=%s quality_rejected=%s fallback_used=%s fallback_reason=%s final_response=%r",
            user.id, user_message, situation.get("intent"), situation.get("confidence"), llm_called, result.model, result.status_code, len(raw_response or ""), len(response or ""), retry_used, bool(quality and quality.rejected), bool(forced_fallback_reason), forced_fallback_reason, response,
        )

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
        logger.info(
            "PIPELINE raw_user_message=%r detected_intent=%s confidence=%s why=%s matched_keywords=%s context_used=%s context_reset=%s fast_response_used=false llm_called=%s llm_error=%s fallback_replaced_llm=%s quality_gate_result=%s quality_gate_reason=%s",
            user_message,
            situation.get("intent"),
            situation.get("confidence"),
            situation.get("why"),
            situation.get("matched_keywords"),
            situation.get("context_used"),
            situation.get("context_reset"),
            str(llm_called).lower(),
            bool(result.error),
            fallback_replaced_llm,
            user.last_quality_gate_result,
            user.last_quality_gate_reason,
        )
        _log_perf(user.id, str(situation.get("intent")), llm_called, timings, started, llm_ms=timings.get("llm_ms", 0), quality_ms=timings.get("quality_gate_ms", 0))
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

    def _recent_user_messages(self, db: Session, user_id: int, limit: int = 5) -> list[str]:
        rows = db.scalars(
            select(Message)
            .where(Message.user_id == user_id, Message.role == "user")
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
        return [message.content for message in reversed(rows)]


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _log_perf(user_id: int, intent: str, llm_called: bool, timings: dict[str, float], started: float, llm_ms: float, quality_ms: float) -> None:
    total = _ms(started)
    logger.info(
        "PERF user_id=%s intent=%s llm_called=%s total_ms=%s db_ms=%s situation_ms=%s memory_ms=%s llm_ms=%s quality_ms=%s telegram_ms=%s",
        user_id,
        intent,
        str(llm_called).lower(),
        total,
        timings.get("db_load_ms", 0),
        timings.get("situation_detection_ms", 0),
        timings.get("memory_retrieval_ms", 0),
        llm_ms,
        quality_ms,
        timings.get("telegram_ms", 0),
    )
