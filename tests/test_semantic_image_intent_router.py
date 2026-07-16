import asyncio
import json
from pathlib import Path


from app.evaluation.semantic_image_intent_eval import evaluate_predictions, load_dataset
from app.services.semantic_image_intent_router import (
    SemanticImageDecision,
    SemanticImageIntentRouter,
    SemanticImageRouterContext,
    VisualIntent,
    semantic_shadow_log_event,
    validate_source_reference_deterministically,
)

DATASET = Path("app/evaluation/image_semantic_intent_dataset.json")


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
