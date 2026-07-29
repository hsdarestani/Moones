import asyncio
from types import SimpleNamespace


def test_explicit_selfie_commands_are_new_image_actions():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction,
        canonical_explicit_image_action,
    )

    assert canonical_explicit_image_action(
        "روی مبل دراز کشیدی، از همین الان یه سلفی طبیعی بده"
    ) == SemanticImageAction.GENERATE_NEW
    assert canonical_explicit_image_action(
        "خب از همون کافه که هستی یه سلفی بده"
    ) == SemanticImageAction.GENERATE_NEW


def test_chat_misclassification_is_recovered_with_explicit_sofa_scene():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        VisualIntent,
        recover_forced_generate_new_visual_intent,
    )

    class Client:
        async def complete_result(self, messages, **kwargs):
            assert "action is fixed to generate_new" in messages[0]["content"]
            return SimpleNamespace(text='''{
              "action":"generate_new",
              "media_delivery_requested":true,
              "confidence":0.99,
              "reason_code":"explicit_selfie",
              "needs_clarification":false,
              "source_reference":null,
              "visual_intent":{
                "camera_mode":"casual_selfie",
                "camera_explicit_current_request":true,
                "scene":"lying on a sofa at home",
                "scene_explicit_current_request":true,
                "pose":"lying down",
                "primary_subject":"partner",
                "partner_visible":true
              },
              "safety_relevant_signals":{}
            }''')

    model = SimpleNamespace(client=Client(), model="test", timeout_seconds=1)
    initial = SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=.8,
        reason_code="scene_conflict_chat",
        visual_intent=VisualIntent(),
    )
    context = SemanticImageRouterContext(
        current_user_message="روی مبل دراز کشیدی، از همین الان یه سلفی طبیعی بده"
    )
    result = asyncio.run(
        recover_forced_generate_new_visual_intent(
            context,
            SemanticImageAction.GENERATE_NEW,
            initial,
            model=model,
        )
    )
    assert result.action == SemanticImageAction.GENERATE_NEW
    assert result.media_delivery_requested is True
    assert result.needs_clarification is False
    assert result.visual_intent.camera_mode == "casual_selfie"
    assert result.visual_intent.scene_explicit_current_request is True
    assert "sofa" in (result.visual_intent.scene or "")


def test_same_cafe_followup_recovers_current_scene_from_chat():
    from app.services.semantic_image_intent_router import (
        ConversationTurnSummary,
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        VisualIntent,
        recover_forced_generate_new_visual_intent,
    )

    class Client:
        async def complete_result(self, messages, **kwargs):
            return SimpleNamespace(text='''{
              "action":"generate_new",
              "media_delivery_requested":true,
              "confidence":0.99,
              "reason_code":"current_cafe_selfie",
              "needs_clarification":false,
              "source_reference":null,
              "visual_intent":{
                "camera_mode":"casual_selfie",
                "camera_explicit_current_request":true,
                "location":"cafe",
                "environment_type":"cafe interior",
                "current_scene_from_chat":true,
                "scene_context_summary":"the partner is currently at a cafe",
                "primary_subject":"partner",
                "partner_visible":true
              },
              "safety_relevant_signals":{}
            }''')

    model = SimpleNamespace(client=Client(), model="test", timeout_seconds=1)
    initial = SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=.8,
        reason_code="ordinary_chat",
        visual_intent=VisualIntent(),
    )
    context = SemanticImageRouterContext(
        current_user_message="خب از همون کافه که هستی یه سلفی بده",
        recent_conversation=[
            ConversationTurnSummary(
                role="assistant",
                text_summary="الان تو کافه‌ام و روی مبل نیستم",
            )
        ],
    )
    result = asyncio.run(
        recover_forced_generate_new_visual_intent(
            context,
            SemanticImageAction.GENERATE_NEW,
            initial,
            model=model,
        )
    )
    assert result.action == SemanticImageAction.GENERATE_NEW
    assert result.visual_intent.current_scene_from_chat is True
    assert result.visual_intent.location == "cafe"
    assert "cafe" in (result.visual_intent.scene_context_summary or "")


def test_recovery_failure_still_cannot_fall_through_to_chat():
    from app.services.semantic_image_intent_router import (
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        VisualIntent,
        enforce_clear_image_request_action,
        enforce_partner_photo_defaults,
        recover_forced_generate_new_visual_intent,
    )

    class Client:
        async def complete_result(self, *args, **kwargs):
            raise RuntimeError("classifier unavailable")

    model = SimpleNamespace(client=Client(), model="test", timeout_seconds=1)
    initial = SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=.8,
        reason_code="ordinary_chat",
        visual_intent=VisualIntent(),
    )
    context = SemanticImageRouterContext(current_user_message="یه سلفی بده")
    recovered = asyncio.run(
        recover_forced_generate_new_visual_intent(
            context,
            SemanticImageAction.GENERATE_NEW,
            initial,
            model=model,
        )
    )
    locked = enforce_clear_image_request_action(
        SemanticImageAction.GENERATE_NEW,
        recovered,
    )
    locked = enforce_partner_photo_defaults(context, locked)
    assert locked.action == SemanticImageAction.GENERATE_NEW
    assert locked.media_delivery_requested is True
    assert locked.visual_intent.camera_mode == "casual_selfie"
