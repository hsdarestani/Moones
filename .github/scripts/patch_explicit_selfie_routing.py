from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()

router = replace_once(
    router,
    'SEMANTIC_ROUTER_SCHEMA_VERSION = "semantic-image-intent-v2-partner-photo"',
    'SEMANTIC_ROUTER_SCHEMA_VERSION = "semantic-image-intent-v3-explicit-media-lock"',
    "router schema version",
)
router = replace_once(
    router,
    '    wants_visual = "عکس" in t or "تصویر" in t or "ببینمت" in t or "نشونم بده" in t\n',
    '    wants_visual = "عکس" in t or "تصویر" in t or "سلفی" in t or "ببینمت" in t or "نشونم بده" in t\n',
    "selfie explicit action surface",
)
router = replace_once(
    router,
    '    image_surface = any(marker in normalized for marker in ("عکس", "تصویر", "ببینمت", "نشونم بده", "نشانم بده", "بگیر تازه", "تازه ببینم"))\n',
    '    image_surface = any(marker in normalized for marker in ("عکس", "تصویر", "سلفی", "ببینمت", "نشونم بده", "نشانم بده", "بگیر تازه", "تازه ببینم"))\n',
    "selfie new-photo default surface",
)
router = replace_once(
    router,
    '        marker in normalized for marker in ("عکس", "تصویر", "ببینمت", "نشونم بده", "نشانم بده")\n',
    '        marker in normalized for marker in ("عکس", "تصویر", "سلفی", "ببینمت", "نشونم بده", "نشانم بده")\n',
    "selfie clarification scope surface",
)

anchor = '''def enforce_clear_image_request_action(
    deterministic_action: str | None,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
'''
recovery = '''async def recover_forced_generate_new_visual_intent(
    context: SemanticImageRouterContext,
    deterministic_action: str | None,
    decision: SemanticImageDecision,
    *,
    model=None,
) -> SemanticImageDecision:
    """Recover structured visual intent when an explicit media command was misclassified as chat.

    The delivery action is already deterministic at this point. This second semantic pass is
    extraction-only and never gets to refuse the image because current-world context conflicts
    with the requested scene.
    """
    if deterministic_action != SemanticImageAction.GENERATE_NEW:
        return decision
    if decision.action == SemanticImageAction.GENERATE_NEW and decision.media_delivery_requested:
        return decision

    semantic_model = model or VeniceSemanticImageIntentModel()
    payload = context.redacted_payload(include_legacy=False)
    system = (
        "The user has already made an explicit request to receive a newly generated image. "
        "The action is fixed to generate_new and media_delivery_requested must be true. "
        "Extract the complete structured visual_intent from the current Persian message and recent conversation. "
        "Do not answer as chat, do not refuse, and do not choose clarify merely because the requested scene differs from the partner's previously stated location. "
        "An explicit scene or pose in the current user message overrides prior scene context. "
        "A phrase meaning from the same place you are now uses the most recent assistant current-location/activity statement and sets current_scene_from_chat=true with scene_context_summary. "
        "For an explicit selfie request set camera_mode=casual_selfie unless mirror, tripod, timer, or another camera method is explicitly requested; set camera_explicit_current_request=true. "
        "Return only JSON matching the supplied schema, with action=generate_new, media_delivery_requested=true, needs_clarification=false, and source_reference=null."
    )
    try:
        result = await semantic_model.client.complete_result(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"schema": SEMANTIC_IMAGE_DECISION_JSON_SCHEMA, "context": payload}, ensure_ascii=False, sort_keys=True)},
            ],
            model=semantic_model.model,
            parameters={"temperature": 0.0, "top_p": 0.1, "max_tokens": 700, "response_format": {"type": "json_object"}},
            timeout=min(float(getattr(semantic_model, "timeout_seconds", 4.0)), 4.0),
        )
        data = json.loads(result.text or "{}")
        recovered = SemanticImageDecision(**data)
        recovered.action = SemanticImageAction.GENERATE_NEW
        recovered.media_delivery_requested = True
        recovered.needs_clarification = False
        recovered.source_reference = None
        recovered.reason_code = "explicit_media_visual_intent_recovered"
        logger.info(
            "IMAGE_EXPLICIT_MEDIA_VISUAL_INTENT_RECOVERED original_action=%s camera_mode=%s scene_explicit=%s current_scene_from_chat=%s",
            decision.action,
            recovered.visual_intent.camera_mode,
            recovered.visual_intent.scene_explicit_current_request,
            recovered.visual_intent.current_scene_from_chat,
        )
        return recovered
    except Exception as exc:
        logger.warning(
            "IMAGE_EXPLICIT_MEDIA_VISUAL_INTENT_RECOVERY_FAILED original_action=%s error_type=%s",
            decision.action,
            type(exc).__name__,
        )
        return decision


'''
router = replace_once(router, anchor, recovery + anchor, "forced visual extraction helper")

router = replace_once(
    router,
    '            "Never choose clarify for a straightforward photo request: ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, mirror selfie, timer/tripod, driving, cafe, bedroom, bathroom, nature, city, or car. Choose generate_new and produce the most complete structured visual intent. For a generic request to see the partner now, default to a believable casual handheld selfie; use mirror_selfie for full-body unless the user explicitly requests timer/tripod or another camera method. "\n',
    '            "Never choose chat or clarify for a direct imperative request to send, take, show, or provide a selfie, photo, or image. Scene inconsistency is not a reason to refuse media delivery: the action remains generate_new, explicit scene/pose instructions in the current message override older context, and phrases meaning from the same place you are now use the latest assistant location/activity. "\n            "Never choose clarify for a straightforward photo request: ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, mirror selfie, timer/tripod, driving, cafe, bedroom, bathroom, nature, city, or car. Choose generate_new and produce the most complete structured visual intent. For a generic request to see the partner now, default to a believable casual handheld selfie; use mirror_selfie for full-body unless the user explicitly requests timer/tripod or another camera method. "\n',
    "semantic prompt explicit media invariant",
)
router_path.write_text(router)


telegram_path = Path("app/api/telegram.py")
telegram = telegram_path.read_text()
telegram = replace_once(
    telegram,
    '    enforce_clear_image_request_action, enforce_clarification_scope, enforce_new_photo_default,\n',
    '    enforce_clear_image_request_action, enforce_clarification_scope, enforce_new_photo_default,\n    recover_forced_generate_new_visual_intent,\n',
    "telegram recovery import",
)
telegram = replace_once(
    telegram,
    '''        if deterministic_action and not deterministic_generate_requires_extraction:
          semantic_decision = SemanticImageDecision(action=deterministic_action, media_delivery_requested=deterministic_action not in {SemanticImageAction.CHAT, SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}, confidence=1.0, reason_code='resolved_structured_image_intent')
        else:
          semantic_decision = await SemanticImageIntentRouter(VeniceSemanticImageIntentModel()).decide(context, shadow_or_evaluation=False)
        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)
''',
    '''        semantic_model = VeniceSemanticImageIntentModel()
        if deterministic_action and not deterministic_generate_requires_extraction:
          semantic_decision = SemanticImageDecision(action=deterministic_action, media_delivery_requested=deterministic_action not in {SemanticImageAction.CHAT, SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}, confidence=1.0, reason_code='resolved_structured_image_intent')
        else:
          semantic_decision = await SemanticImageIntentRouter(semantic_model).decide(context, shadow_or_evaluation=False)
        semantic_decision = await recover_forced_generate_new_visual_intent(context, deterministic_action, semantic_decision, model=semantic_model)
        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)
''',
    "telegram extraction recovery wiring",
)
telegram_path.write_text(telegram)


test_path = Path("tests/test_explicit_selfie_routing.py")
test_path.write_text('''import asyncio
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
            return SimpleNamespace(text=''' + "'''" + '''{
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
            }''' + "'''" + ''')

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
            return SimpleNamespace(text=''' + "'''" + '''{
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
            }''' + "'''" + ''')

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
''')

print("patch_explicit_selfie_routing: ok")
