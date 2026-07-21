from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"{label} target not found")


router = Path("app/services/semantic_image_intent_router.py")
text = router.read_text(encoding="utf-8")

marker = "_NORMALIZED_CLARIFICATION_ANSWERS = {"
helpers = '''def _clarification_action_from_reply(text: str) -> str | None:
    """Resolve only short, choice-like replies to the newest pending clarification."""
    normalized = normalize_image_clarification_text(text)
    stretched = _collapse_stretched_clarification_runs(normalized)
    normalized_answers = {
        action: {normalize_image_clarification_text(answer) for answer in answers}
        for action, answers in _CLARIFICATION_ANSWERS.items()
    }
    for candidate in (normalized, stretched):
        exact = next((action for action, answers in normalized_answers.items() if candidate in answers), None)
        if exact:
            return exact

    words = [word for word in stretched.split() if word]
    if not words or len(words) > 8:
        return None
    word_set = set(words)
    generate_markers = {"تازه", "جدید"}
    refine_markers = {"قبلی", "همون", "تغییر", "ویرایش", "ادیت", "عوض"}
    chat_markers = {"نمیخوام", "نمی", "سوال", "گفتگو", "حرف"}
    fillers = {
        "تازه", "جدید", "عکس", "یه", "یک", "بده", "بدین", "بگیر", "بگیری",
        "بساز", "برام", "لطفا", "لطفاً", "بابا", "کشتی", "دیگه", "حالا",
        "از", "اول", "همونو", "همون", "قبلی", "رو", "را", "تغییر", "ویرایش",
        "ادیت", "عوض", "کن", "بکن", "میخوام", "می", "خوام"
    }
    substantive = [word for word in words if word not in fillers]
    if word_set & chat_markers:
        return "chat" if len(substantive) <= 2 else None
    if word_set & generate_markers and not (word_set & refine_markers) and not substantive:
        return "generate_new"
    if word_set & refine_markers and not (word_set & generate_markers) and not substantive:
        return "refine_previous"
    return None


def supersede_pending_image_clarification(
    db: Session,
    *,
    user_id: int,
    telegram_message_id: int | None = None,
    reason: str = "new_user_message",
) -> Message | None:
    """Close the newest unresolved clarification when the user moves on or gives a new request."""
    candidates = db.scalars(
        select(Message).where(
            Message.user_id == user_id,
            Message.role == "assistant",
            Message.input_type == "image_clarification",
        ).order_by(Message.created_at.desc(), Message.id.desc()).limit(20)
    ).all()
    for message in candidates:
        metadata = dict(message.metadata_json or {})
        if metadata.get("kind") != "pending_image_clarification":
            continue
        if metadata.get("status") == "pending":
            metadata.update({
                "status": "superseded",
                "superseded_at": datetime.utcnow().isoformat(),
                "superseded_reason": reason,
                "superseded_by_telegram_message_id": telegram_message_id,
            })
            message.metadata_json = metadata
            logger.info(
                "IMAGE_CLARIFICATION_SUPERSEDED user_id=%s clarification_id=%s reason=%s",
                user_id,
                message.id,
                reason,
            )
            return message
        return None
    return None


_NORMALIZED_CLARIFICATION_ANSWERS = {'''
if marker in text and "def _clarification_action_from_reply(" not in text:
    text = text.replace(marker, helpers, 1)

old = '''    normalized = normalize_image_clarification_text(text)
    action = next((key for key, answers in _NORMALIZED_CLARIFICATION_ANSWERS.items() if normalized in answers), None)
    if action is None:
        stretched = _collapse_stretched_clarification_runs(normalized)
        action = next((key for key, answers in _NORMALIZED_CLARIFICATION_ANSWERS.items() if stretched in answers), None)
    if action is None:
        return None'''
new = '''    action = _clarification_action_from_reply(text)
    if action is None:
        return None'''
text = replace_once(text, old, new, "clarification resolver")

insert_marker = "@dataclass\nclass ConversationTurnSummary:"
policy_helpers = '''def enforce_clarification_scope(
    current_text: str,
    pending_resolution: PendingImageClarificationResolution | None,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Never let stale image context turn ordinary conversation into a clarification loop."""
    if decision.action != SemanticImageAction.CLARIFY or pending_resolution is not None:
        return decision
    normalized = _norm_intent_text(current_text)
    explicit_visual_surface = any(
        marker in normalized for marker in ("عکس", "تصویر", "ببینمت", "نشونم بده", "نشانم بده")
    )
    if canonical_explicit_image_action(current_text) is not None or explicit_visual_surface:
        return decision
    logger.info(
        "IMAGE_CLARIFICATION_DOWNGRADED_TO_CHAT reason=no_current_image_request model_reason=%s",
        decision.reason_code,
    )
    return SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=max(float(decision.confidence), 0.8),
        reason_code="clarification_without_current_image_request",
        needs_clarification=False,
        source_reference=None,
        visual_intent=decision.visual_intent,
        safety_relevant_signals=decision.safety_relevant_signals,
    )


def _referenced_object_phrase(text: str) -> str | None:
    normalized = _norm_intent_text(text)
    words = normalized.split()
    if "از" not in words or not ({"اون", "همون", "این", "همین"} & set(words)):
        return None
    start = words.index("از") + 1
    stop_words = {"بده", "بدی", "بفرست", "بفرستی", "بساز", "بگیر", "بگیری", "ببینم", "ببین"}
    end = next((idx for idx in range(start, len(words)) if words[idx] in stop_words), len(words))
    candidate = [
        word for word in words[start:end]
        if word not in {"اون", "همون", "این", "همین", "عکس", "تصویر", "یه", "یک"}
    ]
    if not candidate:
        return None
    self_markers = {"خودت", "خودتو", "صورتت", "چهره", "بدنت", "لباست", "موهات"}
    if set(candidate) & self_markers:
        return None
    return " ".join(candidate)


def enforce_referenced_object_request(
    context: SemanticImageRouterContext,
    deterministic_action: str | None,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Resolve «that object from the previous photo» as a source-bound object detail photo."""
    if deterministic_action != SemanticImageAction.GENERATE_NEW:
        return decision
    phrase = _referenced_object_phrase(context.current_user_message)
    if not phrase:
        return decision
    visual = decision.visual_intent
    visual.request_type = "object_photo"
    visual.primary_subject = "object"
    visual.object_only = True
    visual.partner_visible = False
    visual.pet_only = False
    visual.hands_only = False
    visual.face_visible = False
    visual.face_hidden = True
    visual.camera_mode = "point_of_view"
    visual.framing = "detail"
    visual.visible_objects = list(dict.fromkeys([*(visual.visible_objects or []), phrase]))
    visual.natural_capture_required = True
    latest = context.recent_image_job or context.latest_image_job
    if latest and latest.has_retrievable_artifact and latest.job_id is not None:
        decision.action = SemanticImageAction.REFINE_PREVIOUS
        decision.source_reference = SemanticSourceReference(kind="latest_image", job_id=latest.job_id)
        decision.reason_code = "referenced_object_from_latest_image"
    else:
        decision.action = SemanticImageAction.GENERATE_NEW
        decision.source_reference = None
        decision.reason_code = "referenced_object_new_photo"
    decision.media_delivery_requested = True
    decision.needs_clarification = False
    logger.info(
        "IMAGE_REFERENCED_OBJECT_LOCKED action=%s source_job_id=%s object_phrase=%s",
        decision.action,
        getattr(decision.source_reference, "job_id", None),
        phrase,
    )
    return decision


@dataclass
class ConversationTurnSummary:'''
if insert_marker in text and "def enforce_referenced_object_request(" not in text:
    text = text.replace(insert_marker, policy_helpers, 1)
router.write_text(text, encoding="utf-8")

contract = Path("app/services/partner_photo_contract.py")
text = contract.read_text(encoding="utf-8")
old = '''    request_type = str(_value(visual_intent, "request_type", "new_photo") or "new_photo").strip().lower()
    primary_subject = str(_value(visual_intent, "primary_subject", "partner") or "partner").strip().lower()
    primary_subject = _PRIMARY_SUBJECT_ALIASES.get(primary_subject, primary_subject)

    object_only = bool(_value(visual_intent, "object_only", False))
    pet_only = bool(_value(visual_intent, "pet_only", False))
    hands_only = bool(_value(visual_intent, "hands_only", False))
    pet_visible = bool(_value(visual_intent, "pet_visible", False) or pet_only or primary_subject == "pet")
    partner_visible_value = _bool_or_none(_value(visual_intent, "partner_visible", None))
    partner_visible = True if partner_visible_value is None else partner_visible_value'''
new = '''    request_type = str(_value(visual_intent, "request_type", "new_photo") or "new_photo").strip().lower()
    visible_objects = _unique(_value(visual_intent, "visible_objects", []) or [])
    object_only = bool(_value(visual_intent, "object_only", False))
    pet_only = bool(_value(visual_intent, "pet_only", False))
    hands_only = bool(_value(visual_intent, "hands_only", False))
    raw_primary_subject = _value(visual_intent, "primary_subject", None)
    if raw_primary_subject not in (None, ""):
        normalized_primary = str(raw_primary_subject).strip().lower()
        primary_subject = _PRIMARY_SUBJECT_ALIASES.get(normalized_primary, normalized_primary)
    elif pet_only or request_type in {"pet_photo", "pet"}:
        primary_subject = "pet"
    elif object_only or request_type in {"object_photo", "object", "scene_photo"}:
        primary_subject = "scene" if request_type == "scene_photo" else "object"
    else:
        primary_subject = "partner"
    if primary_subject in {"object", "scene"} and not hands_only:
        object_only = True
    pet_visible = bool(_value(visual_intent, "pet_visible", False) or pet_only or primary_subject == "pet")
    partner_visible_value = _bool_or_none(_value(visual_intent, "partner_visible", None))
    partner_visible = True if partner_visible_value is None else partner_visible_value'''
text = replace_once(text, old, new, "partner contract subject resolution")
text = replace_once(
    text,
    '        visible_objects=_unique(_value(visual_intent, "visible_objects", []) or []),',
    "        visible_objects=visible_objects,",
    "partner contract visible objects",
)
contract.write_text(text, encoding="utf-8")

telegram = Path("app/api/telegram.py")
text = telegram.read_text(encoding="utf-8")
old = '''    mark_image_clarification_resolved, resolve_pending_image_clarification,
    enforce_clear_image_request_action, validate_source_reference_deterministically)'''
new = '''    mark_image_clarification_resolved, resolve_pending_image_clarification,
    enforce_clear_image_request_action, enforce_clarification_scope,
    enforce_referenced_object_request, supersede_pending_image_clarification,
    validate_source_reference_deterministically)'''
text = replace_once(text, old, new, "telegram semantic imports")

old = '''        pending_resolution = resolve_pending_image_clarification(db, user_id=user.id, text=text) if semantic_flags.execution_enabled else None
        deterministic_action = pending_resolution.action if pending_resolution else canonical_explicit_image_action(text)'''
new = '''        pending_resolution = resolve_pending_image_clarification(db, user_id=user.id, text=text) if semantic_flags.execution_enabled else None
        if semantic_flags.execution_enabled and pending_resolution is None:
          supersede_pending_image_clarification(db, user_id=user.id, telegram_message_id=msg.message_id)
        deterministic_action = pending_resolution.action if pending_resolution else canonical_explicit_image_action(text)'''
text = replace_once(text, old, new, "telegram stale clarification supersede")

old = '''        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)
        logger.info("IMAGE_ROUTE_LLM_DECISION user_id=%s action=%s reason_code=%s source_job_id=%s", user.id, semantic_decision.action, semantic_decision.reason_code, getattr(getattr(semantic_decision, 'source_reference', None), 'job_id', None))'''
new = '''        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)
        semantic_decision = enforce_referenced_object_request(context, deterministic_action, semantic_decision)
        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)
        logger.info("IMAGE_ROUTE_LLM_DECISION user_id=%s action=%s reason_code=%s source_job_id=%s", user.id, semantic_decision.action, semantic_decision.reason_code, getattr(getattr(semantic_decision, 'source_reference', None), 'job_id', None))'''
text = replace_once(text, old, new, "telegram semantic postprocessing")
telegram.write_text(text, encoding="utf-8")

tests = Path("tests/test_semantic_image_intent_router.py")
text = tests.read_text(encoding="utf-8")
old = '''    SemanticImageRouterContext,
    VisualIntent,'''
new = '''    SemanticImageRouterContext,
    RecentImageJobSummary,
    VisualIntent,'''
text = replace_once(text, old, new, "semantic test recent job import")
old = '''    enforce_clear_image_request_action,
    mark_image_clarification_resolved,'''
new = '''    enforce_clear_image_request_action,
    enforce_clarification_scope,
    enforce_referenced_object_request,
    mark_image_clarification_resolved,
    supersede_pending_image_clarification,'''
text = replace_once(text, old, new, "semantic test imports")
anchor = "def test_resolved_and_expired_clarifications_cannot_be_reused():"
addition = '''def test_natural_choice_replies_resolve_once_without_loop():
    for reply in ("تازه تازه", "جدید بده بابا کشتی", "تاااازه"):
        db, user, _ = _clarification_db()
        db.add(Message(user_id=user.id, role="user", content="از اون ست قوری عکس بده", telegram_message_id=41, input_type="text"))
        db.commit()
        resolution = resolve_pending_image_clarification(db, user_id=user.id, text=reply)
        assert resolution is not None
        assert resolution.action == "generate_new"
        db.close()


def test_unrelated_message_supersedes_pending_clarification():
    db, user, clarification = _clarification_db()
    assert resolve_pending_image_clarification(db, user_id=user.id, text="وا مگه نگفتی کافه ای برگشتی خونه؟") is None
    supersede_pending_image_clarification(db, user_id=user.id, telegram_message_id=99)
    db.commit()
    assert clarification.metadata_json["status"] == "superseded"
    assert clarification.metadata_json["superseded_by_telegram_message_id"] == 99


def test_clarify_without_current_image_request_becomes_chat():
    decision = SemanticImageDecision(
        action="clarify",
        media_delivery_requested=False,
        confidence=.61,
        reason_code="stale_image_context",
        needs_clarification=True,
    )
    fixed = enforce_clarification_scope("وا مگه نگفتی کافه ای برگشتی خونه؟", None, decision)
    assert fixed.action == "chat"
    assert fixed.needs_clarification is False


def test_referenced_object_request_uses_latest_image_and_hides_partner():
    context = SemanticImageRouterContext(
        current_user_message="یه عکس از اون ست قوری و فنجون رو میز بده ببینم",
        recent_image_job=RecentImageJobSummary(job_id=77, status="sent", has_retrievable_artifact=True),
        recent_retrievable_image_exists=True,
    )
    decision = SemanticImageDecision(
        action="generate_new",
        media_delivery_requested=True,
        confidence=.95,
        reason_code="clear_photo",
        visual_intent=VisualIntent(),
    )
    fixed = enforce_referenced_object_request(context, "generate_new", decision)
    assert fixed.action == "refine_previous"
    assert fixed.source_reference.job_id == 77
    assert fixed.visual_intent.primary_subject == "object"
    assert fixed.visual_intent.object_only is True
    assert fixed.visual_intent.partner_visible is False
    assert fixed.visual_intent.framing == "detail"
    assert fixed.visual_intent.camera_mode == "point_of_view"
    assert any("قوری" in item for item in fixed.visual_intent.visible_objects)



def test_resolved_and_expired_clarifications_cannot_be_reused():'''
if anchor in text and "test_natural_choice_replies_resolve_once_without_loop" not in text:
    text = text.replace(anchor, addition, 1)
tests.write_text(text, encoding="utf-8")

partner_tests = Path("tests/test_partner_photo_engine.py")
text = partner_tests.read_text(encoding="utf-8")
anchor = "def test_object_only_prompt_has_no_generic_portrait():"
addition = '''def test_object_photo_request_type_defaults_to_zero_human_object_contract():
    contract = build_partner_photo_contract(
        VisualIntent(request_type="object_photo", visible_objects=["tea set"], partner_visible=None)
    )
    assert contract["primary_subject"] == "object"
    assert contract["object_only"] is True
    assert contract["partner_visible"] is False
    assert contract["expected_human_subject_count"] == 0
    assert contract["framing"] == "detail"
    assert contract["camera_mode"] == "point_of_view"



def test_object_only_prompt_has_no_generic_portrait():'''
if anchor in text and "test_object_photo_request_type_defaults_to_zero_human_object_contract" not in text:
    text = text.replace(anchor, addition, 1)
partner_tests.write_text(text, encoding="utf-8")
