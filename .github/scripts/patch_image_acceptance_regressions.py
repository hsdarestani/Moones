from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# 1) Semantic routing: body-visibility commands are image actions; distinguish source context from exact bytes.
router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()
router = replace_once(
    router,
    '''    # Compatibility fallback only. Production still calls the semantic model for
    # GENERATE_NEW so this helper never becomes the source of an empty VisualIntent.
    wants_visual = "عکس" in t or "تصویر" in t or "سلفی" in t or "ببینمت" in t or "نشونم بده" in t
''',
    '''    adult_body_surface = any(term in t for term in ("لخت", "لختی", "برهنه", "بدون لباس", "ممه", "ممه ها", "ممه‌هات", "سینه", "پستان"))
    adult_visibility_delivery = any(term in t for term in ("بده", "بدی", "بفرست", "بفرس", "ببینم", "ببینمت", "نشون", "نشان", "معلوم باش", "پیدا باش", "میخوام", "می خوام"))
    if adult_body_surface and adult_visibility_delivery:
        return SemanticImageAction.GENERATE_NEW
    # Compatibility fallback only. Production still calls the semantic model for
    # GENERATE_NEW so this helper never becomes the source of an empty VisualIntent.
    wants_visual = "عکس" in t or "تصویر" in t or "سلفی" in t or "ببینمت" in t or "نشونم بده" in t
''',
    "adult body visual routing",
)
router = replace_once(
    router,
    '''    recent_retrievable_image_exists: bool = False
    seconds_since_recent_image: int | None = None
''',
    '''    recent_retrievable_image_exists: bool = False
    recent_exact_artifact_exists: bool = False
    seconds_since_recent_image: int | None = None
''',
    "router exact artifact context field",
)
router = replace_once(
    router,
    '''            "recent_retrievable_image_exists": self.recent_retrievable_image_exists,
            "seconds_since_recent_image": self.seconds_since_recent_image,
''',
    '''            "recent_retrievable_image_exists": self.recent_retrievable_image_exists,
            "recent_exact_artifact_exists": self.recent_exact_artifact_exists,
            "seconds_since_recent_image": self.seconds_since_recent_image,
''',
    "router exact artifact payload",
)
old_validator = '''def validate_source_reference_deterministically(decision: SemanticImageDecision, *, recent_retrievable_image_exists: bool, allowed_job_ids: set[int]) -> tuple[bool, str | None]:
    if decision.action not in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}:
        return True, None
    if not recent_retrievable_image_exists:
        return False, "no_recent_retrievable_image"
    ref = decision.source_reference
    if ref and ref.job_id is not None and ref.job_id not in allowed_job_ids:
        return False, "source_job_out_of_scope"
    return True, None
'''
new_validator = '''def validate_source_reference_deterministically(
    decision: SemanticImageDecision,
    *,
    recent_retrievable_image_exists: bool,
    allowed_job_ids: set[int],
    recent_source_image_exists: bool | None = None,
    recent_exact_artifact_exists: bool | None = None,
) -> tuple[bool, str | None]:
    if decision.action not in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}:
        return True, None
    source_exists = recent_retrievable_image_exists if recent_source_image_exists is None else bool(recent_source_image_exists)
    exact_exists = recent_retrievable_image_exists if recent_exact_artifact_exists is None else bool(recent_exact_artifact_exists)
    if decision.action == SemanticImageAction.RESEND_EXACT:
        if not exact_exists:
            return False, "no_recent_retrievable_image"
    elif not source_exists:
        return False, "no_recent_source_image_context"
    ref = decision.source_reference
    if ref and ref.job_id is not None and ref.job_id not in allowed_job_ids:
        return False, "source_job_out_of_scope"
    return True, None
'''
router = replace_once(router, old_validator, new_validator, "source validation split")
router_path.write_text(router)


# 2) Router context: a sent plan can be refined even when exact bytes have been archived/cleared.
context_path = Path("app/services/semantic_image_router_context.py")
context = context_path.read_text()
context = replace_once(
    context,
    '''    recent_summary=None; plan_summary=None; retrievable=False; seconds=None
    if recent:
        retrievable=v2.source_job_is_retrievable(recent, user_id=user_id, chat_id=chat_id)
''',
    '''    recent_summary=None; plan_summary=None; retrievable=False; exact_artifact=False; seconds=None
    if recent:
        retrievable=v2.source_job_is_context_eligible(recent, user_id=user_id, chat_id=chat_id)
        exact_artifact=v2.source_job_is_retrievable(recent, user_id=user_id, chat_id=chat_id)
''',
    "context source availability",
)
context = replace_once(
    context,
    '''    return SemanticImageRouterContext(current_user_message=current_text, recent_conversation=turns, reply_to_message=reply_meta, active_image_job=active_summary, latest_image_job=latest_summary, recent_image_job=recent_summary, recent_resolved_image_plan=plan_summary, recent_retrievable_image_exists=retrievable, seconds_since_recent_image=seconds, legacy_route_decision=legacy_route_decision)
''',
    '''    return SemanticImageRouterContext(current_user_message=current_text, recent_conversation=turns, reply_to_message=reply_meta, active_image_job=active_summary, latest_image_job=latest_summary, recent_image_job=recent_summary, recent_resolved_image_plan=plan_summary, recent_retrievable_image_exists=retrievable, recent_exact_artifact_exists=exact_artifact, seconds_since_recent_image=seconds, legacy_route_decision=legacy_route_decision)
''',
    "context return exact artifact",
)
context_path.write_text(context)


# 3) V2 source retention, adult profile/classification, and full-body camera geometry.
v2_path = Path("app/services/image_pipeline_v2.py")
v2 = v2_path.read_text()
old_sources = '''def source_job_is_retrievable(job: ImageGenerationJob, *, user_id:int, chat_id:int, ttl_minutes:int=30) -> bool:
    if not job or job.user_id != user_id or job.chat_id != chat_id or job.status != 'sent': return False
    if job.sent_at and job.sent_at < datetime.utcnow()-timedelta(minutes=ttl_minutes): return False
    if any(a.image_bytes for a in getattr(job, 'artifacts', []) or []): return True
    return False


def find_eligible_source_image_context(db: Session, *, user_id:int, chat_id:int, ttl_minutes:int=30) -> ImageGenerationJob|None:
    cutoff=datetime.utcnow()-timedelta(minutes=ttl_minutes)
    return db.scalar(select(ImageGenerationJob).outerjoin(ImageGenerationArtifact).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status=='sent', ImageGenerationJob.sent_at>=cutoff, (ImageGenerationArtifact.image_bytes.is_not(None))).order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))
'''
new_sources = '''def source_job_is_context_eligible(job: ImageGenerationJob, *, user_id:int, chat_id:int, ttl_minutes:int=360) -> bool:
    if not job or job.user_id != user_id or job.chat_id != chat_id or job.status != 'sent': return False
    if not job.sent_at or job.sent_at < datetime.utcnow()-timedelta(minutes=ttl_minutes): return False
    return bool(job.resolved_plan_json or (job.metadata_json or {}).get('resolved_plan'))


def source_job_is_retrievable(job: ImageGenerationJob, *, user_id:int, chat_id:int, ttl_minutes:int=360) -> bool:
    if not source_job_is_context_eligible(job, user_id=user_id, chat_id=chat_id, ttl_minutes=ttl_minutes): return False
    return any(a.image_bytes for a in getattr(job, 'artifacts', []) or [])


def find_eligible_source_image_context(db: Session, *, user_id:int, chat_id:int, ttl_minutes:int=360) -> ImageGenerationJob|None:
    cutoff=datetime.utcnow()-timedelta(minutes=ttl_minutes)
    return db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status=='sent', ImageGenerationJob.sent_at>=cutoff).order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))


def find_eligible_source_artifact_context(db: Session, *, user_id:int, chat_id:int, ttl_minutes:int=360) -> ImageGenerationJob|None:
    cutoff=datetime.utcnow()-timedelta(minutes=ttl_minutes)
    return db.scalar(select(ImageGenerationJob).join(ImageGenerationArtifact).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status=='sent', ImageGenerationJob.sent_at>=cutoff, ImageGenerationArtifact.image_bytes.is_not(None)).order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))
'''
v2 = replace_once(v2, old_sources, new_sources, "source context retention")
v2 = replace_once(
    v2,
    '''    if not getattr(profile, 'anatomical_profile', None):
        profile.anatomical_profile=normalize_anatomical_profile(traits.get('anatomical_profile'))
''',
    '''    current_anatomy=normalize_anatomical_profile(getattr(profile, 'anatomical_profile', None) or traits.get('anatomical_profile'))
    if current_anatomy == 'unspecified':
        current_anatomy=normalize_anatomical_profile(getattr(profile, 'gender_presentation', None))
    profile.anatomical_profile=current_anatomy
''',
    "derive anatomical profile",
)
v2 = replace_once(
    v2,
    '''    explicit_nudity=str(intent.content_classification).endswith('full_nudity')
    visual_requirements.anatomical_profile=(ap if explicit_nudity else None)
    visual_requirements.anatomy_source=(anatomical_profile_source(profile) if explicit_nudity else None)
    visual_requirements.explicit_nudity_requested=explicit_nudity
    visual_requirements.anatomy_consistency_required=bool(explicit_nudity and ap != 'unspecified')
    visual_requirements.anatomy_qa_required=visual_requirements.anatomy_consistency_required
''',
    '''    explicit_nudity=intent.content_classification in {ContentClassification.TOPLESS, ContentClassification.FULL_NUDITY}
    visual_requirements.anatomical_profile=(ap if explicit_nudity else None)
    visual_requirements.anatomy_source=(anatomical_profile_source(profile) if explicit_nudity else None)
    visual_requirements.explicit_nudity_requested=explicit_nudity
    visual_requirements.anatomy_consistency_required=bool(explicit_nudity and ap != 'unspecified')
    visual_requirements.anatomy_qa_required=visual_requirements.anatomy_consistency_required
''',
    "topless explicit nudity contract",
)
v2 = replace_once(
    v2,
    '''    if semantic_full_body and vr.partner_visible:
        vr.framing_requirement='full_body'; vr.full_body_visible=True; vr.head_visible=not vr.face_hidden_required; vr.feet_visible=True; vr.body_not_cropped=True; vr.visibility_targets.upper_body_visible=True
''',
    '''    if semantic_full_body and vr.partner_visible:
        if vr.camera_mode == 'casual_selfie' and not bool(contract.get('camera_explicit_current_request')):
            vr.camera_mode='mirror_selfie'; contract['camera_mode']='mirror_selfie'; vr.photo_contract=contract
            vr.reason_codes.append('full_body_mirror_selfie_required')
        vr.framing_requirement='full_body'; vr.full_body_visible=True; vr.head_visible=not vr.face_hidden_required; vr.feet_visible=True; vr.body_not_cropped=True; vr.visibility_targets.upper_body_visible=True
''',
    "full body selfie geometry",
)
v2_path.write_text(v2)


# 4) Deterministic adult intent guard and adult model selection.
guard_path = Path("app/services/image_generation_guardrails.py")
guard = guard_path.read_text()
guard = replace_once(
    guard,
    '''    "chest": "chest",
    "breasts": "chest",
''',
    '''    "chest": "breasts",
    "breasts": "breasts",
''',
    "breast canonical region",
)
insert_anchor = '''def apply_adult_scene_policy(intent, routine_context: dict[str, Any] | None) -> AdultScenePolicyResult:
'''
adult_guard = '''def apply_deterministic_adult_visual_intent(intent, user_text: str):
    """Preserve explicit Persian adult visibility requests when the semantic extractor under-classifies them."""
    from app.services import image_pipeline_v2 as v2

    text = " ".join(str(user_text or "").replace("‌", " ").replace("ي", "ی").replace("ك", "ک").lower().split())
    full_nudity = any(term in text for term in ("لخت", "لختی", "برهنه", "کاملا برهنه", "کاملاً برهنه", "بدون لباس", "لباس نداشته", "لباس نپوش"))
    breast_term = any(term in text for term in ("ممه", "ممه ها", "ممه هات", "سینه", "سینه هات", "پستان"))
    visibility = any(term in text for term in ("عکس", "بده", "بدی", "بفرست", "بفرس", "ببینم", "نشون", "نشان", "معلوم باش", "پیدا باش", "میخوام", "می خوام"))

    if full_nudity and visibility:
        intent.content_classification = v2.ContentClassification.FULL_NUDITY
        intent.adult_intent = "full_nudity"
        for region_name in ("breasts", "buttocks", "full_body"):
            region = intent.body_visibility.regions.setdefault(region_name, v2.BodyRegionIntent())
            region.mentioned = True; region.visibility_requested = True; region.explicit_current_request = True
            if region_name == "full_body": region.framing_requested = True
    elif breast_term and visibility:
        intent.content_classification = v2.ContentClassification.TOPLESS
        intent.adult_intent = "topless"
        region = intent.body_visibility.regions.setdefault("breasts", v2.BodyRegionIntent())
        region.mentioned = True; region.visibility_requested = True; region.framing_requested = True; region.explicit_current_request = True
    return intent


'''
guard = replace_once(guard, insert_anchor, adult_guard + insert_anchor, "deterministic adult guard")
guard = replace_once(
    guard,
    '''    elif nudity_level == "topless":
        intent.content_classification = v2.ContentClassification.TOPLESS
        intent.adult_intent = "topless"
''',
    '''    elif nudity_level == "topless":
        intent.content_classification = v2.ContentClassification.TOPLESS
        intent.adult_intent = "topless"
        region = intent.body_visibility.regions.setdefault("breasts", v2.BodyRegionIntent())
        region.mentioned = True; region.visibility_requested = True; region.framing_requested = True; region.explicit_current_request = True
''',
    "semantic topless region",
)
guard = replace_once(
    guard,
    '''    if str(content_classification) == str(v2.ContentClassification.FULL_NUDITY) and str(adult_model or "").strip():
        return str(adult_model).strip()
''',
    '''    if str(content_classification) in {str(v2.ContentClassification.TOPLESS), str(v2.ContentClassification.FULL_NUDITY)} and str(adult_model or "").strip():
        return str(adult_model).strip()
''',
    "adult model selection",
)
guard_path.write_text(guard)


# 5) Enqueue/delivery: persist image commands, inherit the prior requested scene, separate refine-vs-resend source requirements, retain bytes for six hours.
service_path = Path("app/services/image_generation_service.py")
service = service_path.read_text()
service = replace_once(
    service,
    '''from app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_adult_scene_policy, select_generation_model
''',
    '''from app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_deterministic_adult_visual_intent, apply_adult_scene_policy, select_generation_model
''',
    "adult guard import",
)
service = replace_once(
    service,
    '''def _build_request_context(db: Session, user: User, user_request: str):
''',
    '''def inherit_recent_image_scene(intent, recent_conversation):
    """Carry the newest explicit image scene into a short follow-up image request."""
    from app.services import image_pipeline_v2 as v2
    contract=dict(getattr(intent, 'photo_contract', {}) or {})
    if intent.scene.scene_key or intent.scene.location or (contract.get('current_scene_from_chat') and contract.get('scene_context_summary')):
        return intent
    for message in reversed(list(recent_conversation or [])):
        if getattr(message, 'role', None) != 'user':
            continue
        text=str(getattr(message, 'content', '') or '')
        prior=v2.parse_image_intent(v2.normalize_request_v2(text))
        if not prior.is_image_request or not (prior.scene.scene_key or prior.scene.location):
            continue
        intent.scene.scene_key=prior.scene.scene_key
        intent.scene.location=prior.scene.location or prior.scene.scene_key
        intent.scene.environment_type=prior.scene.environment_type
        intent.scene.privacy=prior.scene.privacy
        intent.scene.explicit_current_request=False
        summary='; '.join(str(x) for x in (intent.scene.scene_key, intent.scene.location, intent.scene.environment_type) if x)
        contract.update({'current_scene_from_chat':True, 'scene_context_summary':summary, 'scene_explicit_current_request':False})
        intent.photo_contract=contract
        logger.info('IMAGE_RECENT_REQUEST_SCENE_INHERITED scene=%s location=%s', intent.scene.scene_key, intent.scene.location)
        break
    return intent


def mark_delivered_artifact_retained(job, artifact, *, retention_hours: int = 6):
    artifact.cleared_at=None
    job.metadata_json={**(job.metadata_json or {}), 'artifact_retention_hours':int(retention_hours), 'artifact_retained_for_continuity':True}
    return artifact


def _build_request_context(db: Session, user: User, user_request: str):
''',
    "scene inheritance and retention helpers",
)
service = replace_once(
    service,
    '''    intent=v2.parse_image_intent(norm)
    intent=apply_semantic_visual_intent_to_v2_intent(intent, getattr(route_decision, "semantic_decision", None), resolved_visual_intent=resolved_visual_intent)
''',
    '''    intent=v2.parse_image_intent(norm)
    intent=apply_semantic_visual_intent_to_v2_intent(intent, getattr(route_decision, "semantic_decision", None), resolved_visual_intent=resolved_visual_intent)
    intent=apply_deterministic_adult_visual_intent(intent, user_request)
''',
    "apply deterministic adult intent",
)
service = replace_once(
    service,
    '''    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)
    intent.photo_contract=attach_world_memory_context(getattr(intent, 'photo_contract', {}), relevant_memories)
''',
    '''    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)
    intent=inherit_recent_image_scene(intent, recent_conversation)
    intent.photo_contract=attach_world_memory_context(getattr(intent, 'photo_contract', {}), relevant_memories)
''',
    "inherit recent scene before routine",
)
old_source_logic = '''    requested_source_id=getattr(route_decision, 'source_image_job_id', None) if route_decision is not None else None
    source_job=db.get(ImageGenerationJob, requested_source_id) if requested_source_id else None
    if source_job and not v2.source_job_is_retrievable(source_job, user_id=user.id, chat_id=chat_id): source_job=None
    if source_job is None:
        source_job=v2.find_eligible_source_image_context(db, user_id=user.id, chat_id=chat_id) if intent.continuity.action in {v2.ImageAction.RESEND_EXACT, v2.ImageAction.VARIATION, v2.ImageAction.REFINEMENT} else None
'''
new_source_logic = '''    requested_source_id=getattr(route_decision, 'source_image_job_id', None) if route_decision is not None else None
    source_job=db.get(ImageGenerationJob, requested_source_id) if requested_source_id else None
    if source_job:
        valid_source=(v2.source_job_is_retrievable(source_job, user_id=user.id, chat_id=chat_id) if intent.continuity.action == v2.ImageAction.RESEND_EXACT else v2.source_job_is_context_eligible(source_job, user_id=user.id, chat_id=chat_id))
        if not valid_source: source_job=None
    if source_job is None and intent.continuity.action == v2.ImageAction.RESEND_EXACT:
        source_job=v2.find_eligible_source_artifact_context(db, user_id=user.id, chat_id=chat_id)
    elif source_job is None and intent.continuity.action in {v2.ImageAction.VARIATION, v2.ImageAction.REFINEMENT}:
        source_job=v2.find_eligible_source_image_context(db, user_id=user.id, chat_id=chat_id)
'''
service = replace_once(service, old_source_logic, new_source_logic, "source context vs exact artifact")
service = replace_once(
    service,
    '''        await GeneratedMediaArchiveService().archive_image(db, job)
        if job.archive_status in ('sent','disabled','skipped'): artifact.image_bytes=None; artifact.cleared_at=datetime.utcnow()
''',
    '''        await GeneratedMediaArchiveService().archive_image(db, job)
        mark_delivered_artifact_retained(job, artifact, retention_hours=6)
''',
    "retain delivered artifact",
)
service_path.write_text(service)


# 6) Telegram: persist every current image command (including denied refine requests) and use split source validation.
telegram_path = Path("app/api/telegram.py")
telegram = telegram_path.read_text()
telegram = replace_once(
    telegram,
    '''        # Pending clarification is marked resolved only after enqueue persists successfully.
        ok, source_error = validate_source_reference_deterministically(semantic_decision, recent_retrievable_image_exists=context.recent_retrievable_image_exists, allowed_job_ids={recent_img.id} if recent_img else set())
''',
    '''        # Persist image commands even when source validation later denies them, so the next image follow-up keeps the requested scene.
        if semantic_decision.action not in {SemanticImageAction.CHAT, SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}:
          existing_image_user_message=db.scalar(select(Message).where(Message.user_id==user.id, Message.role=='user', Message.telegram_message_id==msg.message_id).limit(1))
          if existing_image_user_message is None:
            db.add(Message(user_id=user.id, role='user', content=text, telegram_message_id=msg.message_id, telegram_reply_to_message_id=getattr(msg.reply_to_message, 'message_id', None), input_type='text', metadata_json={'source':'image_router','kind':'image_command'}))
            db.flush()
        # Pending clarification is marked resolved only after enqueue persists successfully.
        ok, source_error = validate_source_reference_deterministically(semantic_decision, recent_retrievable_image_exists=context.recent_retrievable_image_exists, recent_source_image_exists=bool(recent_img), recent_exact_artifact_exists=context.recent_exact_artifact_exists, allowed_job_ids={recent_img.id} if recent_img else set())
''',
    "persist image commands and split source validation",
)
telegram_path.write_text(telegram)


# 7) Remove the temporary production diagnostic and restore the normal app entry point.
docker_path = Path("Dockerfile")
docker = docker_path.read_text().replace(
    'CMD ["uvicorn", "app.ops_main:app", "--host", "0.0.0.0", "--port", "8000"]',
    'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]',
)
docker_path.write_text(docker)
for temporary in (Path("app/ops_main.py"), Path("app/api/ops_image_acceptance_diagnostic.py")):
    if temporary.exists():
        temporary.unlink()


# 8) Exact no-provider regression tests.
test_path = Path("tests/test_image_acceptance_regressions.py")
test_path.write_text('''from datetime import datetime, timedelta\nfrom types import SimpleNamespace\n\n\ndef test_adult_body_correction_routes_to_image():\n    from app.services.semantic_image_intent_router import SemanticImageAction, canonical_explicit_image_action\n    assert canonical_explicit_image_action("نه لختی میخوام. ممه هات معلوم باشه") == SemanticImageAction.GENERATE_NEW\n    assert canonical_explicit_image_action("خب بفرس عکس ممه هاتو") == SemanticImageAction.GENERATE_NEW\n\n\ndef test_deterministic_adult_guard_preserves_full_nudity_and_topless():\n    from app.services import image_pipeline_v2 as v2\n    from app.services.image_generation_guardrails import apply_deterministic_adult_visual_intent\n    nude=v2.parse_image_intent(v2.normalize_request_v2("یه عکس لختی بده بدنتو ببینم"))\n    nude=apply_deterministic_adult_visual_intent(nude, "یه عکس لختی بده بدنتو ببینم")\n    assert nude.content_classification == v2.ContentClassification.FULL_NUDITY\n    assert nude.adult_intent == "full_nudity"\n    topless=v2.parse_image_intent(v2.normalize_request_v2("خب بفرس عکس ممه هاتو"))\n    topless=apply_deterministic_adult_visual_intent(topless, "خب بفرس عکس ممه هاتو")\n    assert topless.content_classification == v2.ContentClassification.TOPLESS\n    assert topless.body_visibility.regions["breasts"].visibility_requested is True\n\n\ndef test_topless_and_full_nudity_use_adult_model():\n    from app.services import image_pipeline_v2 as v2\n    from app.services.image_generation_guardrails import select_generation_model\n    for classification in (v2.ContentClassification.TOPLESS, v2.ContentClassification.FULL_NUDITY):\n        assert select_generation_model(content_classification=classification, default_model="seedream-v5-lite", adult_model="lustify-sdxl") == "lustify-sdxl"\n\n\ndef test_refinement_uses_sent_plan_without_exact_bytes_but_resend_does_not():\n    from app.services import image_pipeline_v2 as v2\n    from app.services.semantic_image_intent_router import SemanticImageAction, SemanticImageDecision, validate_source_reference_deterministically\n    job=SimpleNamespace(user_id=1, chat_id=2, status="sent", sent_at=datetime.utcnow()-timedelta(minutes=5), resolved_plan_json={"plan_version":v2.PLAN_VERSION}, metadata_json={}, artifacts=[SimpleNamespace(image_bytes=None)])\n    assert v2.source_job_is_context_eligible(job, user_id=1, chat_id=2) is True\n    assert v2.source_job_is_retrievable(job, user_id=1, chat_id=2) is False\n    refine=SemanticImageDecision(action=SemanticImageAction.REFINE_PREVIOUS, media_delivery_requested=True, confidence=1, reason_code="test")\n    resend=SemanticImageDecision(action=SemanticImageAction.RESEND_EXACT, media_delivery_requested=True, confidence=1, reason_code="test")\n    assert validate_source_reference_deterministically(refine, recent_retrievable_image_exists=True, recent_source_image_exists=True, recent_exact_artifact_exists=False, allowed_job_ids=set()) == (True, None)\n    assert validate_source_reference_deterministically(resend, recent_retrievable_image_exists=True, recent_source_image_exists=True, recent_exact_artifact_exists=False, allowed_job_ids=set())[0] is False\n\n\ndef test_recent_denied_cafe_request_supplies_scene_to_short_full_body_followup():\n    from app.services import image_pipeline_v2 as v2\n    from app.services.image_generation_service import inherit_recent_image_scene\n    current=v2.parse_image_intent(v2.normalize_request_v2("یه عکس قدی بده"))\n    recent=[SimpleNamespace(role="user", content="همین قبلی رو تو کافه بده")]\n    current=inherit_recent_image_scene(current, recent)\n    assert current.scene.scene_key == "cafe" or current.scene.location == "cafe"\n    assert current.photo_contract["current_scene_from_chat"] is True\n\n\ndef test_delivered_artifact_is_retained_for_continuity():\n    from app.services.image_generation_service import mark_delivered_artifact_retained\n    job=SimpleNamespace(metadata_json={})\n    artifact=SimpleNamespace(cleared_at=datetime.utcnow(), image_bytes=b"image")\n    mark_delivered_artifact_retained(job, artifact, retention_hours=6)\n    assert artifact.image_bytes == b"image"\n    assert artifact.cleared_at is None\n    assert job.metadata_json["artifact_retained_for_continuity"] is True\n''')
