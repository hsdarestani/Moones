from __future__ import annotations
import hashlib
import re
import logging
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
from app.models.relationship import Relationship

logger=logging.getLogger(__name__)

class ImageGenerationDenied(Exception): pass

def deterministic_provider_seed(*parts: object) -> int:
    digest=int(hashlib.sha256(':'.join(str(p) for p in parts).encode()).hexdigest(),16)
    return VENICE_SEED_MIN + (digest % VENICE_SEED_MAX)

def _variation_requested(text: str, meta: dict | None = None) -> bool:
    t=text or ''; m=meta or {}
    return bool(m.get('contextual_followup') or m.get('route_type') in {'image_followup','image_refinement'} or re.search(r'یکی دیگه|یه دونه دیگه|variation|واریاسیون|مثل قبلی|این بار', t))


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


def _pipeline_v2_enabled(db: Session) -> tuple[bool, bool]:
    try:
        from app.services.settings_service import SettingsService
        svc = SettingsService()
        return (
            svc.get_bool(db, 'image_generation.pipeline_v2_enabled', False),
            svc.get_bool(db, 'image_generation.pipeline_v2_shadow_mode', False),
        )
    except Exception:
        return False, False

def _enqueue_image_request_v2(db: Session, *, user: User, chat_id:int, source_telegram_message_id:int, user_request:str, route_decision=None) -> ImageGenerationJob:
    from app.services import image_pipeline_v2 as v2
    if not user_has_addon(db, user.id, IMAGE_ADDON_KEY) or not user_addon_enabled(db, user.id, IMAGE_ADDON_KEY):
        raise ImageGenerationDenied('addon_required')
    idem_action = getattr(route_decision, 'route', None) or 'image'
    idem=f'tg:image:v2:{user.telegram_id}:{chat_id}:{source_telegram_message_id}:{idem_action}'
    existing=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.idempotency_key==idem))
    if existing: return existing
    logger.info('IMAGE_REQUEST_PERSISTED user_id=%s chat_id=%s source_message_id=%s', user.id, chat_id, source_telegram_message_id)
    norm=v2.normalize_request_v2(user_request, user_id=user.id, chat_id=chat_id, source_message_id=source_telegram_message_id)
    logger.info('IMAGE_REQUEST_NORMALIZED user_id=%s chat_id=%s', user.id, chat_id)
    intent=v2.parse_image_intent(norm)
    if route_decision is not None and getattr(route_decision, 'route', 'chat') != 'chat' and intent.continuity.action == v2.ImageAction.CHAT:
        intent.is_image_request=True; intent.continuity.action=v2.ImageAction.NEW_GENERATION
    source_job=v2.find_eligible_source_image_context(db, user_id=user.id, chat_id=chat_id) if intent.continuity.action in {v2.ImageAction.RESEND_EXACT, v2.ImageAction.VARIATION, v2.ImageAction.REFINEMENT} else None
    if source_job: intent.continuity.source_image_job_id=source_job.id
    logger.info('IMAGE_SOURCE_CONTEXT_SELECTED user_id=%s chat_id=%s source_job_id=%s action=%s', user.id, chat_id, getattr(source_job,'id',None), intent.continuity.action)
    if intent.continuity.action == v2.ImageAction.RESEND_EXACT and source_job:
        job=ImageGenerationJob(idempotency_key=idem, correlation_id=new_correlation_id('image-resend'), user_id=user.id, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id, status='queued', content_mode='resend', user_request=user_request, prompt_engine_version=v2.PROMPT_ENGINE_VERSION, plan_version=v2.PLAN_VERSION, source_image_job_id=source_job.id, image_action=v2.ImageAction.RESEND_EXACT, usage_charge_id=None, seed=source_job.seed, final_provider_seed=None, policy_reason_code='resend_exact', metadata_json={'billing_action':'none','source_image_job_id':source_job.id,'route_action':v2.ImageAction.RESEND_EXACT})
        db.add(job); db.flush(); logger.info('IMAGE_RESEND_EXECUTED user_id=%s chat_id=%s job_id=%s source_job_id=%s', user.id, chat_id, job.id, source_job.id); return job
    profile=v2.ensure_visual_profile_v2(db, user, ensure_visual_profile(db,user))
    previous=v2.deserialize_resolved_plan((source_job.resolved_plan_json if source_job else None) or ((source_job.metadata_json or {}).get('resolved_plan') if source_job else None))
    merged=v2.merge_image_intent(intent, previous)
    logger.info('IMAGE_PLAN_MERGED user_id=%s chat_id=%s action=%s', user.id, chat_id, intent.continuity.action)
    safety=v2.evaluate_safety_policy(intent)
    logger.info('IMAGE_POLICY_DECIDED user_id=%s chat_id=%s policy_reason=%s decision=%s', user.id, chat_id, safety.reason_code, safety.decision)
    if safety.decision != v2.PolicyDecision.ALLOW:
        raise ImageGenerationDenied(safety.reason_code or 'blocked')
    plan=v2.construct_resolved_plan(intent, merged, safety, profile, source_job=source_job, message_id=source_telegram_message_id, user_request=user_request)
    errors=v2.validate_plan_invariants(plan, source_job=source_job, user_id=user.id, chat_id=chat_id)
    logger.info('IMAGE_PLAN_VALIDATED user_id=%s chat_id=%s invariant_codes=%s', user.id, chat_id, errors)
    if errors: raise ImageGenerationDenied('plan_invariant_failed:' + ','.join(errors))
    compiled=v2.compile_image_prompt(plan)
    logger.info('IMAGE_PROMPT_COMPILED user_id=%s chat_id=%s seed=%s', user.id, chat_id, compiled.provider_parameters.get('seed'))
    prompt_errors=v2.validate_compiled_prompt(plan, compiled)
    logger.info('IMAGE_PROMPT_VALIDATED user_id=%s chat_id=%s invariant_codes=%s', user.id, chat_id, prompt_errors)
    if prompt_errors: raise ImageGenerationDenied('prompt_invariant_failed:' + ','.join(prompt_errors))
    quote=image_generation_quote(db); correlation=new_correlation_id('image')
    logger.info('IMAGE_BILLING_DECIDED user_id=%s chat_id=%s action=%s billable=true', user.id, chat_id, plan.action)
    charge=UsageBillingService().reserve(db,user=user,idempotency_key=idem,feature='image_generation_bundle',provider='venice',model=DEFAULT_IMAGE_MODEL,quote=quote,correlation_id=correlation,metadata={'label_fa':'ساخت تصویر مونس','image_action':plan.action})
    seed=int(plan.seed_strategy['final_provider_seed'])
    job=ImageGenerationJob(idempotency_key=idem, correlation_id=correlation, user_id=user.id, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id, content_mode='adult' if plan.body_visibility else 'normal', user_request=user_request, prompt=compiled.positive_prompt, negative_prompt=compiled.negative_prompt, prompt_engine_version=v2.PROMPT_ENGINE_VERSION, visual_profile_version=profile.version, identity_fingerprint=plan.identity['identity_fingerprint'], usage_charge_id=charge.id, resolved_plan_json=v2.plan_to_json(plan), plan_version=v2.PLAN_VERSION, source_image_job_id=getattr(source_job,'id',None), image_action=plan.action, identity_seed=plan.seed_strategy['identity_seed'], variation_index=plan.seed_strategy['variation_index'], final_provider_seed=seed, policy_reason_code=safety.reason_code, metadata_json={'seed_used':seed,'normalized_provider_seed':seed,'identity_fingerprint':plan.identity['identity_fingerprint'],'identity_descriptor':plan.identity['descriptor'],'provider_capabilities':v2.ProviderImageCapabilities().__dict__,'route_action':plan.action,'source_image_job_id':getattr(source_job,'id',None),'resolved_plan':v2.plan_to_json(plan),'billing_action':'reserve_generation','invariant_codes':[]}, model=DEFAULT_IMAGE_MODEL, width=compiled.provider_parameters['width'], height=compiled.provider_parameters['height'], steps=DEFAULT_STEPS, cfg_scale=DEFAULT_CFG_SCALE, seed=seed)
    db.add(job); db.flush(); logger.info('IMAGE_JOB_ENQUEUED user_id=%s chat_id=%s job_id=%s action=%s seed=%s', user.id, chat_id, job.id, plan.action, seed); return job

def image_generation_quote(db: Session):
    pricing=CoinPricingService(); img=get_price('venice', DEFAULT_IMAGE_MODEL, image_resolution_tier(DEFAULT_WIDTH, DEFAULT_HEIGHT))
    prompt=pricing.quote_tokens(db, provider='venice', model='qwen-3-6-plus', feature='chat', input_tokens=1500, output_tokens=500)
    image=pricing.quote_usd(db, img.standard_rate_usd, {'feature':'image_generation','model':DEFAULT_IMAGE_MODEL,'resolution':'1024x1280','tier':image_resolution_tier(DEFAULT_WIDTH,DEFAULT_HEIGHT)})
    return pricing.quote_usd(db, prompt.provider_cost_usd + image.provider_cost_usd, {'bundle':['image_prompt','image_generation'], 'image': image.pricing_snapshot, 'prompt': prompt.pricing_snapshot})

def enqueue_image_request(db: Session, *, user: User, chat_id:int, source_telegram_message_id:int, user_request:str, route_decision=None) -> ImageGenerationJob:
    v2_enabled, shadow = _pipeline_v2_enabled(db)
    if v2_enabled:
        return _enqueue_image_request_v2(db, user=user, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id, user_request=user_request, route_decision=route_decision)
    if shadow:
        try:
            _enqueue_image_request_v2(db, user=user, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id, user_request=user_request, route_decision=route_decision)
            db.rollback()
        except Exception:
            db.rollback()
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

async def process_job(db: Session, job: ImageGenerationJob, *, image_client=None, telegram_service=None) -> ImageGenerationJob:
    billing=UsageBillingService(); charge=db.get(__import__('app.models.billing', fromlist=['UsageCharge']).UsageCharge, job.usage_charge_id) if job.usage_charge_id else None
    if telegram_service is None:
        job.status='delivery_failed'; job.error_code='telegram_delivery'; job.error_message='telegram_service_required'; job.failed_at=datetime.utcnow(); job.lock_expires_at=None; db.flush()
        raise RuntimeError('telegram_service_required')
    try:
        if getattr(job, 'image_action', None) == 'resend_exact' and getattr(job, 'source_image_job_id', None):
            source_artifact=db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.source_image_job_id))
            if not source_artifact or not source_artifact.image_bytes:
                job.status='failed'; job.error_code='resend_artifact_unavailable'; job.error_message='source artifact unavailable for exact resend'; job.failed_at=datetime.utcnow(); job.lock_expires_at=None; db.flush(); return job
            logger.info('IMAGE_RESEND_EXECUTED job_id=%s source_job_id=%s user_id=%s chat_id=%s', job.id, job.source_image_job_id, job.user_id, job.chat_id)
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
            try:
                job.seed, norm_applied = normalize_venice_seed(job.seed, salt=f'job:{job.id}')
                job.metadata_json={**(job.metadata_json or {}),'normalized_provider_seed':job.seed,'seed_normalization_applied': bool((job.metadata_json or {}).get('seed_normalization_applied') or norm_applied),'seed_provider_min':VENICE_SEED_MIN,'seed_provider_max':VENICE_SEED_MAX}
                res=await client.generate(job.prompt or '', job.negative_prompt or '', width=job.width, height=job.height, seed=job.seed)
            except TypeError:
                res=await client.generate(job.prompt or '', job.negative_prompt or '')
            if not artifact:
                artifact=ImageGenerationArtifact(job_id=job.id,mime_type=res.mime_type,checksum='',byte_size=0,image_bytes=None); db.add(artifact)
            artifact.mime_type=res.mime_type; artifact.checksum=hashlib.sha256(res.image_bytes).hexdigest()
            if _variation_requested(job.user_request or '', job.metadata_json):
                duplicate=db.scalar(select(ImageGenerationArtifact).join(ImageGenerationJob).where(ImageGenerationJob.user_id==job.user_id, ImageGenerationJob.id!=job.id, ImageGenerationJob.status=='sent', ImageGenerationArtifact.checksum==artifact.checksum).order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))
                if duplicate:
                    old_seed=job.seed; job.seed=deterministic_provider_seed(job.seed, job.id, 'duplicate-variation-retry')
                    job.metadata_json={**(job.metadata_json or {}),'duplicate_checksum_detected':artifact.checksum,'duplicate_retry_applied':True,'duplicate_retry_previous_seed':old_seed,'normalized_provider_seed':job.seed,'seed_used':job.seed}
                    res=await client.generate(job.prompt or '', job.negative_prompt or '', width=job.width, height=job.height, seed=job.seed)
                    artifact.checksum=hashlib.sha256(res.image_bytes).hexdigest()
            artifact.byte_size=len(res.image_bytes); artifact.image_bytes=res.image_bytes; artifact.cleared_at=None
            response_meta = getattr(res, 'metadata', {}) or {}
            actual_seed = int(
                response_meta.get('seed_used', job.seed)
            )
            job.seed = actual_seed
            job.final_provider_seed = actual_seed
            job.generated_at = datetime.utcnow()
            job.provider_request_id = res.request_id
            job.metadata_json = {
                **(job.metadata_json or {}),
                'provider_latency': res.latency_seconds,
                'response_type': res.response_type,
                'actual_width': res.width,
                'actual_height': res.height,
                'seed_used': actual_seed,
                'provider_payload_seed': actual_seed,
                'seed_fallback_used': bool(
                    response_meta.get('seed_fallback_used', False)
                ),
            }
            if charge and not getattr(charge, 'settled_at', None):
                pricing=CoinPricingService(); img=get_price('venice', job.model, image_resolution_tier(job.width,job.height)); actual=pricing.quote_usd(db,img.standard_rate_usd,{'feature':'image_generation','model':job.model})
                event=AiUsageEvent(user_id=job.user_id,feature='image_generation',provider='venice',model=job.model,input_tokens=0,output_tokens=0,status='success')
                db.add(event); db.flush(); billing.settle(db, charge=charge, actual_quote=actual, usage_event=event)
            logger.info("IMAGE_PROVIDER_COMPLETED job_id=%s user_id=%s chat_id=%s attempt_count=%s seed=%s", job.id, job.user_id, job.chat_id, job.attempt_count, job.seed)
            db.flush()
        logger.info("IMAGE_TELEGRAM_DELIVERY_STARTED job_id=%s user_id=%s chat_id=%s attempt_count=%s reused_artifact=%s", job.id, job.user_id, job.chat_id, job.attempt_count, reused)
        delivery=await telegram_service.send_photo_bytes(job.chat_id, artifact.image_bytes or b'', filename='moones-image.jpg', mime_type=artifact.mime_type, caption='اینم عکسی که خواستی 🤍', reply_markup={'inline_keyboard':[[{'text':'👍 خوب بود','callback_data':f'imgfb:{job.id}:positive'},{'text':'👎 خوب نبود','callback_data':f'imgfb:{job.id}:negative'}]]})
        mid=getattr(delivery, 'message_id', delivery)
        if not isinstance(mid,int) or mid <= 0:
            raise RuntimeError('telegram_delivery_missing_message_id')
        job.telegram_message_id=mid
        job.delivery_message_id=mid
        if artifact.image_bytes and not job.thumbnail_bytes:
            job.thumbnail_bytes, job.thumbnail_mime_type = _make_thumbnail(artifact.image_bytes, artifact.mime_type)
        job.status='sent'; job.sent_at=datetime.utcnow(); job.lock_expires_at=None; job.error_code=None; job.error_message=None
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
        if job.generated_at or (db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id, ImageGenerationArtifact.image_bytes.is_not(None))) is not None):
            logger.warning("IMAGE_TELEGRAM_DELIVERY_FAILED job_id=%s user_id=%s chat_id=%s attempt_count=%s error=%s", job.id, job.user_id, job.chat_id, job.attempt_count, str(exc)[:200])
            job.status='delivery_failed'; job.error_code='telegram_delivery'; job.error_message=str(exc)[:500]
        else:
            non_retryable = (
                isinstance(exc, ImageClientError)
                and not getattr(exc, 'retryable', False)
            )
            job.status = (
                'failed'
                if non_retryable
                or job.attempt_count >= job.max_attempts
                else 'queued'
            )
            job.error_code = 'provider_failure'
            job.error_message = str(exc)[:500]
            if job.status == 'failed' and charge:
                billing.refund(
                    db,
                    charge=charge,
                    error=job.error_message,
                )
        job.failed_at=datetime.utcnow(); job.lock_expires_at=None; db.flush(); return job

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
