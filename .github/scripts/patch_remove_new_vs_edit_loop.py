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
    '    generate_markers = {"تازه", "جدید"}\n',
    '    generate_markers = {"تازه", "جدید", "دوباره", "بگیر", "بده", "بفرست", "بساز"}\n',
    "clarification generate markers",
)
router = replace_once(
    router,
    '        "ادیت", "عوض", "کن", "بکن", "میخوام", "می", "خوام"\n',
    '        "ادیت", "عوض", "کن", "بکن", "میخوام", "می", "خوام", "ببینم",\n        "ببینمت", "نشونم", "نشانم", "لطفاً"\n',
    "clarification fillers",
)

anchor = '''    if word_set & refine_markers and not (word_set & generate_markers) and not substantive:\n        return "refine_previous"\n    return None\n\n\n'''
addition = '''    if word_set & refine_markers and not (word_set & generate_markers) and not substantive:\n        return "refine_previous"\n    return None\n\n\ndef default_pending_clarification_action(text: str) -> str | None:\n    """Resolve one old new-vs-edit question without ever asking it a second time."""\n    normalized = _collapse_stretched_clarification_runs(normalize_image_clarification_text(text))\n    words = [word for word in normalized.split() if word]\n    if not words or len(words) > 12:\n        return None\n    word_set = set(words)\n    chat_markers = {"نه", "نمیخوام", "نمی", "بیخیال", "ولش", "وا", "چرا", "چی", "سوال", "حرف"}\n    refine_markers = {"قبلی", "همون", "همونو", "تغییر", "ویرایش", "ادیت", "عوض"}\n    generate_markers = {"تازه", "جدید", "دوباره", "بگیر", "بده", "بفرست", "بساز", "عکس", "ببینم", "ببینمت"}\n    if word_set & chat_markers:\n        return "chat"\n    if word_set & refine_markers:\n        return "refine_previous"\n    if word_set & generate_markers:\n        return "generate_new"\n    # The question already offered only new/edit. Any other short affirmative reply\n    # defaults to a new photo instead of reopening the same clarification loop.\n    return "generate_new" if len(words) <= 4 else None\n\n\n'''
router = replace_once(router, anchor, addition, "pending clarification default helper")

old_resolve = '''def resolve_pending_image_clarification(\n    db: Session, *, user_id: int, text: str, now: datetime | None = None\n) -> PendingImageClarificationResolution | None:\n    """Resolve only an active, recent clarification and leave unclear replies untouched."""\n    action = _clarification_action_from_reply(text)\n    if action is None:\n        return None\n    now = now or datetime.utcnow()\n    candidates = db.scalars(\n'''
new_resolve = '''def resolve_pending_image_clarification(\n    db: Session, *, user_id: int, text: str, now: datetime | None = None\n) -> PendingImageClarificationResolution | None:\n    """Resolve the newest clarification once; never reopen a new-vs-edit loop."""\n    action = _clarification_action_from_reply(text)\n    now = now or datetime.utcnow()\n    candidates = db.scalars(\n'''
router = replace_once(router, old_resolve, new_resolve, "pending resolver header")
router = replace_once(
    router,
    '''        if not message.created_at or now - message.created_at > IMAGE_CLARIFICATION_TTL:\n            return None\n        source_tid = metadata.get("source_user_telegram_message_id")\n''',
    '''        if not message.created_at or now - message.created_at > IMAGE_CLARIFICATION_TTL:\n            return None\n        if action is None:\n            action = default_pending_clarification_action(text)\n        if action is None:\n            return None\n        source_tid = metadata.get("source_user_telegram_message_id")\n''',
    "pending resolver fallback",
)

insert_after = '''def enforce_clear_image_request_action(\n    deterministic_action: str | None,\n    decision: SemanticImageDecision,\n) -> SemanticImageDecision:\n    """Preserve extracted visuals while locking an unambiguous new-photo delivery command."""\n    if deterministic_action != SemanticImageAction.GENERATE_NEW:\n        return decision\n    if decision.action in {SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}:\n        return decision\n    if (\n        decision.action != SemanticImageAction.GENERATE_NEW\n        or decision.needs_clarification\n        or not decision.media_delivery_requested\n    ):\n        logger.info(\n            "IMAGE_CLEAR_REQUEST_ACTION_LOCKED model_action=%s model_reason=%s",\n            decision.action,\n            decision.reason_code,\n        )\n    decision.action = SemanticImageAction.GENERATE_NEW\n    decision.media_delivery_requested = True\n    decision.needs_clarification = False\n    decision.reason_code = "clear_image_delivery_action_locked"\n    return decision\n\n\n'''
new_block = insert_after + '''def enforce_new_photo_default(\n    current_text: str,\n    deterministic_action: str | None,\n    decision: SemanticImageDecision,\n) -> SemanticImageDecision:\n    """Default an image request to a new photo unless editing the previous image is explicit."""\n    if decision.action != SemanticImageAction.CLARIFY:\n        return decision\n    if deterministic_action in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}:\n        return decision\n    normalized = _norm_intent_text(current_text)\n    previous_markers = ("قبلی", "همون عکس", "همونو", "همین عکس", "این عکس")\n    edit_markers = ("تغییر", "ویرایش", "ادیت", "عوض", "درست کن", "بهتر کن")\n    explicitly_editing_previous = any(marker in normalized for marker in previous_markers) and any(marker in normalized for marker in edit_markers)\n    if explicitly_editing_previous:\n        return decision\n    image_surface = any(marker in normalized for marker in ("عکس", "تصویر", "ببینمت", "نشونم بده", "نشانم بده", "بگیر تازه", "تازه ببینم"))\n    if deterministic_action == SemanticImageAction.GENERATE_NEW or image_surface:\n        logger.info("IMAGE_CLARIFICATION_DEFAULTED_TO_NEW model_reason=%s", decision.reason_code)\n        decision.action = SemanticImageAction.GENERATE_NEW\n        decision.media_delivery_requested = True\n        decision.needs_clarification = False\n        decision.source_reference = None\n        decision.reason_code = "image_request_defaults_to_new_photo"\n    return decision\n\n\n'''
router = replace_once(router, insert_after, new_block, "new photo default guard")
router_path.write_text(router)

telegram_path = Path("app/api/telegram.py")
telegram = telegram_path.read_text()
telegram = replace_once(
    telegram,
    '''    enforce_clear_image_request_action, enforce_clarification_scope,\n''',
    '''    enforce_clear_image_request_action, enforce_clarification_scope, enforce_new_photo_default,\n''',
    "telegram import new-photo guard",
)
telegram = replace_once(
    telegram,
    '''        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)\n        semantic_decision = await resolve_active_image_job_followup_semantically(context, semantic_decision)\n''',
    '''        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)\n        semantic_decision = enforce_new_photo_default(text, deterministic_action, semantic_decision)\n        semantic_decision = await resolve_active_image_job_followup_semantically(context, semantic_decision)\n''',
    "telegram enforce new-photo default",
)
telegram = replace_once(
    telegram,
    '''        if semantic_decision.action == SemanticImageAction.CLARIFY:\n          clarification = "این رو یه عکس تازه بگیرم یا همون عکس قبلی رو تغییر بدم؟"\n''',
    '''        if semantic_decision.action == SemanticImageAction.CLARIFY:\n          clarification = {\n            "image_source_ambiguous": "منظورت کدوم عکس قبلیه؟ روی همون عکس ریپلای کن.",\n            "image_composition_conflict": "توی عکس فقط خودم باشم یا کسی دیگه هم کنارم باشه؟",\n            "image_safety_detail_ambiguous": "یه کم دقیق‌تر بگو توی عکس چه چیزی دیده بشه؟",\n          }.get(str(semantic_decision.reason_code), "یه کم دقیق‌تر بگو توی عکس دقیقاً چی دیده بشه؟")\n''',
    "remove new-vs-edit clarification copy",
)
telegram = replace_once(
    telegram,
    '''        "image_action_ambiguous": "این رو به‌صورت عکس جدید بسازم یا عکس قبلی رو تغییر بدم؟",\n''',
    '''        "image_action_ambiguous": "جزئیات خود عکس رو یک‌بار کامل بگو تا از نو برات بگیرم.",\n''',
    "denial action ambiguity copy",
)
telegram = replace_once(
    telegram,
    '''        "image_parser_uncertain": "درخواست عکست رو گرفتم، ولی یک تصمیم لازم برام روشن نبود. دقیق‌تر بگو عکس جدید می‌خوای یا تغییر عکس قبلی؟",\n''',
    '''        "image_parser_uncertain": "جزئیات عکس رو یک‌بار کامل بگو تا از نو برات بگیرم.",\n''',
    "denial parser ambiguity copy",
)
telegram_path.write_text(telegram)


test_path = Path("tests/test_remove_new_vs_edit_clarification.py")
test_path.write_text('''from datetime import datetime\nfrom types import SimpleNamespace\n\n\ndef test_natural_fresh_reply_resolves_to_generate_new():\n    from app.services.semantic_image_intent_router import _clarification_action_from_reply\n    assert _clarification_action_from_reply("بگیر تازه ببینم") == "generate_new"\n    assert _clarification_action_from_reply("جدید بده بابا کشتی") == "generate_new"\n    assert _clarification_action_from_reply("تازه تازه") == "generate_new"\n\n\ndef test_pending_unknown_short_affirmative_defaults_to_new():\n    from app.services.semantic_image_intent_router import default_pending_clarification_action\n    assert default_pending_clarification_action("باشه بگیر") == "generate_new"\n    assert default_pending_clarification_action("همون قبلی رو تغییر بده") == "refine_previous"\n    assert default_pending_clarification_action("نه بیخیال") == "chat"\n\n\ndef test_clear_image_complaint_never_reopens_new_vs_edit_question():\n    from app.services.semantic_image_intent_router import (\n        SemanticImageAction, SemanticImageDecision, enforce_new_photo_default,\n    )\n    decision = SemanticImageDecision(\n        action=SemanticImageAction.CLARIFY,\n        media_delivery_requested=False,\n        confidence=.7,\n        reason_code="image_action_ambiguous",\n        needs_clarification=True,\n    )\n    resolved = enforce_new_photo_default("عکس ندادی اعصابم گریه", None, decision)\n    assert resolved.action == SemanticImageAction.GENERATE_NEW\n    assert resolved.media_delivery_requested is True\n    assert resolved.needs_clarification is False\n\n\ndef test_explicit_previous_edit_is_not_forced_to_new():\n    from app.services.semantic_image_intent_router import (\n        SemanticImageAction, SemanticImageDecision, enforce_new_photo_default,\n    )\n    decision = SemanticImageDecision(\n        action=SemanticImageAction.CLARIFY,\n        media_delivery_requested=False,\n        confidence=.7,\n        reason_code="image_source_ambiguous",\n        needs_clarification=True,\n    )\n    resolved = enforce_new_photo_default("همون عکس قبلی رو تغییر بده", None, decision)\n    assert resolved.action == SemanticImageAction.CLARIFY\n\n\ndef test_pending_resolution_uses_original_request_and_resolves_once():\n    from app.services.semantic_image_intent_router import resolve_pending_image_clarification\n    source = SimpleNamespace(id=10, content="عکس ندادی اعصابم گریه", telegram_message_id=100)\n    clarification = SimpleNamespace(\n        id=11,\n        created_at=datetime.utcnow(),\n        metadata_json={\n            "kind": "pending_image_clarification",\n            "status": "pending",\n            "source_user_telegram_message_id": 100,\n        },\n    )\n    class Rows:\n        def all(self):\n            return [clarification]\n    class DB:\n        def scalars(self, statement):\n            return Rows()\n        def scalar(self, statement):\n            return source\n    result = resolve_pending_image_clarification(DB(), user_id=1, text="بگیر تازه ببینم")\n    assert result.action == "generate_new"\n    assert result.effective_request_text == source.content\n''')

print("patch_remove_new_vs_edit_loop: ok")
