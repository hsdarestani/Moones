import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.message import Message
from app.models.user import User
from app.evaluation.semantic_image_intent_eval import evaluate_predictions, load_dataset
from app.services.semantic_image_intent_router import (
    SemanticImageDecision,
    SemanticImageIntentRouter,
    SemanticImageRouterContext,
    VisualIntent,
    canonical_standalone_image_action,
    mark_image_clarification_resolved,
    normalize_image_clarification_text,
    resolve_pending_image_clarification,
    semantic_shadow_log_event,
    validate_source_reference_deterministically,
)

DATASET = Path("app/evaluation/image_semantic_intent_dataset.json")


def _clarification_db(created_at=None):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Message.__table__])
    db = sessionmaker(bind=engine)()
    user = User(telegram_id=991)
    db.add(user); db.flush()
    clarification = Message(
        user_id=user.id, role="assistant", content="clarify", telegram_message_id=501,
        input_type="image_clarification", created_at=created_at or datetime.utcnow(),
        metadata_json={"source":"semantic_image_router", "kind":"pending_image_clarification", "status":"pending", "options":["generate_new", "refine_previous", "chat"], "source_user_telegram_message_id":41},
    )
    db.add(clarification); db.commit()
    return db, user, clarification


def test_pending_clarification_canonical_answers_resolve_without_model():
    expected = {
        "عکس جدید": "generate_new", "یه عکس جدید": "generate_new", "عکس تازه": "generate_new",
        "جدید": "generate_new", "جدید بساز": "generate_new", "از اول بساز": "generate_new",
        "تغییر عکس قبلی": "refine_previous", "قبلی رو تغییر بده": "refine_previous",
        "عکس قبلی رو ویرایش کن": "refine_previous", "ادیت عکس قبلی": "refine_previous",
        "همون قبلی رو درست کن": "refine_previous", "فقط دارم درباره‌ش حرف می‌زنم": "chat",
        "فقط حرف می‌زنم": "chat", "عکس نمی‌خوام": "chat", "سوال بود": "chat", "منظورم گفتگو بود": "chat",
    }
    for text, action in expected.items():
        db, user, _ = _clarification_db()
        resolution = resolve_pending_image_clarification(db, user_id=user.id, text=text)
        assert resolution is not None and resolution.action == action
        db.close()


def test_resolved_and_expired_clarifications_cannot_be_reused():
    db, user, clarification = _clarification_db()
    db.add(Message(user_id=user.id, role='user', content='بفرس', telegram_message_id=41, input_type='text'))
    db.commit()
    resolution = resolve_pending_image_clarification(db, user_id=user.id, text="عکس جدید")
    mark_image_clarification_resolved(resolution, telegram_message_id=42)
    db.commit()
    assert resolve_pending_image_clarification(db, user_id=user.id, text="عکس جدید") is None
    assert clarification.metadata_json["resolved_action"] == "generate_new"
    assert clarification.metadata_json["resolved_by_telegram_message_id"] == 42

    expired_db, expired_user, _ = _clarification_db(datetime.utcnow() - timedelta(minutes=6))
    assert resolve_pending_image_clarification(expired_db, user_id=expired_user.id, text="عکس جدید") is None


def test_normalization_and_standalone_fallback_are_narrow():
    assert normalize_image_clarification_text("  فقط درباره\u200cش حرف می‌زنم؟ ") == "فقط درباره ش حرف می زنم"
    assert canonical_standalone_image_action("عکس جدید!") == "generate_new"
    assert canonical_standalone_image_action("عکس قبلی چرا مصنوعی بود؟") is None


class FixtureSemanticModel:
    async def classify(self, payload):
        msg = payload["current_user_message"]
        cases = {c["current_message"]: c for c in load_dataset(DATASET)}
        c = cases[msg]
        return {
            "action": c["expected_action"],
            "media_delivery_requested": c["expected_media_delivery_requested"],
            "confidence": 0.93 if c["expected_action"] != "clarify" else 0.62,
            "reason_code": "fixture_semantic_label",
            "needs_clarification": c["expected_clarification_behavior"]["needs_clarification"],
            "source_reference": {"kind": "recent_image", "job_id": 10} if c["recent_image_context"].get("exists") else None,
            "visual_intent": c["expected_visual_constraints"],
            "safety_relevant_signals": {"classification": c["expected_safety_classification"]},
        }


def test_required_distinctions_are_semantic_not_keyword_routes():
    async def run():
        router = SemanticImageIntentRouter(FixtureSemanticModel())
        expected = {
            "عکس قبلی چرا مصنوعی بود؟": "chat",
            "عکس گرفتن توی دانشگاه ممنوعه؟": "chat",
            "عکس قبلی رو درست کن": "refine_previous",
            "همون عکس رو دوباره بفرست": "resend_exact",
            "یکی دیگه شبیه قبلی": "variation",
            "دلم میخواد ببینمت": "generate_new",
            "الان چه شکلی شدی؟ بذار ببینمت": "generate_new",
            "بازوهات درد می‌کنه؟": "chat",
            "بازوهاتو ببینم": "generate_new",
        }
        for msg, action in expected.items():
            decision = await router.decide(SemanticImageRouterContext(current_user_message=msg), shadow_or_evaluation=True)
            assert decision.action == action
    asyncio.run(run())


def test_metamorphic_semantic_action_invariance():
    async def run():
        router = SemanticImageIntentRouter(FixtureSemanticModel())
        groups = [
            ["یه عکس بده", "یه عکسی از خودت بفرست", "بذار ببینمت", "دلم میخواد ببینمت", "الان چه شکلی هستی نشونم بده"],
            ["کیف دستت باشه", "لیوان دستت باشه", "کتاب دستت باشه", "گل دستت باشه"],
        ]
        for group in groups:
            actions = {(await router.decide(SemanticImageRouterContext(current_user_message=m))).action for m in group}
            assert actions == {"generate_new"}
    asyncio.run(run())


def test_negation_discussion_and_critique_change_meaning():
    async def run():
        router = SemanticImageIntentRouter(FixtureSemanticModel())
        pairs = [
            ("کفش‌هات معلوم باشه", "کفش‌هات معلوم نباشه"),
            ("بازوهاتو نشون بده", "چرا بازوت درد می‌کنه؟"),
            ("بازوهاتو نشون بده", "درباره عضلات بازو توضیح بده"),
            ("عکس قبلی تار بود", "عکس قبلی تار بود، درستش کن"),
        ]
        for left, right in pairs:
            l = await router.decide(SemanticImageRouterContext(current_user_message=left))
            r = await router.decide(SemanticImageRouterContext(current_user_message=right))
            assert (l.action, l.visual_intent) != (r.action, r.visual_intent)
    asyncio.run(run())


def test_dataset_has_required_size_and_fields():
    cases = load_dataset(DATASET)
    assert len(cases) >= 300
    required = {"current_message", "recent_conversation", "recent_image_context", "expected_action", "expected_media_delivery_requested", "expected_visual_constraints", "expected_clarification_behavior", "expected_safety_classification"}
    assert all(required <= set(c) for c in cases)
    assert {"chat", "generate_new", "refine_previous", "variation", "resend_exact", "clarify"} <= {c["expected_action"] for c in cases}


def test_shadow_log_is_compact_and_redacted():
    ctx = SemanticImageRouterContext(current_user_message="یه عکس بده")
    dec = SemanticImageDecision(action="generate_new", media_delivery_requested=True, confidence=.91, reason_code="semantic", visual_intent=VisualIntent(held_objects=["book"]))
    event = semantic_shadow_log_event(ctx, dec, ["ok"])
    text = json.dumps(event, ensure_ascii=False)
    assert event["event"] == "IMAGE_SEMANTIC_ROUTE_SHADOW"
    assert "یه عکس بده" not in text
    assert "positive_prompt" not in text and "negative_prompt" not in text and "identity" not in text
    assert event["extracted_field_names"] == ["held_objects"]


def test_source_reference_validation_remains_deterministic():
    dec = SemanticImageDecision(action="resend_exact", media_delivery_requested=True, confidence=.95, reason_code="semantic", source_reference={"kind":"recent_image","job_id":99})
    assert validate_source_reference_deterministically(dec, recent_retrievable_image_exists=False, allowed_job_ids={99}) == (False, "no_recent_retrievable_image")
    assert validate_source_reference_deterministically(dec, recent_retrievable_image_exists=True, allowed_job_ids={10}) == (False, "source_job_out_of_scope")
    assert validate_source_reference_deterministically(dec, recent_retrievable_image_exists=True, allowed_job_ids={99}) == (True, None)


def test_offline_metrics_shape():
    cases = load_dataset(DATASET)[:30]
    preds = [{"action": c["expected_action"], "media_delivery_requested": c["expected_media_delivery_requested"], "visual_intent": c["expected_visual_constraints"], "source_reference_valid": True} for c in cases]
    metrics = evaluate_predictions(cases, preds)
    assert "by_action" in metrics
    assert metrics["false_image_generation_rate"] == 0
    assert metrics["billing_before_confirmed_image_intent_count"] == 0


def test_canonical_explicit_image_action_direct_generate_requests():
    from app.services.semantic_image_intent_router import canonical_explicit_image_action
    positives = [
        "یه عکس بده",
        "عکس بده",
        "یه عکس بفرست",
        "عکس بفرست",
        "یه عکس از خودت بفرست",
        "عکستو بفرست",
        "یه عکس بده ببینمت خبب",
        "بذار ببینمت",
        "میخوام ببینمت",
        "نشونم بده",
        "الان یه عکس بده",
        "عکس جدید",
        "یه عکس جدید",
        "عکس تازه",
    ]
    for text in positives:
        assert canonical_explicit_image_action(text) == "generate_new", text


def test_canonical_explicit_image_action_previous_image_actions():
    from app.services.semantic_image_intent_router import canonical_explicit_image_action
    assert canonical_explicit_image_action("عکس قبلی رو درست کن") == "refine_previous"
    assert canonical_explicit_image_action("عکس قبلی رو تغییر بده") == "refine_previous"
    assert canonical_explicit_image_action("عکس قبلی رو ویرایش کن") == "refine_previous"
    assert canonical_explicit_image_action("همون عکس رو بهتر کن") == "refine_previous"
    assert canonical_explicit_image_action("این بار لباسش رو عوض کن") == "refine_previous"
    assert canonical_explicit_image_action("یکی دیگه شبیه قبلی") == "variation"
    assert canonical_explicit_image_action("یه عکس دیگه مثل قبلی") == "variation"
    assert canonical_explicit_image_action("همونجوری یکی دیگه") == "variation"
    assert canonical_explicit_image_action("یه مدل دیگه از همون") == "variation"
    assert canonical_explicit_image_action("همون عکس رو دوباره بفرست") == "resend_exact"
    assert canonical_explicit_image_action("عکس قبلی رو دوباره بفرست") == "resend_exact"
    assert canonical_explicit_image_action("همونو بفرست") == "resend_exact"


def test_canonical_explicit_image_action_discussion_and_negation_stay_chat():
    from app.services.semantic_image_intent_router import canonical_explicit_image_action
    negatives = [
        "عکس قبلی چرا مصنوعی بود؟",
        "درباره عکس توضیح بده",
        "عکس گرفتن اینجا ممنوعه؟",
        "عکس نمی‌خوام",
        "لازم نیست عکس بدی",
        "فقط درباره عکس حرف می‌زنم",
    ]
    for text in negatives:
        assert canonical_explicit_image_action(text) is None, text


def test_canonical_explicit_image_action_allows_descriptive_modifiers_and_blocks_chat():
    from app.services.semantic_image_intent_router import canonical_explicit_image_action
    positives = [
        "یه عکس کاملاً برهنه و بدون هیچ لباسی، تمام‌قد بفرست",
        "یه عکس با لباس زیر توی اتاق خواب بده",
        "یه عکس بزرگسالانه و تمام‌قد از خودت بفرست",
        "عکس شیطون‌تر با نور کم بفرست",
        "یه عکس معمولی کنار پنجره بفرست",
    ]
    for text in positives:
        assert canonical_explicit_image_action(text) == "generate_new"
    negatives = [
        "درباره عکس برهنه توضیح بده",
        "چرا عکس برهنه ممنوعه؟",
        "عکس نفرست",
        "عکس لخت نمیخوام",
        "این عکس چرا مصنوعی بود؟",
    ]
    for text in negatives:
        assert canonical_explicit_image_action(text) is None


def test_clarification_resolution_preserves_original_source_message():
    db, user, clarification = _clarification_db()
    original = "یه عکس کاملاً برهنه و بدون هیچ لباسی، تمام‌قد، بدون نمای نزدیک اندام تناسلی بفرست"
    db.add(Message(user_id=user.id, role="user", content=original, telegram_message_id=41, input_type="text"))
    db.commit()
    resolution = resolve_pending_image_clarification(db, user_id=user.id, text="عکس جدید")
    assert resolution.action == "generate_new"
    assert resolution.effective_request_text == original
    assert resolution.effective_source_telegram_message_id == 41
    assert resolution.source_user_message.content == original


def test_pending_new_clarification_builds_structured_resolved_request_once():
    db, user, clarification = _clarification_db()
    db.add(Message(user_id=user.id, role='user', content='بفرس', telegram_message_id=41, input_type='text'))
    db.commit()
    resolution = resolve_pending_image_clarification(db, user_id=user.id, text='جدید')
    assert resolution is not None
    assert resolution.action == 'generate_new'
    assert resolution.resolved_request.action == 'generate_new'
    assert resolution.resolved_request.original_request_text == 'بفرس'
    assert resolution.resolved_request.clarification_answer_text == 'جدید'
    mark_image_clarification_resolved(resolution, telegram_message_id=42)
    db.commit()
    assert resolve_pending_image_clarification(db, user_id=user.id, text='جدید') is None
    assert canonical_standalone_image_action('جدید') is None
