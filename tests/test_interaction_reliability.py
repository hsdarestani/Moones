from types import SimpleNamespace

from app.services.image_prompt_engine import (
    decide_image_route, validate_prompt_invariants,
)
from app.services.interaction_reliability import (
    aggregate_voice_feedback, block_unbacked_image_promise, interpret_sticker,
    resolve_response_style,
)


def test_colloquial_persian_image_requests_and_discussions():
    explicit = ["یه عکس بده", "یه عکس دیگه بده", "یه عکس دیگه بده ببینمت",
                "عکس از خودت بفرست", "بفرس عکس از خودت ببینم", "بفرست عکستو",
                "عکست رو بده", "دوباره عکس بده", "قدی بفرست", "یه عکس قدی بفرست",
                "یه عکس بده قدی باشه", "تمام قد بفرست", "فول بادی بده"]
    for text in explicit:
        assert decide_image_route(text, recent_image_job_id=7, recent_image_context_found=True).route != "chat", text
    followups = ["یه دونه دیگه بفرست", "این بار قدی باشه", "مثل قبلی ولی قدی"]
    for text in followups:
        assert decide_image_route(text, recent_image_job_id=7, recent_image_context_found=True).route in {"image_followup", "image_refinement"}
    for text in ["همونو دوباره بفرست", "عکس قبلی رو بفرست"]:
        assert decide_image_route(text, recent_image_job_id=7, recent_image_context_found=True).route == "image_resend"
    for text in ["دیروز یه عکس دیدم", "درباره عکس توضیح بده", "چرا عکس‌ها اینطوری میشن؟", "یکی دیگه بگو", "دوباره توضیح بده"]:
        assert decide_image_route(text, recent_image_context_found=True).route == "chat", text


def test_full_body_negative_closeup_is_not_a_contradiction():
    plan = SimpleNamespace(composition_plan=SimpleNamespace(orientation="portrait", width=1024, height=1280,
                           required_environment_objects=[]), visual_scene_state=SimpleNamespace(support_surface=None),
                           intent=SimpleNamespace(adult_intent="none", nudity_level="none", body_emphasis=[]))
    assert "full_body_closeup_contradiction" not in validate_prompt_invariants(plan, "full-body framing; no close-up", "")
    assert "full_body_closeup_contradiction" not in validate_prompt_invariants(plan, "full-body framing; avoid generic portrait and default close-up", "")
    assert "full_body_closeup_contradiction" in validate_prompt_invariants(plan, "full-body framing and close-up portrait", "")


def test_response_style_plans_explicit_control():
    short = resolve_response_style("فقط کوتاه جواب بده و ازم سوال نپرس")
    assert short.requested_length == "very_short" and short.question_budget == "zero"
    detailed = resolve_response_style("کامل و با جزئیات توضیح بده")
    assert detailed.requested_length == "very_detailed" and detailed.answer_mode == "explanation"
    steps = resolve_response_style("قدم به قدم بگو")
    assert steps.answer_mode == "step_by_step" and steps.formatting == "numbered_steps"
    assert resolve_response_style("فقط گوش کن").answer_mode == "listening"


def test_image_promise_requires_successful_action():
    fixed, blocked = block_unbacked_image_promise("باشه، الان عکس می‌فرستم")
    assert blocked and "الان عکس می‌فرستم" not in fixed
    original, blocked = block_unbacked_image_promise("باشه، الان عکس می‌فرستم", image_action_succeeded=True)
    assert not blocked and original == "باشه، الان عکس می‌فرستم"


def test_sticker_interpretation_is_bounded_and_prevents_loops():
    laugh = interpret_sticker(emoji="😂")
    assert laugh.emotion_category == "amusement" and laugh.confidence >= .8
    sad = interpret_sticker(emoji="😭", preceding_text="خیلی ناراحتم")
    assert sad.emotion_category == "sadness"
    heart = interpret_sticker(emoji="❤️")
    assert heart.intent_category == "affection"
    unknown = interpret_sticker(emoji=None, set_name="unknown")
    assert unknown.confidence < .5 and not unknown.sticker_response_appropriate
    loop = interpret_sticker(emoji="😂", replying_to_sticker=True)
    assert not loop.sticker_response_appropriate


def test_voice_feedback_aggregation_is_neutral_bounded_and_user_scoped_by_caller():
    one = aggregate_voice_feedback([{"source_message_id": 1, "confidence": 1, "dimensions": {"pace": 1}}])
    assert 0 < one["pace"] < .5
    repeated = aggregate_voice_feedback([{"source_message_id": i, "confidence": 1, "dimensions": {"pace": 1}} for i in range(8)])
    assert one["pace"] < repeated["pace"] <= .75
    contradictory = aggregate_voice_feedback([{"source_message_id": i, "confidence": 1, "dimensions": {"pace": 1 if i % 2 else -1}} for i in range(10)])
    assert abs(contradictory["pace"]) < .2
    duplicate = aggregate_voice_feedback([{"source_message_id": 1, "confidence": 1, "dimensions": {"pace": 1}}] * 20)
    assert duplicate["pace"] == one["pace"]
