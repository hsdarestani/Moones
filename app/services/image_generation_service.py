from __future__ import annotations
import hashlib
import re
import logging
import json
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select, update, inspect
from sqlalchemy.orm import Session
from app.llm.image_client import VeniceImageClient, ImageClientError, image_resolution_tier, DEFAULT_IMAGE_MODEL, DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_STEPS, DEFAULT_CFG_SCALE, DEFAULT_SEED, VENICE_SEED_MIN, VENICE_SEED_MAX, normalize_venice_seed, validate_image_dimensions
from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact, ImageGenerationFeedback
from app.models.user import User
from app.models.usage import AiUsageEvent
from app.services.addon_service import user_has_addon, user_addon_enabled, user_owns_addon, ADULT_IMAGE_GENERATION_UNLOCK
from app.services.coin_pricing_service import CoinPricingService
from app.services.generated_media_archive_service import GeneratedMediaArchiveService
from app.services.provider_pricing_registry import get_price
from app.services.usage_billing_service import UsageBillingService, InsufficientCoins, new_correlation_id
from app.services.image_prompt_engine import IMAGE_ADDON_KEY, build_image_prompt, ensure_visual_profile, adult_requested, identity_fingerprint, stable_identity_descriptor
from app.services.conversation_time_service import ConversationTimeService
from app.services.partner_routine_service import PartnerRoutineService
from app.models.message import Message
from app.models.memory import MemoryItem
from app.services.media_continuity_service import record_media_delivery
from app.services.provider_error_screen_detector import detect_provider_error_screen
from app.services.generated_image_qa_service import GeneratedImageQAResult, evaluate_generated_image_composition, evaluate_single_subject_image, metadata_has_valid_generated_image_qa, corrective_prompt_for_reasons, qa_failure_user_message, evaluate_adult_anatomy_image
from app.core.config import get_settings
from app.services.image_request_state_machine import begin_or_update_chain, is_duplicate_command, mark_state, metadata_for_chain, ImageRequestState, sync_image_request_chain_state
from app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_adult_scene_policy, select_generation_model
from app.services.partner_photo_contract import attach_world_memory_context, build_partner_photo_contract


class ProviderPolicyScreenError(Exception):
    pass

class SingleSubjectImageQualityError(Exception):
    pass
from app.models.relationship import Relationship

logger=logging.getLogger(__name__)

class ImageGenerationDenied(Exception): pass

@dataclass
class CandidateValidationResult:
    image_bytes: bytes
    mime_type: str
    checksum: str
    generation_model: str
    request_id: str | None
    width: int | None
    height: int | None
    latency_seconds: float | None
    response_type: str | None
    metadata: dict
    detection: object
    qa: GeneratedImageQAResult



async def _evaluate_job_composition(qa_evaluator, image_bytes, job):
    metadata=job.metadata_json or {}
    kwargs={
        'expected_subject_count': int(metadata.get('expected_subject_count', 1)),
        'expected_interaction': metadata.get('interaction'),
        'selfie_allowed': bool(metadata.get('selfie_allowed')),
        'mirror_allowed': bool(metadata.get('mirror_allowed')),
        'visual_requirements': metadata.get('visual_requirements'),
        'previous_metadata': metadata.get('previous_image_metadata'),
    }
    try:
        return await qa_evaluator(image_bytes, **kwargs)
    except TypeError:
        kwargs.pop('visual_requirements', None); kwargs.pop('previous_metadata', None); kwargs.pop('expected_interaction', None)
        return await qa_evaluator(image_bytes, **kwargs)

def image_candidate_near_duplicate(*, checksum: str, metadata: dict|None, previous_metadata: dict|None=None) -> bool:
    metadata=metadata or {}; previous_metadata=previous_metadata or metadata.get('previous_image_metadata') or {}
    if not previous_metadata: return False
    if previous_metadata.get('checksum') == checksum or previous_metadata.get('artifact_checksum') == checksum: return True
    same_seed_family=metadata.get('seed_family') and metadata.get('seed_family') == previous_metadata.get('seed_family')
    same_frame=metadata.get('camera_mode') == previous_metadata.get('camera_mode') and metadata.get('framing') == previous_metadata.get('framing')
    same_seed=metadata.get('seed_used') and metadata.get('seed_used') == previous_metadata.get('seed_used')
    return bool((same_seed_family and same_frame) or same_seed)

async def validate_generated_candidate(*, image_bytes, generation_model, job, qa_evaluator, mime_type='image/png', request_id=None, width=None, height=None, latency_seconds=None, response_type=None, metadata=None) -> CandidateValidationResult:
    detection=detect_provider_error_screen(image_bytes)
    if detection.is_error_screen:
        raise ProviderPolicyScreenError('provider returned moderation screen image')
    checksum=hashlib.sha256(image_bytes).hexdigest()
    if (job.metadata_json or {}).get('route_action') in {'generate_new','new_generation'} and image_candidate_near_duplicate(checksum=checksum, metadata={**(metadata or {}), **(job.metadata_json or {})}):
        logger.info('IMAGE_NEAR_DUPLICATE_REJECTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', getattr(job,'user_id',None), getattr(job,'id',None), (job.metadata_json or {}).get('route_action'), (job.metadata_json or {}).get('continuity_mode'), ['near_duplicate_composition'])
        raise SingleSubjectImageQualityError('near-duplicate generated-image candidate rejected')
    qa=await _evaluate_job_composition(qa_evaluator, image_bytes, job)
    if not qa.passed:
        logger.info('IMAGE_QA_INTENT_FAILURE user_id=%s job_id=%s action=%s continuity_mode=%s qa_results=%s', getattr(job,'user_id',None), getattr(job,'id',None), (job.metadata_json or {}).get('route_action'), (job.metadata_json or {}).get('continuity_mode'), qa.reason_codes)
        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
    return CandidateValidationResult(image_bytes, mime_type, checksum, generation_model, request_id, width, height, latency_seconds, response_type, metadata or {}, detection, qa)


def deterministic_provider_seed(*parts: object) -> int:
    digest=int(hashlib.sha256(':'.join(str(p) for p in parts).encode()).hexdigest(),16)
    return VENICE_SEED_MIN + (digest % VENICE_SEED_MAX)

def _variation_requested(text: str, meta: dict | None = None) -> bool:
    m=meta or {}
    return bool(m.get('contextual_followup') or m.get('route_type') in {'image_followup','image_refinement'} or m.get('route_action') in {'variation','refinement','refine_previous'})


def _make_thumbnail(image_bytes: bytes, mime_type: str | None = None) -> tuple[bytes, str]:
    from PIL import Image
    with Image.open(BytesIO(image_bytes)) as im:
        im = im.convert('RGB')
        im.thumbnail((320, 320))
        out = BytesIO()
        im.save(out, format='JPEG', quality=85, optimize=True)
        data = out.getvalue()
        if data == image_bytes:
            raise RuntimeError('thumbnail_matches_full_image')
        return data, 'image/jpeg'

def _explicit_context_overrides(text: str) -> tuple[str | None, str | None]:
    t = text or ''
    time_map = [('نیمه‌شب','late_night'),('نیمه شب','late_night'),('صبح','morning'),('ظهر','noon'),('عصر','evening'),('غروب','evening'),('شب','night')]
    loc_map = [('خانه','خانه'),('خونه','خانه'),('کافه','کافه'),('خیابان','خیابان')]
    return next((v for k,v in time_map if k in t), None), next((v for k,v in loc_map if k in t), None)

def _build_request_context(db: Session, user: User, user_request: str):
    try:
        time_context = ConversationTimeService().build_context(db, user)
    except Exception:
        time_context = type('TimeContext', (), {'local_now': datetime.utcnow(), 'local_date': datetime.utcnow().date(), 'timezone_name': 'UTC', 'local_weekday': '', 'local_hour': datetime.utcnow().hour, 'daypart': 'day'})()
    routine_service = PartnerRoutineService()
    try:
        routine = routine_service.get_or_create_for_context(db, user, time_context)
        slot = routine_service.current_slot(routine, time_context)
    except Exception:
        routine = None
        slot = {'location': None, 'slot_name': getattr(time_context, 'daypart', 'day')}
    explicit_time, explicit_loc = _explicit_context_overrides(user_request)
    current_location = explicit_loc or slot.get('location') or getattr(routine, 'city', None)
    if explicit_time:
        slot = {**slot, 'slot_name': explicit_time}
    if 'messages' in inspect(db.bind).get_table_names():
        raw_recent = db.scalars(select(Message).where(Message.user_id==user.id).order_by(Message.created_at.desc(), Message.id.desc()).limit(24)).all()
    else:
        raw_recent = []
    cutoff = datetime.utcnow() - timedelta(minutes=60)
    recent_desc = []
    previous_created = None
    for m in raw_recent:
        if m.created_at and m.created_at < cutoff:
            break
        if previous_created and m.created_at and (previous_created - m.created_at) > timedelta(minutes=60):
            break
        recent_desc.append(m); previous_created = m.created_at
    recent = list(reversed(recent_desc))
    tables = inspect(db.bind).get_table_names()
    if 'memory_items' in tables:
        memories = db.scalars(select(MemoryItem).where(MemoryItem.user_id==user.id).order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc()).limit(5)).all()
        stored_visual_state = db.scalar(select(MemoryItem).where(MemoryItem.user_id==user.id, MemoryItem.type=='visual_scene_state', MemoryItem.created_at >= datetime.utcnow()-timedelta(hours=4)).order_by(MemoryItem.created_at.desc()).limit(1))
        if stored_visual_state: memories.append(stored_visual_state)
    else:
        memories = []
    rel = db.scalar(select(Relationship).where(Relationship.user_id==user.id)) if 'relationships' in tables else None
    rel_summary = None if not rel else f'stage={rel.stage}; intimacy={rel.intimacy}; trust={rel.trust}; attachment={rel.attachment}; attraction={rel.attraction}'
    snapshot = {'local_datetime': time_context.local_now.isoformat(), 'timezone': time_context.timezone_name, 'weekday': time_context.local_weekday, 'local_hour': time_context.local_hour, 'daypart': explicit_time or time_context.daypart, 'routine_slot': slot, 'current_location': current_location, 'mood': getattr(user, 'current_mood', None), 'relationship_state_summary': rel_summary}
    return time_context, slot, current_location, recent, memories, rel, snapshot



def _enqueue_image_request_v2(db: Session, *, user: User, chat_id:int, source_telegram_message_id:int, user_request:str, route_decision=None, resolved_action: str|None=None, resolved_visual_intent=None, clarification_resolved: bool=False) -> ImageGenerationJob:
    from app.services import image_pipeline_v2 as v2
    if not user_has_addon(db, user.id, IMAGE_ADDON_KEY) or not user_addon_enabled(db, user.id, IMAGE_ADDON_KEY):
        raise ImageGenerationDenied('addon_required')
    idem_action = resolved_action or getattr(route_decision, 'route', None) or 'image'
    idem=f'tg:image:v2:{user.telegram_id}:{chat_id}:{source_telegram_message_id}:{idem_action}'
    existing=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.idempotency_key==idem))
    if existing: return existing
    active_chain=begin_or_update_chain(db, user_id=user.id, action=resolved_action or getattr(route_decision, 'route', None) or 'generate_new', text=user_request, parent_request_id=None)
    if is_duplicate_command(active_chain, user_request):
        existing_chain_job=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user.id, ImageGenerationJob.request_chain_id==active_chain.request_chain_id, ImageGenerationJob.status.in_(['queued','generating','pending'])).order_by(ImageGenerationJob.created_at.desc()).limit(1))
        if existing_chain_job: return existing_chain_job
    logger.info('IMAGE_REQUEST_PERSISTED user_id=%s chat_id=%s source_message_id=%s', user.id, chat_id, source_telegram_message_id)
    norm=v2.normalize_request_v2(user_request, user_id=user.id, chat_id=chat_id, source_message_id=source_telegram_message_id)
    logger.info('IMAGE_REQUEST_NORMALIZED user_id=%s chat_id=%s', user.id, chat_id)
    intent=v2.parse_image_intent(norm)
    intent=apply_semantic_visual_intent_to_v2_intent(intent, getattr(route_decision, "semantic_decision", None), resolved_visual_intent=resolved_visual_intent)
    route_map={'image_explicit':v2.ImageAction.NEW_GENERATION,'image_followup':v2.ImageAction.VARIATION,'image_refinement':v2.ImageAction.REFINEMENT,'image_resend':v2.ImageAction.RESEND_EXACT,'semantic_generate_new':v2.ImageAction.NEW_GENERATION,'semantic_refine_previous':v2.ImageAction.REFINEMENT,'semantic_variation':v2.ImageAction.VARIATION,'semantic_resend_exact':v2.ImageAction.RESEND_EXACT}
    if resolved_action:
        resolved_map={'generate_new':v2.ImageAction.NEW_GENERATION,'refine_previous':v2.ImageAction.REFINEMENT,'variation':v2.ImageAction.VARIATION,'resend_exact':v2.ImageAction.RESEND_EXACT}
        intent.is_image_request=True; intent.continuity.action=resolved_map.get(str(resolved_action), v2.ImageAction.NEW_GENERATION)
        intent.parse_coverage.disposition=v2.ParseDisposition.BEST_EFFORT
        intent.parse_coverage.clarification_reason=None
        logger.info('IMAGE_CLARIFICATION_REPARSE_PREVENTED user_id=%s job_id=%s request_chain_id=%s action=%s framing=%s reason_code=%s', user.id, None, active_chain.request_chain_id, intent.continuity.action, getattr(intent.composition,'framing',None), 'resolved_action_authoritative')
    elif route_decision is not None and getattr(route_decision, 'route', 'chat') in route_map:
        intent.is_image_request=True; intent.continuity.action=route_map[getattr(route_decision, 'route')]
    disposition=str(intent.parse_coverage.disposition)
    if disposition == v2.ParseDisposition.BEST_EFFORT:
        logger.info('IMAGE_V2_BEST_EFFORT_PARSE user_id=%s chat_id=%s recognized_categories=%s passthrough_span_count=%s critical_unresolved_count=%s request_hash=%s', user.id, chat_id, intent.parse_coverage.recognized_categories, len(intent.parse_coverage.passthrough_visual_spans or []), len(intent.parse_coverage.critical_unresolved_spans or []), v2.passthrough_details_hash([user_request]))
        logger.info('IMAGE_PARSE_METRIC name=image_parse_best_effort_total value=1')
        logger.info('IMAGE_PARSE_METRIC name=image_parse_passthrough_span_count value=%s', len(intent.parse_coverage.passthrough_visual_spans or []))
    elif disposition == v2.ParseDisposition.CLARIFICATION_REQUIRED:
        reason=intent.parse_coverage.clarification_reason or 'image_action_ambiguous'
        logger.info('IMAGE_PARSE_METRIC name=image_parse_clarification_total value=1 reason=%s critical_unresolved_count=%s request_hash=%s', reason, len(intent.parse_coverage.critical_unresolved_spans or []), v2.passthrough_details_hash([user_request]))
        raise ImageGenerationDenied(reason)
    elif disposition == v2.ParseDisposition.DENY:
        logger.info('IMAGE_PARSE_METRIC name=image_parse_denied_total value=1 request_hash=%s', v2.passthrough_details_hash([user_request]))
        raise ImageGenerationDenied(intent.parse_coverage.clarification_reason or 'blocked')
    else:
        logger.info('IMAGE_PARSE_METRIC name=image_parse_complete_total value=1')
    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)
    intent.photo_contract=attach_world_memory_context(getattr(intent, 'photo_contract', {}), relevant_memories)
    requested_source_id=getattr(route_decision, 'source_image_job_id', None) if route_decision is not None else None
    source_job=db.get(ImageGenerationJob, requested_source_id) if requested_source_id else None
    if source_job and not v2.source_job_is_retrievable(source_job, user_id=user.id, chat_id=chat_id): source_job=None
    if source_job is None:
        source_job=v2.find_eligible_source_image_context(db, user_id=user.id, chat_id=chat_id) if intent.continuity.action in {v2.ImageAction.RESEND_EXACT, v2.ImageAction.VARIATION, v2.ImageAction.REFINEMENT} else None
    if source_job: intent.continuity.source_image_job_id=source_job.id
    if source_job is None and intent.continuity.action in {v2.ImageAction.RESEND_EXACT, v2.ImageAction.VARIATION, v2.ImageAction.REFINEMENT}:
        logger.info('IMAGE_PARSE_METRIC name=image_parse_clarification_total value=1 reason=image_source_ambiguous critical_unresolved_count=1 request_hash=%s', v2.passthrough_details_hash([user_request]))
        raise ImageGenerationDenied('image_source_ambiguous')
    logger.info('IMAGE_CONTINUITY_APPLIED user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s', user.id, intent.continuity.action, getattr(source_job,'id',None), 'source_job' if source_job else 'none', None, v2.PROMPT_ENGINE_VERSION)
    if intent.continuity.action == v2.ImageAction.RESEND_EXACT and source_job:
        job=ImageGenerationJob(idempotency_key=idem, correlation_id=new_correlation_id('image-resend'), user_id=user.id, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id, status='queued', content_mode='resend', user_request=user_request, prompt_engine_version=v2.PROMPT_ENGINE_VERSION, plan_version=v2.PLAN_VERSION, source_image_job_id=source_job.id, image_action=v2.ImageAction.RESEND_EXACT, usage_charge_id=None, seed=source_job.seed, final_provider_seed=None, policy_reason_code='resend_exact', metadata_json={'billing_action':'none','source_image_job_id':source_job.id,'route_action':v2.ImageAction.RESEND_EXACT})
        db.add(job); db.flush(); logger.info('IMAGE_RESEND_EXECUTED user_id=%s chat_id=%s job_id=%s source_job_id=%s', user.id, chat_id, job.id, source_job.id); return job
    adult_global = False
    soft_safety = True
    try:
        from app.services.settings_service import SettingsService
        settings = SettingsService()
        adult_global = settings.get_bool(db, 'image_generation.adult_enabled', False)
        soft_safety = settings.get_bool(db, 'image_generation.soft_safety_enabled', True)
    except Exception:
        adult_global = False; soft_safety = True
    scene_policy=apply_adult_scene_policy(intent, routine_slot)
    if scene_policy.denied_reason:
        raise ImageGenerationDenied(scene_policy.denied_reason)
    routine_slot=scene_policy.routine_context or {}
    policy_context=v2.AdultImagePolicyContext(adult_enabled=adult_global, soft_safety_enabled=soft_safety, normal_addon_owned=user_has_addon(db, user.id, IMAGE_ADDON_KEY), normal_addon_enabled=user_addon_enabled(db, user.id, IMAGE_ADDON_KEY), adult_addon_owned=user_owns_addon(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK), adult_addon_enabled=user_addon_enabled(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK), fictional_partner_min_age=getattr(user, 'fictional_partner_age', None) or getattr(user, 'fictional_age', None) or 18, parsed_body_visibility={k:v.__dict__ for k,v in intent.body_visibility.regions.items()}, nudity_level=str(intent.content_classification))
    safety=v2.evaluate_safety_policy(intent, policy_context)
    profile=v2.ensure_visual_profile_v2(db, user, ensure_visual_profile(db,user))
    previous=v2.deserialize_resolved_plan((source_job.resolved_plan_json if source_job else None) or ((source_job.metadata_json or {}).get('resolved_plan') if source_job else None))
    merged=v2.merge_image_intent(intent, previous, recent_context=recent_conversation, memory_context=relevant_memories, routine_context=routine_slot)
    logger.info('IMAGE_CONTEXT_RESOLVED user_id=%s action=%s source_job_id=%s continuity_mode=%s prompt_engine_version=%s reason_codes=%s', user.id, intent.continuity.action, getattr(source_job,'id',None), 'source_job' if source_job else 'none', v2.PROMPT_ENGINE_VERSION, [])
    logger.info('IMAGE_POLICY_DECIDED user_id=%s chat_id=%s policy_reason=%s decision=%s', user.id, chat_id, safety.reason_code, safety.decision)
    if safety.decision != v2.PolicyDecision.ALLOW:
        raise ImageGenerationDenied(safety.reason_code or 'blocked')
    active_job=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user.id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status.in_(['queued','processing','generating','sending','delivery_failed'])).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(1))
    if active_job and not clarification_resolved:
        logger.info('IMAGE_DUPLICATE_ENQUEUE_PREVENTED user_id=%s job_id=%s request_chain_id=%s action=%s job_status=%s reason_codes=%s', user.id, active_job.id, active_job.request_chain_id, getattr(active_job,'image_action',None), active_job.status, [])
        raise ImageGenerationDenied('active_image_job_exists')
    plan=v2.construct_resolved_plan(intent, merged, safety, profile, source_job=source_job, message_id=source_telegram_message_id, user_request=user_request)
    if scene_policy.private_scene_applied:
        plan.visual_requirements.environment_visibility_required=True
        plan.visual_requirements.visibility_targets.environment_visible=True
        plan.visual_requirements.must_satisfy['required_scene_elements']=['private_indoor', 'private indoor setting']
        plan.visual_requirements.reason_codes.append('adult_private_scene_required')
    runtime_settings=get_settings()
    generation_model=select_generation_model(content_classification=intent.content_classification, default_model=DEFAULT_IMAGE_MODEL, adult_model=getattr(runtime_settings, 'image_generation_adult_model', None))
    plan.provider_capability_decision.model=generation_model
    errors=v2.validate_plan_invariants(plan, source_job=source_job, user_id=user.id, chat_id=chat_id)
    logger.info('IMAGE_PLAN_VALIDATED user_id=%s chat_id=%s invariant_codes=%s', user.id, chat_id, errors)
    if errors: raise ImageGenerationDenied('plan_invariant_failed:' + ','.join(errors))
    if getattr(plan.visual_requirements, 'explicit_nudity_requested', False) and (getattr(plan.visual_requirements, 'anatomical_profile', None) in (None,'','unspecified')):
        logger.info('ADULT_ANATOMY_PROFILE_MISSING user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user.id, None, active_chain.request_chain_id, 'unspecified', None, ['anatomy_profile_missing'])
        raise ImageGenerationDenied('anatomy_profile_missing')
    compiled=v2.compile_image_prompt(plan)
    logger.info('IMAGE_PROMPT_COMPILED user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s reason_codes=%s', user.id, plan.action, getattr(source_job,'id',None), plan.seed_strategy.get('continuity_mode'), plan.seed_strategy.get('seed_strategy'), v2.PROMPT_ENGINE_VERSION, [])
    logger.info('IMAGE_SEED_RESOLVED user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s', user.id, plan.action, getattr(source_job,'id',None), plan.seed_strategy.get('continuity_mode'), plan.seed_strategy.get('seed_strategy'), v2.PROMPT_ENGINE_VERSION)
    prompt_errors=v2.validate_compiled_prompt(plan, compiled)
    logger.info('IMAGE_PROMPT_VALIDATED user_id=%s chat_id=%s invariant_codes=%s', user.id, chat_id, prompt_errors)
    if prompt_errors: raise ImageGenerationDenied('prompt_invariant_failed:' + ','.join(prompt_errors))
    quote=image_generation_quote(db, generation_model); correlation=new_correlation_id('image')
    logger.info('IMAGE_BILLING_DECIDED user_id=%s chat_id=%s action=%s billable=true', user.id, chat_id, plan.action)
    charge=UsageBillingService().reserve(db,user=user,idempotency_key=idem,feature='image_generation_bundle',provider='venice',model=generation_model,quote=quote,correlation_id=correlation,metadata={'label_fa':'ساخت تصویر مونس','image_action':plan.action,'request_chain_id':active_chain.request_chain_id})
    seed=int(plan.seed_strategy['final_provider_seed'])
    active_chain=mark_state(active_chain, ImageRequestState.QUEUED)
    chain_meta=metadata_for_chain(active_chain)
    job=ImageGenerationJob(idempotency_key=idem, correlation_id=correlation, user_id=user.id, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id, content_mode=str(plan.current_intent.get('content_classification') or ('suggestive' if plan.body_visibility else 'normal')), user_request=user_request, prompt=compiled.positive_prompt, negative_prompt=compiled.negative_prompt, prompt_engine_version=v2.PROMPT_ENGINE_VERSION, visual_profile_version=profile.version, identity_fingerprint=plan.identity['identity_fingerprint'], usage_charge_id=charge.id, resolved_plan_json=v2.plan_to_json(plan), plan_version=v2.PLAN_VERSION, source_image_job_id=getattr(source_job,'id',None), image_action=plan.action, request_chain_id=active_chain.request_chain_id, current_image_state=active_chain.current_image_state, parent_request_id=active_chain.parent_request_id, clarification_target=active_chain.clarification_target, resumed_after_topup=int(active_chain.resumed_after_topup), original_user_intent_snapshot=active_chain.original_user_intent_snapshot, identity_seed=plan.seed_strategy['identity_seed'], variation_index=plan.seed_strategy['variation_index'], final_provider_seed=seed, policy_reason_code=safety.reason_code, metadata_json={**chain_meta, 'seed_used':seed,'normalized_provider_seed':seed,'identity_fingerprint':plan.identity['identity_fingerprint'],'identity_descriptor':plan.identity['descriptor'],'identity_continuity':plan.identity.get('continuity'),'provider_capabilities':v2.ProviderImageCapabilities().__dict__,'selected_generation_model':generation_model,'adult_private_scene_policy_applied':scene_policy.private_scene_applied,'route_action':plan.action,'source_image_job_id':getattr(source_job,'id',None),'continuity_source_job_id':plan.seed_strategy.get('continuity_source_job_id'),'continuity_mode':plan.seed_strategy.get('continuity_mode'),'continuity_seed_strategy':plan.seed_strategy.get('seed_strategy'),'prompt_engine_version':v2.PROMPT_ENGINE_VERSION,'resolved_field_provenance':plan.composition.get('field_provenance'),'resolved_plan':v2.plan_to_json(plan),'content_classification':str(intent.content_classification),'adult_intent':intent.adult_intent,'wardrobe_level':intent.wardrobe.wardrobe,'body_visibility':{k:v.__dict__ for k,v in intent.body_visibility.regions.items()},'explicit_exclusions':intent.explicit_exclusions,'policy_decision':str(safety.decision),'billing_action':'reserve_generation','selfie_allowed':'one_person_selfie' in ((compiled.sections or {}).get('camera_mode') or '') or 'one_person_mirror_selfie' in ((compiled.sections or {}).get('camera_mode') or ''),'mirror_allowed':'one_person_mirror_selfie' in ((compiled.sections or {}).get('camera_mode') or ''),'camera_mode':(compiled.sections or {}).get('camera_mode'),'expected_subject_count':plan.composition.get('expected_subject_count',1),'primary_subject_role':plan.composition.get('primary_subject_role'),'secondary_subject_role':plan.composition.get('secondary_subject_role'),'interaction':plan.composition.get('interaction'),'all_subjects_fictional_adults':plan.composition.get('all_subjects_fictional_adults', True),'interaction_requires_consent':plan.composition.get('interaction_requires_consent', False),'visual_requirements':v2.asdict(plan.visual_requirements),'photo_contract':dict(getattr(plan.visual_requirements,'photo_contract',{}) or {}),'explicit_nudity_requested':bool(plan.visual_requirements.explicit_nudity_requested),'anatomy_qa_required':bool(plan.visual_requirements.anatomy_qa_required),'anatomy_qa_started':False,'anatomy_qa_completed':False,'anatomy_qa_passed':False,'anatomy_qa_blocked_delivery':False,'semantic_requested_scene':intent.scene.scene_key,'semantic_requested_location':intent.scene.location,'resolved_scene':plan.scene.value,'resolved_location':plan.location.value,'scene_provenance':str(plan.scene.source),'environment_visibility_required':plan.visual_requirements.environment_visibility_required,'qa_requested_scene':plan.scene.value,'qa_scene_matches_request':None,'router_requested_framing':getattr(getattr(route_decision, 'semantic_decision', None), 'visual_intent', None).framing if getattr(getattr(route_decision, 'semantic_decision', None), 'visual_intent', None) else None,'adapted_requested_framing':intent.composition.framing,'resolved_requested_framing':plan.visual_requirements.framing_requirement,'compiled_requested_framing':(compiled.sections or {}).get('visual_requirements',{}).get('framing_requirement'),'qa_requested_framing':None,'full_body_required':plan.visual_requirements.full_body_visible,'head_visible_required':plan.visual_requirements.head_visible,'feet_visible_required':plan.visual_requirements.feet_visible,'body_not_cropped_required':plan.visual_requirements.body_not_cropped,'continuity_plan':v2.asdict(plan.continuity_plan),'continuity_mode':plan.seed_strategy.get('continuity_mode'),'seed_strategy':plan.seed_strategy.get('seed_strategy'),'identity_seed':plan.seed_strategy.get('identity_seed'),'seed_family':plan.seed_strategy.get('seed_family'),'request_fingerprint':plan.seed_strategy.get('request_fingerprint'),'request_instance_key':plan.seed_strategy.get('request_instance_key'),'seed_branch':plan.seed_strategy.get('seed_branch'),'retry_branch':plan.seed_strategy.get('retry_branch',0),'final_provider_seed':plan.seed_strategy.get('final_provider_seed'),'continuity_source_job_id':plan.seed_strategy.get('continuity_source_job_id'),'invariant_codes':[], 'parse_disposition':str(intent.parse_coverage.disposition),'parse_confidence':intent.parse_coverage.confidence,'passthrough_visual_detail_count':len(intent.passthrough_visual_details or []),'critical_unresolved_count':len(intent.parse_coverage.critical_unresolved_spans or []),'visual_extractor_used':bool(getattr(route_decision, 'semantic_decision', None)),'visual_extractor_model':getattr(getattr(route_decision, 'semantic_decision', None), 'model', None),'visual_extractor_fallback_used':False, **(getattr(route_decision, 'clarification_metadata', {}) if route_decision is not None else {})}, model=generation_model, width=compiled.provider_parameters['width'], height=compiled.provider_parameters['height'], steps=DEFAULT_STEPS, cfg_scale=DEFAULT_CFG_SCALE, seed=seed)
    db.add(job); db.flush(); logger.info('IMAGE_JOB_ENQUEUED user_id=%s chat_id=%s job_id=%s action=%s seed=%s', user.id, chat_id, job.id, plan.action, seed); return job


def apply_semantic_visual_intent_to_v2_intent(intent, semantic_decision, *, resolved_visual_intent=None):
    """Copy the complete semantic partner-photo contract into V2 without losing detail."""
    if resolved_visual_intent is not None and isinstance(resolved_visual_intent, dict):
        from app.services.semantic_image_intent_router import VisualIntent
        resolved_visual_intent=VisualIntent(**resolved_visual_intent)
    if not resolved_visual_intent and (not semantic_decision or not getattr(semantic_decision, 'visual_intent', None)):
        return intent
    from app.services import image_pipeline_v2 as v2
    vi=resolved_visual_intent or semantic_decision.visual_intent
    free=list(getattr(vi, 'freeform_visual_constraints', []) or [])
    contract=build_partner_photo_contract(vi)
    intent.photo_contract=contract
    intent.expected_subject_count=int(contract.get('expected_human_subject_count', 1))

    if getattr(vi, 'scene', None): intent.scene.scene_key=vi.scene; intent.scene.explicit_current_request=True
    if getattr(vi, 'location', None): intent.scene.location=vi.location; intent.scene.explicit_current_request=True
    if getattr(vi, 'environment_type', None): intent.scene.environment_type=vi.environment_type
    if getattr(vi, 'privacy', None): intent.scene.privacy=vi.privacy
    if getattr(vi, 'required_visible_environment_elements', None): intent.scene.required_visible_environment_elements=list(vi.required_visible_environment_elements or [])
    if intent.scene.explicit_current_request:
        semantic_free=set(str(x) for x in free)
        intent.passthrough_visual_details=[x for x in intent.passthrough_visual_details if x in semantic_free]
        intent.parse_coverage.passthrough_visual_spans=[x for x in intent.parse_coverage.passthrough_visual_spans if x in semantic_free]
        logger.info('IMAGE_SEMANTIC_SCENE_RESOLVED user_id=%s job_id=%s request_chain_id=%s action=%s semantic_requested_scene=%s semantic_requested_location=%s', None, None, None, getattr(semantic_decision, 'action', None), intent.scene.scene_key, intent.scene.location)
    if getattr(vi, 'pose', None): intent.pose.pose=vi.pose
    if contract.get('back_to_camera') and not intent.pose.pose: intent.pose.pose='back_to_camera'
    if getattr(vi, 'activity', None): intent.visual_assertions.append(v2.VisualAssertion('subject','activity',vi.activity,(0,0),1.0))
    if getattr(vi, 'expression', None): intent.expression_modifiers.append(v2.ExpressionModifier('face','expression',vi.expression,(0,0)))
    if getattr(vi, 'wardrobe', None): intent.wardrobe.wardrobe=vi.wardrobe; intent.wardrobe.explicit_current_request=True
    intent.composition.camera=contract.get('camera_mode') or getattr(vi, 'camera', None)
    if getattr(vi, 'gaze_direction', None): intent.gaze_direction=vi.gaze_direction
    if getattr(vi, 'eye_contact_required', False) and not contract.get('face_hidden'): intent.eye_contact_required=True
    framing=contract.get('framing') or getattr(vi, 'framing', None)
    if framing:
        intent.composition.framing=framing
        if framing == 'full_body':
            intent.body_visibility.regions.setdefault('full_body', v2.BodyRegionIntent(mentioned=True, visibility_requested=True, framing_requested=True, explicit_current_request=True))
        logger.info('IMAGE_SEMANTIC_FRAMING_ADAPTED user_id=%s job_id=%s request_chain_id=%s action=%s framing=%s reason_code=%s', None, None, None, getattr(semantic_decision, 'action', None), framing, 'semantic_visual_intent')

    for region in contract.get('required_body_regions') or []:
        current=intent.body_visibility.regions.setdefault(region, v2.BodyRegionIntent())
        current.mentioned=True; current.visibility_requested=True; current.framing_requested=True; current.explicit_current_request=True
    for region in contract.get('forbidden_body_regions') or []:
        current=intent.body_visibility.regions.setdefault(region, v2.BodyRegionIntent())
        current.mentioned=True; current.visibility_negated=True; current.explicit_current_request=True
    if contract.get('face_hidden'):
        if 'visible face' not in intent.explicit_exclusions: intent.explicit_exclusions.append('visible face')
    if contract.get('partner_visible') is False:
        intent.explicit_exclusions.extend(x for x in ['human person','visible partner','human face'] if x not in intent.explicit_exclusions)

    for obj in list(dict.fromkeys((getattr(vi, 'visible_objects', []) or []) + (contract.get('visible_objects') or []))):
        if obj: intent.scene.spatial_relations.append(v2.SpatialRelation('visible_object', obj)); free.append(obj)
    for obj in list(dict.fromkeys((getattr(vi, 'held_objects', []) or []) + (contract.get('held_objects') or []))):
        if obj: intent.scene.spatial_relations.append(v2.SpatialRelation('held_object', obj)); free.append(obj)
    if contract.get('primary_subject') == 'pet': free.append('established pet is the primary subject')
    elif contract.get('primary_subject') == 'object': free.append('requested object is the primary subject')
    elif contract.get('primary_subject') == 'scene': free.append('requested scene is the primary subject')

    intent=apply_semantic_safety_contract(intent, vi, getattr(semantic_decision, 'safety_relevant_signals', None) if semantic_decision is not None else None)
    for ex in getattr(vi, 'exclusions', []) or []:
        if ex: intent.explicit_exclusions.append(ex)
    if getattr(vi, 'expected_subject_count', None) is not None and contract.get('partner_visible', True): intent.expected_subject_count=vi.expected_subject_count
    for val, label in ((getattr(vi,'lighting',None),'lighting'), (getattr(vi,'subject_focus',None),'subject_focus')):
        if val: free.append(f'{label}: {val}')
    if free:
        cleaned=[v2.sanitize_passthrough_visual_detail(x) for x in dict.fromkeys(free) if v2.sanitize_passthrough_visual_detail(x)]
        intent.passthrough_visual_details=list(dict.fromkeys(intent.passthrough_visual_details + cleaned))
        intent.parse_coverage.passthrough_visual_spans=list(dict.fromkeys(intent.parse_coverage.passthrough_visual_spans + cleaned))
        intent.visual_assertions.extend(v2.VisualAssertion('freeform_visual_constraints','constraint',x,(0,0),1.0) for x in cleaned)
        if intent.parse_coverage.disposition == v2.ParseDisposition.COMPLETE:
            intent.parse_coverage.disposition=v2.ParseDisposition.BEST_EFFORT
    return intent

def image_generation_quote(db: Session, model: str=DEFAULT_IMAGE_MODEL):
    pricing=CoinPricingService(); img=get_price('venice', model, image_resolution_tier(DEFAULT_WIDTH, DEFAULT_HEIGHT))
    prompt=pricing.quote_tokens(db, provider='venice', model='qwen-3-6-plus', feature='chat', input_tokens=1500, output_tokens=500)
    image=pricing.quote_usd(db, img.standard_rate_usd, {'feature':'image_generation','model':model,'resolution':'1024x1280','tier':image_resolution_tier(DEFAULT_WIDTH,DEFAULT_HEIGHT)})
    return pricing.quote_usd(db, prompt.provider_cost_usd + image.provider_cost_usd, {'bundle':['image_prompt','image_generation'], 'image': image.pricing_snapshot, 'prompt': prompt.pricing_snapshot})

def enqueue_image_request(db: Session, *, user: User, chat_id:int, source_telegram_message_id:int, user_request:str, route_decision=None, resolved_action: str|None=None, resolved_visual_intent=None, clarification_resolved: bool=False) -> ImageGenerationJob:
    from app.services.image_pipeline_v2_flags import resolve_image_pipeline_v2_flags

    flags = resolve_image_pipeline_v2_flags(db)
    if flags.execution_enabled:
        return _enqueue_image_request_v2(db, user=user, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id, user_request=user_request, route_decision=route_decision, resolved_action=resolved_action, resolved_visual_intent=resolved_visual_intent, clarification_resolved=clarification_resolved)
    if flags.shadow_enabled:
        # V2 shadow mode is intentionally detached/read-only here: no billing, no job insertion,
        # no profile/message mutation, no provider/Telegram calls, and no rollback touching caller state.
        try:
            from app.services import image_pipeline_v2 as v2
            result=v2.shadow_plan_read_only(user_request, user_id=user.id, chat_id=chat_id, source_message_id=source_telegram_message_id, legacy_route=getattr(route_decision, 'route', 'chat'))
            logger.info('IMAGE_V2_SHADOW_RESULT %s', json.dumps(result, ensure_ascii=False, sort_keys=True))
        except Exception as exc:
            logger.info('IMAGE_V2_SHADOW_FAILED user_id=%s chat_id=%s error=%s', user.id, chat_id, type(exc).__name__)
    if not user_has_addon(db, user.id, IMAGE_ADDON_KEY) or not user_addon_enabled(db, user.id, IMAGE_ADDON_KEY): raise ImageGenerationDenied('addon_required')
    profile=ensure_visual_profile(db,user)
    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)
    adult_intent = adult_requested(user_request)
    adult_owned = user_owns_addon(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK)
    adult_enabled = user_addon_enabled(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK)
    result=build_image_prompt(db,user=user,user_request=user_request,visual_profile=profile,adult_mode_requested=adult_intent,time_context=time_context,routine_slot=routine_slot,current_location=current_location,mood=getattr(user,'current_mood',None),recent_conversation=recent_conversation,relevant_memories=relevant_memories,relationship_state=relationship_state)
    if result.safety_decision!='allow': raise ImageGenerationDenied(result.safety_reason or 'blocked')
    idem=f'tg:image:{user.telegram_id}:{source_telegram_message_id}'
    existing=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.idempotency_key==idem))
    if existing: return existing
    correlation=new_correlation_id('image')
    quote=image_generation_quote(db)
    charge=UsageBillingService().reserve(db,user=user,idempotency_key=idem,feature='image_generation_bundle',provider='venice',model=DEFAULT_IMAGE_MODEL,quote=quote,correlation_id=correlation,metadata={'label_fa':'ساخت تصویر مونس'})
    width, height = validate_image_dimensions(result.width, result.height, model=DEFAULT_IMAGE_MODEL)
    plan = result.resolved_plan
    visual_state = plan.visual_scene_state if plan else None
    comp = plan.composition_plan if plan else None
    ident_fp = identity_fingerprint(profile)
    ident_desc = stable_identity_descriptor(profile)
    requested_seed=int(hashlib.sha256(f'{profile.base_seed}:{source_telegram_message_id}:{user_request}'.encode()).hexdigest()[:8],16)
    selected_seed=deterministic_provider_seed(profile.base_seed, source_telegram_message_id, user_request)
    job=ImageGenerationJob(idempotency_key=idem,correlation_id=correlation,user_id=user.id,chat_id=chat_id,source_telegram_message_id=source_telegram_message_id,content_mode=result.content_mode,user_request=user_request,prompt=result.prompt,negative_prompt=result.negative_prompt,prompt_engine_version=result.prompt_engine_version,visual_profile_version=profile.version,identity_fingerprint=ident_fp,usage_charge_id=charge.id,metadata_json={**snapshot,'adult_intent_detected':adult_intent,'requested_seed':requested_seed,'normalized_provider_seed':selected_seed,'seed_normalization_applied':requested_seed != selected_seed,'seed_provider_min':VENICE_SEED_MIN,'seed_provider_max':VENICE_SEED_MAX,'seed_used':selected_seed,'identity_fingerprint':ident_fp,'identity_descriptor':ident_desc,'identity_strategy':'text_prompt_best_effort','provider_capabilities':{'supports_reference_image':False,'supports_identity_conditioning':False,'supports_image_to_image':False},'adult_nudity_level':result.adult_nudity_level,'adult_body_emphasis':result.adult_body_emphasis,'adult_scene_override':result.adult_scene_override,'adult_pose_override':result.adult_pose_override,'stale_scene_reset':result.stale_scene_reset,'stale_scene_reset_reason':result.stale_scene_reset_reason,'final_environment_type':result.final_environment_type,'final_pose_type':result.final_pose_type,'final_wardrobe_intent':result.final_wardrobe_intent,'adult_entitlement_owned':adult_owned,'adult_addon_enabled':adult_enabled,'adult_gate_result':('allow' if result.safety_decision=='allow' else (result.safety_reason or 'blocked')),'context_summary':result.input_context_summary,'influenced_by_job_ids':result.influenced_by_job_ids,'route_type':getattr(route_decision, 'route', 'image_explicit'),'contextual_followup':getattr(route_decision, 'contextual_followup', False),'route_reason_code':getattr(route_decision, 'reason_code', None),'source_image_job_id':getattr(route_decision, 'source_image_job_id', None),'adult_intent':result.adult_visual_intent,'nudity_level':result.adult_nudity_level,'wardrobe_intent':result.final_wardrobe_intent,'body_emphasis':result.adult_body_emphasis,'explicit_current_fields':(plan.intent.explicit_current_fields if plan else []),'field_provenance':(plan.intent.field_provenance if plan else {}),'continuity_action':(plan.intent.continuity_action if plan else 'unspecified'),'safety_reason':result.safety_reason,'privacy_policy_result':(plan.privacy_policy_result if plan else 'allow'),'final_location':result.location,'final_activity':(visual_state.activity if visual_state else None),'final_pose':result.pose,'support_surface':(visual_state.support_surface if visual_state else None),'final_composition_key':(comp.composition_key if comp else None),'final_subject_frame_share':(comp.subject_frame_share if comp else None),'final_camera_distance':(comp.camera_distance if comp else None),'required_environment_objects':(comp.required_environment_objects if comp else []),'invariant_codes':(plan.validation_results if plan else []),'orientation':result.orientation,'composition_key':(comp.composition_key if comp else None),'requested_close_framing':(comp.requested_close_framing if comp else False),'subject_frame_share':(comp.subject_frame_share if comp else None),'camera_distance':(comp.camera_distance if comp else None),'environment_type':(visual_state.environment_type if visual_state else None),'activity':(visual_state.activity if visual_state else None),'objects':(visual_state.held_objects if visual_state else []),'extraction_source':(visual_state.source_role if visual_state else None),'resolved_plan': {'prompt_engine_version': result.prompt_engine_version, 'composition_key': (comp.composition_key if comp else None), 'subject_frame_share': (comp.subject_frame_share if comp else None), 'environment_type': (visual_state.environment_type if visual_state else None)},'visual_state':{'environment_type':(visual_state.environment_type if visual_state else None),'location':(visual_state.location if visual_state else None),'activity':(visual_state.activity if visual_state else None),'subject_action':(visual_state.subject_action if visual_state else None),'held_objects':(visual_state.held_objects if visual_state else []),'pose':(visual_state.pose if visual_state else None),'support_surface':(visual_state.support_surface if visual_state else None),'source_message':(visual_state.source_message if visual_state else None)}},model=DEFAULT_IMAGE_MODEL,width=width,height=height,steps=DEFAULT_STEPS,cfg_scale=DEFAULT_CFG_SCALE,seed=selected_seed)
    db.add(job); db.flush(); return job

def claim_next_job(db: Session, *, lock_seconds:int=300) -> ImageGenerationJob|None:
    now=datetime.utcnow(); expires=now+timedelta(seconds=lock_seconds)
    stmt=select(ImageGenerationJob).where(ImageGenerationJob.status.in_(['queued','delivery_failed']), ImageGenerationJob.scheduled_at<=now, ((ImageGenerationJob.lock_expires_at==None) | (ImageGenerationJob.lock_expires_at<now))).order_by(ImageGenerationJob.scheduled_at).with_for_update(skip_locked=True).limit(1)
    job=db.scalar(stmt)
    if job:
        job.locked_at=now; job.lock_expires_at=expires; job.status='processing' if job.status=='queued' else 'sending'; job.attempt_count+=1; db.flush()
    return job

async def process_job(db: Session, job: ImageGenerationJob, *, image_client=None, telegram_service=None, generated_image_qa_evaluator=None) -> ImageGenerationJob:
    billing=UsageBillingService(); qa_evaluator=generated_image_qa_evaluator or evaluate_single_subject_image; charge=db.get(__import__('app.models.billing', fromlist=['UsageCharge']).UsageCharge, job.usage_charge_id) if job.usage_charge_id else None
    if telegram_service is None:
        job.status='delivery_failed'; job.error_code='telegram_delivery'; job.error_message='telegram_service_required'; job.failed_at=datetime.utcnow(); job.lock_expires_at=None; db.flush()
        raise RuntimeError('telegram_service_required')
    try:
        if getattr(job, 'image_action', None) == 'resend_exact' and getattr(job, 'source_image_job_id', None):
            source_artifact=db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.source_image_job_id))
            source_job=db.get(ImageGenerationJob, job.source_image_job_id)
            if not source_artifact or not source_artifact.image_bytes or not source_job:
                job.status='failed'; job.error_code='resend_artifact_unavailable'; job.error_message='source artifact unavailable for exact resend'; job.failed_at=datetime.utcnow(); job.lock_expires_at=None; db.flush(); return job
            logger.info('IMAGE_RESEND_EXECUTED job_id=%s source_job_id=%s user_id=%s chat_id=%s', job.id, job.source_image_job_id, job.user_id, job.chat_id)
            src_meta=source_job.metadata_json or {}; src_vr=src_meta.get('visual_requirements') or {}; src_aqa=src_meta.get('adult_anatomy_qa') or {}
            if bool(src_vr.get('explicit_nudity_requested') and src_vr.get('anatomy_qa_required')) and not metadata_has_valid_generated_image_qa(source_job.metadata_json, source_artifact.image_bytes or b''):
                reason_codes=src_aqa.get('reason_codes') or ['anatomy_qa_absent_or_failed']
                job.metadata_json={**(job.metadata_json or {}),'anatomy_qa_blocked_delivery':True,'resend_source_qa_invalid':True,'generated_image_quality_failures':[{'reason_codes':reason_codes}]}
                logger.info('ADULT_ANATOMY_DELIVERY_BLOCKED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', job.user_id, job.id, job.request_chain_id, src_vr.get('anatomical_profile'), src_aqa.get('confidence'), reason_codes)
                raise SingleSubjectImageQualityError('resend source QA checksum missing or mismatched')
            delivery=await telegram_service.send_photo_bytes(job.chat_id, source_artifact.image_bytes, filename='moones-image.jpg', mime_type=source_artifact.mime_type, caption='اینم همون عکس قبلی 🤍')
            mid=getattr(delivery, 'message_id', delivery)
            job.telegram_message_id=mid; job.delivery_message_id=mid; job.status='sent'; job.sent_at=datetime.utcnow(); job.lock_expires_at=None; job.metadata_json={**(job.metadata_json or {}),'resend_delivery_message_id':mid,'provider_usage_event':False,'billing_action':'none'}
            logger.info('IMAGE_DELIVERY_COMPLETED job_id=%s user_id=%s chat_id=%s telegram_message_id=%s action=resend_exact', job.id, job.user_id, job.chat_id, mid)
            db.flush(); return job
        artifact=db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id))
        reused=bool(artifact and artifact.image_bytes)
        if not reused:
            logger.info("IMAGE_PROVIDER_REQUESTED job_id=%s user_id=%s chat_id=%s attempt_count=%s seed=%s", job.id, job.user_id, job.chat_id, job.attempt_count, job.seed)
            job.started_at=datetime.utcnow(); client=image_client or VeniceImageClient();
            settings=get_settings()
            meta=job.metadata_json or {}
            primary_model = (meta.get('primary_generation_model') or job.model or DEFAULT_IMAGE_MODEL)
            fallback_model = (getattr(settings, 'image_generation_fallback_model', '') or '').strip()
            model_plan = [primary_model]
            if fallback_model and fallback_model not in model_plan:
                model_plan.append(fallback_model)
            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'final_generation_model':None}
            res = None
            detection = None
            successful_model = None
            moderation_checksums=[]
            rejected_quality=[]
            accepted_qa=None
            for attempt_index, attempt_model in enumerate(model_plan):
                try:
                    attempt_seed, norm_applied = normalize_venice_seed(job.seed, salt=f'job:{job.id}:{attempt_model}')
                    job.metadata_json={**(job.metadata_json or {}),'normalized_provider_seed':attempt_seed,'seed_normalization_applied': bool((job.metadata_json or {}).get('seed_normalization_applied') or norm_applied),'seed_provider_min':VENICE_SEED_MIN,'seed_provider_max':VENICE_SEED_MAX}
                    attempt_prompt=(job.prompt or '') + (corrective_prompt_for_reasons(rejected_quality[-1]['reason_codes'], expected_subject_count=int((job.metadata_json or {}).get('expected_subject_count', 1)), expected_interaction=(job.metadata_json or {}).get('interaction'), secondary_subject_role=(job.metadata_json or {}).get('secondary_subject_role'), identity_requirements=(job.metadata_json or {}).get('identity_descriptor'), photo_contract=((job.metadata_json or {}).get('visual_requirements') or {}).get('photo_contract')) if rejected_quality and attempt_index > 0 else '')
                    res=await client.generate(attempt_prompt, job.negative_prompt or '', width=job.width, height=job.height, seed=attempt_seed, model=attempt_model)
                except TypeError:
                    res=await client.generate(job.prompt or '', job.negative_prompt or '', width=job.width, height=job.height, seed=job.seed)
                    attempt_seed=job.seed
                detection=detect_provider_error_screen(res.image_bytes)
                response_checksum=hashlib.sha256(res.image_bytes).hexdigest()
                prompt_hash=hashlib.sha256((job.prompt or '').encode()).hexdigest()
                negative_prompt_hash=hashlib.sha256((job.negative_prompt or '').encode()).hexdigest()
                attempts=list((job.metadata_json or {}).get('provider_model_attempts') or [])
                attempt={'provider': job.provider, 'model': attempt_model, 'provider_request_id': res.request_id, 'response_type': res.response_type, 'seed': attempt_seed, 'payload_profile': (res.metadata or {}).get('payload_profile') or ('seedream_4_5_1k' if attempt_model == 'seedream-v5-lite' else 'krea_1024x1280'), 'prompt_hash': prompt_hash, 'negative_prompt_hash': negative_prompt_hash, 'moderation_screen_detected': detection.is_error_screen, 'moderation_screen_reason': detection.reason if detection.is_error_screen else None}
                if getattr(detection, 'diagnostics', None):
                    attempt['detector_metrics'] = detection.diagnostics
                attempts.append(attempt)
                update={'provider_model_attempts':attempts}
                if detection.is_error_screen:
                    moderation_checksums.append(response_checksum)
                    if len(set(moderation_checksums)) == 1 and len(moderation_checksums) > 1:
                        update['identical_provider_error_artifact']=True
                    update.update({'moderation_screen_detected':True,'moderation_screen_reason':detection.reason,'moderation_screen_confidence':detection.confidence})
                    job.metadata_json={**(job.metadata_json or {}),**update}
                    logger.warning('IMAGE_PROVIDER_ERROR_SCREEN_DETECTED job_id=%s user_id=%s chat_id=%s reason=%s confidence=%s model=%s attempt_count=%s', job.id, job.user_id, job.chat_id, detection.reason, detection.confidence, attempt_model, job.attempt_count)
                    continue
                qa=await _evaluate_job_composition(qa_evaluator, res.image_bytes, job)
                if qa.passed and ((job.metadata_json or {}).get('visual_requirements') or {}).get('anatomy_qa_required'):
                    vr=(job.metadata_json or {}).get('visual_requirements') or {}
                    job.metadata_json={**(job.metadata_json or {}),'explicit_nudity_requested':True,'anatomy_qa_required':True,'anatomy_qa_started':True}
                    anatomy_qa=await evaluate_adult_anatomy_image(res.image_bytes, anatomical_profile=vr.get('anatomical_profile'), user_id=job.user_id, job_id=job.id, request_chain_id=job.request_chain_id)
                    job.metadata_json={**(job.metadata_json or {}), 'adult_anatomy_qa': anatomy_qa.to_metadata(artifact_checksum=response_checksum),'anatomy_qa_completed':True,'anatomy_qa_passed':bool(anatomy_qa.passed)}
                    if not anatomy_qa.passed:
                        qa.passed=False; qa.reason_codes=list(dict.fromkeys((qa.reason_codes or []) + (anatomy_qa.reason_codes or []))); qa.confidence=anatomy_qa.confidence
                        job.metadata_json={**(job.metadata_json or {}),'anatomy_qa_blocked_delivery':True}
                        if set(anatomy_qa.reason_codes or []) & {'malformed_anatomy','implausible_anatomy','duplicated_anatomy_parts','missing_expected_parts_when_visible'}:
                            logger.info('ADULT_ANATOMY_MALFORMED_REJECTED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', job.user_id, job.id, job.request_chain_id, vr.get('anatomical_profile'), anatomy_qa.confidence, anatomy_qa.reason_codes)
                        logger.info('ADULT_ANATOMY_DELIVERY_BLOCKED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', job.user_id, job.id, job.request_chain_id, vr.get('anatomical_profile'), anatomy_qa.confidence, anatomy_qa.reason_codes)
                        logger.info('ADULT_ANATOMY_RETRY_TRIGGERED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', job.user_id, job.id, job.request_chain_id, vr.get('anatomical_profile'), anatomy_qa.confidence, anatomy_qa.reason_codes)
                attempt['generated_image_qa']={'passed':qa.passed,'person_count':qa.person_count,'face_count':qa.face_count,'confidence':qa.confidence,'reason_codes':qa.reason_codes,'model':qa.model,'artifact_checksum':response_checksum}
                update['provider_model_attempts']=attempts
                update['qa_scene_matches_request']=qa.requested_scene_visible
                update['reflection_visible']=qa.reflection_visible
                update['reflection_matches_primary_subject']=qa.reflection_matches_primary_subject
                if not qa.passed:
                    rejected_quality.append({'model':attempt_model,'reason_codes':qa.reason_codes,'person_count':qa.person_count,'face_count':qa.face_count,'confidence':qa.confidence,'artifact_checksum_prefix':response_checksum[:12]})
                    update['generated_image_quality_failures']=rejected_quality
                    job.metadata_json={**(job.metadata_json or {}),**update}
                    logger.warning('IMAGE_SINGLE_SUBJECT_QA_FAILED job_id=%s user_id=%s chat_id=%s generation_model=%s qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, attempt_model, qa.model, qa.person_count, qa.face_count, qa.confidence, qa.reason_codes, response_checksum[:12])
                    if attempt_index + 1 < len(model_plan):
                        logger.info('IMAGE_SINGLE_SUBJECT_RETRY job_id=%s user_id=%s chat_id=%s generation_model=%s next_generation_model=%s reason_codes=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, attempt_model, model_plan[attempt_index+1], qa.reason_codes, response_checksum[:12])
                    continue
                successful_model=attempt_model
                accepted_qa=qa
                update.update({'moderation_screen_detected':False,'final_generation_model':attempt_model,'generated_image_qa':qa.to_metadata(artifact_checksum=response_checksum),'qa_requested_framing':(job.metadata_json or {}).get('resolved_requested_framing'),'qa_scene_matches_request':qa.requested_scene_visible,'reflection_visible':qa.reflection_visible,'reflection_matches_primary_subject':qa.reflection_matches_primary_subject})
                if attempt_model != model_plan[0]:
                    update['fallback_model_used']=True
                job.metadata_json={**(job.metadata_json or {}),**update}
                break
            if res is None or successful_model is None:
                if rejected_quality:
                    logger.warning('IMAGE_SINGLE_SUBJECT_FINAL_FAILED job_id=%s user_id=%s chat_id=%s reason_codes=%s', job.id, job.user_id, job.chat_id, rejected_quality[-1].get('reason_codes'))
                    raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
                raise ProviderPolicyScreenError('provider returned moderation screen image')
            if not artifact:
                artifact=ImageGenerationArtifact(job_id=job.id,mime_type=res.mime_type,checksum='',byte_size=0,image_bytes=None); db.add(artifact); db.flush()
            artifact.mime_type=res.mime_type; artifact.checksum=hashlib.sha256(res.image_bytes).hexdigest()
            if str(getattr(job, 'image_action', '') or '') in {'new_generation', 'generate_new'}:
                latest_delivered=db.scalar(select(ImageGenerationArtifact).join(ImageGenerationJob).where(ImageGenerationJob.user_id==job.user_id, ImageGenerationJob.chat_id==job.chat_id, ImageGenerationJob.id!=job.id, ImageGenerationJob.status=='sent', ImageGenerationJob.image_action!='resend_exact').order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))
                latest_duplicate=latest_delivered if latest_delivered and latest_delivered.checksum == artifact.checksum else None
                retry_branch=int((job.metadata_json or {}).get('retry_branch') or 0)
                while latest_duplicate and retry_branch + 1 < int(getattr(job, 'max_attempts', 3) or 3):
                    retry_branch += 1
                    from app.services import image_pipeline_v2 as v2
                    seed_diag=v2.resolve_image_seed((job.metadata_json or {}).get('identity_seed') or job.identity_seed or job.seed, job.image_action, (job.metadata_json or {}).get('request_fingerprint'), None, (((job.metadata_json or {}).get('continuity_plan') or {}).get('requested_variation_axes')), request_instance_key=(job.metadata_json or {}).get('request_instance_key'), retry_branch=retry_branch)
                    job.seed=int(seed_diag['final_provider_seed']); job.final_provider_seed=job.seed
                    job.metadata_json={**(job.metadata_json or {}), **{k:seed_diag.get(k) for k in ['identity_seed','seed_family','request_fingerprint','request_instance_key','seed_branch','retry_branch','final_provider_seed','continuity_source_job_id','seed_strategy']}, 'duplicate_checksum_detected':artifact.checksum, 'duplicate_retry_applied':True}
                    res=await client.generate(job.prompt or '', job.negative_prompt or '', width=job.width, height=job.height, seed=job.seed, model=successful_model)
                    detection=detect_provider_error_screen(res.image_bytes)
                    if detection.is_error_screen:
                        raise ProviderPolicyScreenError('provider returned moderation screen image')
                    replacement_checksum=hashlib.sha256(res.image_bytes).hexdigest()
                    replacement_qa=await _evaluate_job_composition(qa_evaluator, res.image_bytes, job)
                    if replacement_qa.passed and ((job.metadata_json or {}).get('visual_requirements') or {}).get('anatomy_qa_required'):
                        vr=(job.metadata_json or {}).get('visual_requirements') or {}; job.metadata_json={**(job.metadata_json or {}),'anatomy_qa_started':True}
                        replacement_anatomy=await evaluate_adult_anatomy_image(res.image_bytes, anatomical_profile=vr.get('anatomical_profile'), user_id=job.user_id, job_id=job.id, request_chain_id=job.request_chain_id)
                        job.metadata_json={**(job.metadata_json or {}),'adult_anatomy_qa':replacement_anatomy.to_metadata(artifact_checksum=replacement_checksum),'anatomy_qa_completed':True,'anatomy_qa_passed':bool(replacement_anatomy.passed)}
                        if not replacement_anatomy.passed:
                            replacement_qa.passed=False; replacement_qa.reason_codes=list(dict.fromkeys((replacement_qa.reason_codes or [])+(replacement_anatomy.reason_codes or []))); replacement_qa.confidence=replacement_anatomy.confidence
                    job.metadata_json={**(job.metadata_json or {}),'generated_image_qa':replacement_qa.to_metadata(artifact_checksum=replacement_checksum),'duplicate_retry_provider_request_id':res.request_id}
                    if not replacement_qa.passed:
                        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
                    artifact.checksum=replacement_checksum
                    latest_delivered=db.scalar(select(ImageGenerationArtifact).join(ImageGenerationJob).where(ImageGenerationJob.user_id==job.user_id, ImageGenerationJob.chat_id==job.chat_id, ImageGenerationJob.id!=job.id, ImageGenerationJob.status=='sent', ImageGenerationJob.image_action!='resend_exact').order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))
                    latest_duplicate=latest_delivered if latest_delivered and latest_delivered.checksum == artifact.checksum else None
                if latest_duplicate:
                    job.metadata_json={**(job.metadata_json or {}),'duplicate_checksum_detected':artifact.checksum,'duplicate_delivery_blocked':True,'retry_branch':retry_branch}
                    raise SingleSubjectImageQualityError('exact duplicate generated image blocked')
            if _variation_requested(job.user_request or '', job.metadata_json):
                duplicate=db.scalar(select(ImageGenerationArtifact).join(ImageGenerationJob).where(ImageGenerationJob.user_id==job.user_id, ImageGenerationJob.id!=job.id, ImageGenerationJob.status=='sent', ImageGenerationArtifact.checksum==artifact.checksum).order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))
                if duplicate:
                    old_seed=job.seed; job.seed=deterministic_provider_seed(job.seed, job.id, 'duplicate-variation-retry')
                    job.metadata_json={**(job.metadata_json or {}),'duplicate_checksum_detected':artifact.checksum,'duplicate_retry_applied':True,'duplicate_retry_previous_seed':old_seed,'normalized_provider_seed':job.seed,'seed_used':job.seed}
                    res=await client.generate(job.prompt or '', job.negative_prompt or '', width=job.width, height=job.height, seed=job.seed, model=successful_model)
                    detection=detect_provider_error_screen(res.image_bytes)
                    if detection.is_error_screen:
                        job.metadata_json={**(job.metadata_json or {}),'moderation_screen_detected':True,'moderation_screen_reason':detection.reason,'moderation_screen_confidence':detection.confidence}
                        logger.warning('IMAGE_PROVIDER_ERROR_SCREEN_DETECTED job_id=%s user_id=%s chat_id=%s reason=%s confidence=%s attempt_count=%s', job.id, job.user_id, job.chat_id, detection.reason, detection.confidence, job.attempt_count)
                        raise ProviderPolicyScreenError('provider returned moderation screen image')
                    replacement_checksum=hashlib.sha256(res.image_bytes).hexdigest()
                    replacement_qa=await _evaluate_job_composition(qa_evaluator, res.image_bytes, job)
                    job.metadata_json={**(job.metadata_json or {}),'generated_image_qa':replacement_qa.to_metadata(artifact_checksum=replacement_checksum),'qa_requested_framing':(job.metadata_json or {}).get('resolved_requested_framing'),'final_generation_model':successful_model,'duplicate_retry_provider_request_id':res.request_id}
                    if not replacement_qa.passed:
                        logger.warning('IMAGE_SINGLE_SUBJECT_QA_FAILED job_id=%s user_id=%s chat_id=%s generation_model=%s qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, successful_model, replacement_qa.model, replacement_qa.person_count, replacement_qa.face_count, replacement_qa.confidence, replacement_qa.reason_codes, replacement_checksum[:12])
                        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
                    artifact.checksum=replacement_checksum
            artifact.byte_size=len(res.image_bytes); artifact.image_bytes=res.image_bytes; artifact.cleared_at=None
            actual_seed = int(
                (res.metadata or {}).get(
                    'seed_used',
                    job.seed,
                )
            )

            job.generated_at = datetime.utcnow()
            job.provider_request_id = res.request_id
            job.final_provider_seed = actual_seed

            job.metadata_json = {
                **(job.metadata_json or {}),
                'provider_latency': res.latency_seconds,
                'response_type': res.response_type,
                'actual_width': res.width,
                'actual_height': res.height,
                'seed_used': actual_seed,
                'provider_payload_seed': actual_seed,
                'seed_fallback_used': bool(
                    (res.metadata or {}).get(
                        'seed_fallback_used'
                    )
                ),
            }
            if charge and not getattr(charge, 'settled_at', None):
                pricing=CoinPricingService(); img=get_price('venice', (job.metadata_json or {}).get('final_generation_model') or job.model, image_resolution_tier(job.width,job.height)); actual=pricing.quote_usd(db,img.standard_rate_usd,{'feature':'image_generation','model':(job.metadata_json or {}).get('final_generation_model') or job.model})
                event=AiUsageEvent(user_id=job.user_id,feature='image_generation',provider='venice',model=(job.metadata_json or {}).get('final_generation_model') or job.model,input_tokens=0,output_tokens=0,status='success')
                db.add(event); db.flush(); billing.settle(db, charge=charge, actual_quote=actual, usage_event=event)
            logger.info("IMAGE_PROVIDER_COMPLETED job_id=%s user_id=%s chat_id=%s attempt_count=%s seed=%s", job.id, job.user_id, job.chat_id, job.attempt_count, job.seed)
            db.flush()
        logger.info("IMAGE_TELEGRAM_DELIVERY_STARTED job_id=%s user_id=%s chat_id=%s attempt_count=%s reused_artifact=%s", job.id, job.user_id, job.chat_id, job.attempt_count, reused)
        delivery_detection=detect_provider_error_screen(artifact.image_bytes or b'')
        if delivery_detection.is_error_screen:
            job.metadata_json={**(job.metadata_json or {}),'moderation_screen_detected':True,'moderation_screen_reason':delivery_detection.reason,'moderation_screen_confidence':delivery_detection.confidence}
            logger.warning('IMAGE_PROVIDER_ERROR_SCREEN_BLOCKED_AT_DELIVERY job_id=%s user_id=%s chat_id=%s reason=%s confidence=%s', job.id, job.user_id, job.chat_id, delivery_detection.reason, delivery_detection.confidence)
            raise ProviderPolicyScreenError('provider returned moderation screen image')
        if ((job.metadata_json or {}).get('visual_requirements') or {}).get('explicit_nudity_requested') and (job.metadata_json or {}).get('anatomy_qa_completed') is not True:
            job.metadata_json={**(job.metadata_json or {}),'anatomy_qa_blocked_delivery':True,'generated_image_quality_failures':[{'reason_codes':['anatomy_qa_not_completed']}]}
            logger.info('ADULT_ANATOMY_DELIVERY_BLOCKED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', job.user_id, job.id, job.request_chain_id, ((job.metadata_json or {}).get('visual_requirements') or {}).get('anatomical_profile'), None, ['anatomy_qa_not_completed'])
            raise SingleSubjectImageQualityError('explicit anatomy QA not completed before delivery')
        if not metadata_has_valid_generated_image_qa(job.metadata_json, artifact.image_bytes or b''):
            logger.info('ADULT_ANATOMY_DELIVERY_BLOCKED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', job.user_id, job.id, job.request_chain_id, ((job.metadata_json or {}).get('visual_requirements') or {}).get('anatomical_profile'), ((job.metadata_json or {}).get('adult_anatomy_qa') or {}).get('confidence'), ((job.metadata_json or {}).get('adult_anatomy_qa') or {}).get('reason_codes'))
            logger.error('IMAGE_SINGLE_SUBJECT_BLOCKED_AT_DELIVERY job_id=%s user_id=%s chat_id=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, hashlib.sha256(artifact.image_bytes or b'').hexdigest()[:12])
            raise SingleSubjectImageQualityError('single-subject QA checksum missing or mismatched')
        delivery=await telegram_service.send_photo_bytes(job.chat_id, artifact.image_bytes or b'', filename='moones-image.jpg', mime_type=artifact.mime_type, caption='اینم عکسی که خواستی 🤍', reply_markup={'inline_keyboard':[[{'text':'👍 خوب بود','callback_data':f'imgfb:{job.id}:positive'},{'text':'👎 خوب نبود','callback_data':f'imgfb:{job.id}:negative'}]]})
        mid=getattr(delivery, 'message_id', delivery)
        if not isinstance(mid,int) or mid <= 0:
            raise RuntimeError('telegram_delivery_missing_message_id')
        job.telegram_message_id=mid
        job.delivery_message_id=mid
        if artifact.image_bytes and not job.thumbnail_bytes:
            job.thumbnail_bytes, job.thumbnail_mime_type = _make_thumbnail(artifact.image_bytes, artifact.mime_type)
        job.status='sent'; job.sent_at=datetime.utcnow(); job.lock_expires_at=None; job.error_code=None; job.error_message=None
        sync_image_request_chain_state(job, ImageRequestState.DELIVERED)
        await GeneratedMediaArchiveService().archive_image(db, job)
        if job.archive_status in ('sent','disabled','skipped'): artifact.image_bytes=None; artifact.cleared_at=datetime.utcnow()
        record_media_delivery(db, user_id=job.user_id, media_type='image', request_summary=job.user_request or '', generated_summary=(job.metadata_json or {}).get('context_summary', '') or job.prompt or '', telegram_message_id=mid)
        if 'memory_items' in inspect(db.bind).get_table_names():
            meta=job.metadata_json or {}; vs=meta.get('visual_state') or {}
            if any(vs.get(k) for k in ['environment_type','location','activity','pose']):
                content=__import__('json').dumps({**vs,'source_job_id':job.id,'updated_at':datetime.utcnow().isoformat()}, ensure_ascii=False)
                old_state=db.scalar(select(MemoryItem).where(MemoryItem.user_id==job.user_id, MemoryItem.type=='visual_scene_state').order_by(MemoryItem.created_at.desc()).limit(1))
                if old_state:
                    old_state.content=content; old_state.created_at=datetime.utcnow(); old_state.importance_score=0.9
                else:
                    db.add(MemoryItem(user_id=job.user_id,type='visual_scene_state',content=content,importance_score=0.9))
                logger.info("IMAGE_MEMORY_PERSISTED job_id=%s user_id=%s", job.id, job.user_id)
        logger.info("IMAGE_DELIVERY_COMPLETED job_id=%s user_id=%s chat_id=%s telegram_message_id=%s attempt_count=%s reused_artifact=%s", job.id, job.user_id, job.chat_id, mid, job.attempt_count, reused)
        db.flush(); return job
    except Exception as exc:
        if isinstance(exc, SingleSubjectImageQualityError):
            final_codes=((job.metadata_json or {}).get('generated_image_quality_failures') or [{}])[-1].get('reason_codes') or ['image_qa_failure']
            job.status='failed'; job.error_code='image_quality_single_subject_failed'; job.error_message=str(exc)[:500]; job.metadata_json={**(job.metadata_json or {}),'final_generation_model':None,'final_qa_reason_codes':final_codes}
            sync_image_request_chain_state(job, ImageRequestState.FAILED)
            if telegram_service and hasattr(telegram_service, 'send_text'):
                await telegram_service.send_text(job.chat_id, qa_failure_user_message(final_codes))
            logger.info('IMAGE_FAILURE_CATEGORY user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s reason_codes=%s', job.user_id, job.image_action, job.source_image_job_id, (job.metadata_json or {}).get('continuity_mode'), (job.metadata_json or {}).get('continuity_seed_strategy'), job.prompt_engine_version, ['image_qa_failure'])
            if charge: billing.refund(db, charge=charge, error=job.error_message)
            artifact=db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id))
            if artifact:
                artifact.image_bytes=None; artifact.byte_size=0; artifact.checksum=''; artifact.cleared_at=datetime.utcnow()
            job.failed_at=datetime.utcnow(); job.lock_expires_at=None; sync_image_request_chain_state(job, ImageRequestState.FAILED); db.flush(); return job
        if isinstance(exc, ProviderPolicyScreenError):
            job.status = 'failed'
            job.error_code = 'provider_policy_block'
            job.error_message = 'provider returned moderation screen image'
            job.metadata_json={**(job.metadata_json or {}),'final_generation_model':None}
            logger.warning('IMAGE_PROVIDER_POLICY_BLOCK_FINAL job_id=%s user_id=%s chat_id=%s attempt_count=%s', job.id, job.user_id, job.chat_id, job.attempt_count)
            if telegram_service and hasattr(telegram_service, 'send_text'):
                await telegram_service.send_text(job.chat_id, 'نتونستم این عکس رو طبق قوانین ارائه‌دهنده بسازم. می‌تونی درخواستت رو کمی تغییر بدی و دوباره امتحان کنی.')
            logger.info('IMAGE_FAILURE_CATEGORY user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s reason_codes=%s', job.user_id, job.image_action, job.source_image_job_id, (job.metadata_json or {}).get('continuity_mode'), (job.metadata_json or {}).get('continuity_seed_strategy'), job.prompt_engine_version, ['provider_artifact_moderation_screen'])
            if charge: billing.refund(db, charge=charge, error=job.error_message)
            artifact=db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id))
            if artifact:
                artifact.image_bytes=None; artifact.byte_size=0; artifact.checksum=''; artifact.cleared_at=datetime.utcnow()
            job.failed_at=datetime.utcnow(); job.lock_expires_at=None; sync_image_request_chain_state(job, ImageRequestState.FAILED); db.flush(); return job
        if job.generated_at or (db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id, ImageGenerationArtifact.image_bytes.is_not(None))) is not None):
            logger.warning("IMAGE_TELEGRAM_DELIVERY_FAILED job_id=%s user_id=%s chat_id=%s attempt_count=%s error=%s", job.id, job.user_id, job.chat_id, job.attempt_count, str(exc)[:200])
            job.status='delivery_failed'; job.error_code='telegram_delivery'; job.error_message=str(exc)[:500]
        else:
            non_retryable = (
                isinstance(
                    exc,
                    ImageClientError,
                )
                and not getattr(
                    exc,
                    'retryable',
                    False,
                )
            )

            job.status = (
                'failed'
                if (
                    non_retryable
                    or job.attempt_count
                    >= job.max_attempts
                )
                else 'queued'
            )

            job.error_code = 'provider_failure'
            job.error_message = str(exc)[:500]
            logger.info('IMAGE_FAILURE_CATEGORY user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s reason_codes=%s', job.user_id, job.image_action, job.source_image_job_id, (job.metadata_json or {}).get('continuity_mode'), (job.metadata_json or {}).get('continuity_seed_strategy'), job.prompt_engine_version, ['provider_transport_failure'])
            if job.status=='failed' and charge: billing.refund(db, charge=charge, error=job.error_message)
        job.failed_at=datetime.utcnow(); job.lock_expires_at=None; sync_image_request_chain_state(job, ImageRequestState.FAILED if job.status in {'failed','delivery_failed'} else ImageRequestState.QUEUED); db.flush(); return job

def store_feedback(db: Session, *, user_id:int, job_id:int, rating:str) -> ImageGenerationFeedback:
    fb=db.scalar(select(ImageGenerationFeedback).where(ImageGenerationFeedback.user_id==user_id, ImageGenerationFeedback.job_id==job_id))
    if not fb:
        fb=ImageGenerationFeedback(user_id=user_id, job_id=job_id, rating=rating); db.add(fb)
    else: fb.rating=rating
    db.flush(); return fb

def cleanup_stale_artifacts(db: Session, *, older_than_hours:int=6) -> int:
    cutoff=datetime.utcnow()-timedelta(hours=older_than_hours)
    rows=db.execute(update(ImageGenerationArtifact).where(ImageGenerationArtifact.created_at<cutoff, ImageGenerationArtifact.image_bytes.is_not(None)).values(image_bytes=None, cleared_at=datetime.utcnow())).rowcount
    db.flush(); return int(rows or 0)
