from __future__ import annotations
import hashlib
import logging
from io import BytesIO
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select, update, inspect
from sqlalchemy.orm import Session
from app.llm.image_client import VeniceImageClient, ImageClientError, image_resolution_tier, DEFAULT_IMAGE_MODEL, DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_STEPS, DEFAULT_CFG_SCALE, DEFAULT_SEED, validate_image_dimensions
from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact, ImageGenerationFeedback
from app.models.user import User
from app.models.usage import AiUsageEvent
from app.services.addon_service import user_has_addon, user_addon_enabled, user_owns_addon, ADULT_IMAGE_GENERATION_UNLOCK
from app.services.coin_pricing_service import CoinPricingService
from app.services.generated_media_archive_service import GeneratedMediaArchiveService
from app.services.provider_pricing_registry import get_price
from app.services.usage_billing_service import UsageBillingService, InsufficientCoins, new_correlation_id
from app.services.image_prompt_engine import IMAGE_ADDON_KEY, build_image_prompt, ensure_visual_profile, adult_requested, resolve_visual_scene_state, plan_composition
from app.services.conversation_time_service import ConversationTimeService
from app.services.partner_routine_service import PartnerRoutineService
from app.models.message import Message
from app.models.memory import MemoryItem
from app.services.media_continuity_service import record_media_delivery
from app.models.relationship import Relationship

logger=logging.getLogger(__name__)

class ImageGenerationDenied(Exception): pass


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

def image_generation_quote(db: Session):
    pricing=CoinPricingService(); img=get_price('venice', DEFAULT_IMAGE_MODEL, image_resolution_tier(DEFAULT_WIDTH, DEFAULT_HEIGHT))
    prompt=pricing.quote_tokens(db, provider='venice', model='qwen-3-6-plus', feature='chat', input_tokens=1500, output_tokens=500)
    image=pricing.quote_usd(db, img.standard_rate_usd, {'feature':'image_generation','model':DEFAULT_IMAGE_MODEL,'resolution':'1024x1280','tier':image_resolution_tier(DEFAULT_WIDTH,DEFAULT_HEIGHT)})
    return pricing.quote_usd(db, prompt.provider_cost_usd + image.provider_cost_usd, {'bundle':['image_prompt','image_generation'], 'image': image.pricing_snapshot, 'prompt': prompt.pricing_snapshot})

def enqueue_image_request(db: Session, *, user: User, chat_id:int, source_telegram_message_id:int, user_request:str) -> ImageGenerationJob:
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
    visual_state = resolve_visual_scene_state(user_request, recent_conversation)
    if any([visual_state.scene, visual_state.pose, visual_state.activity, visual_state.mood, visual_state.daypart]) and 'memory_items' in inspect(db.bind).get_table_names():
        import json
        content=json.dumps({'scene':visual_state.scene,'location':visual_state.location,'pose':visual_state.pose,'activity':visual_state.activity,'mood':visual_state.mood,'daypart':visual_state.daypart,'source_message_id':visual_state.source_message_id,'updated_at':datetime.utcnow().isoformat()}, ensure_ascii=False)
        old_state=db.scalar(select(MemoryItem).where(MemoryItem.user_id==user.id, MemoryItem.type=='visual_scene_state').order_by(MemoryItem.created_at.desc()).limit(1))
        if old_state: old_state.content=content; old_state.created_at=datetime.utcnow(); old_state.importance_score=0.9
        else: db.add(MemoryItem(user_id=user.id,type='visual_scene_state',content=content,importance_score=0.9))
    variation=int(hashlib.sha256(f'{profile.base_seed}:{source_telegram_message_id}:{user_request}'.encode()).hexdigest()[:8],16) % 2147483647
    selected_seed=(int(profile.base_seed) ^ variation) % 2147483647
    comp=plan_composition(visual_state)
    job=ImageGenerationJob(idempotency_key=idem,correlation_id=correlation,user_id=user.id,chat_id=chat_id,source_telegram_message_id=source_telegram_message_id,content_mode=result.content_mode,user_request=user_request,prompt=result.prompt,negative_prompt=result.negative_prompt,prompt_engine_version=result.prompt_engine_version,visual_profile_version=profile.version,usage_charge_id=charge.id,metadata_json={**snapshot,'adult_intent_detected':adult_intent,'adult_entitlement_owned':adult_owned,'adult_addon_enabled':adult_enabled,'adult_gate_result':('allow' if result.safety_decision=='allow' else (result.safety_reason or 'blocked')),'context_summary':result.input_context_summary,'influenced_by_job_ids':result.influenced_by_job_ids,'orientation':result.orientation,'composition_key':comp.composition_key,'requested_close_framing':comp.requested_close_framing,'subject_frame_share':comp.subject_frame_share,'camera_distance':comp.camera_distance,'required_environment_objects':comp.required_environment_objects,'environment_type':visual_state.environment_type,'activity':visual_state.activity,'objects':visual_state.held_objects,'extraction_source':visual_state.source_role,'visual_state':{'environment_type':visual_state.environment_type,'location':visual_state.location,'activity':visual_state.activity,'subject_action':visual_state.subject_action,'held_objects':visual_state.held_objects,'pose':visual_state.pose,'source_message':visual_state.source_message}},model=DEFAULT_IMAGE_MODEL,width=width,height=height,steps=DEFAULT_STEPS,cfg_scale=DEFAULT_CFG_SCALE,seed=selected_seed)
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
        artifact=db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id))
        reused=bool(artifact and artifact.image_bytes)
        if not reused:
            logger.info("IMAGE_GENERATION_STARTED job_id=%s user_id=%s chat_id=%s attempt_count=%s", job.id, job.user_id, job.chat_id, job.attempt_count)
            job.started_at=datetime.utcnow(); client=image_client or VeniceImageClient();
            try:
                res=await client.generate(job.prompt or '', job.negative_prompt or '', width=job.width, height=job.height, seed=job.seed)
            except TypeError:
                res=await client.generate(job.prompt or '', job.negative_prompt or '')
            if not artifact:
                artifact=ImageGenerationArtifact(job_id=job.id,mime_type=res.mime_type,checksum='',byte_size=0,image_bytes=None); db.add(artifact)
            artifact.mime_type=res.mime_type; artifact.checksum=hashlib.sha256(res.image_bytes).hexdigest(); artifact.byte_size=len(res.image_bytes); artifact.image_bytes=res.image_bytes; artifact.cleared_at=None
            actual_seed = int((res.metadata or {}).get('seed_used', job.seed)); job.generated_at=datetime.utcnow(); job.provider_request_id=res.request_id; job.metadata_json={**(job.metadata_json or {}),'provider_latency':res.latency_seconds,'response_type':res.response_type,'actual_width':res.width,'actual_height':res.height,'seed_used':actual_seed,'seed_fallback_used':bool((res.metadata or {}).get('seed_fallback_used'))}
            if charge and not getattr(charge, 'settled_at', None):
                pricing=CoinPricingService(); img=get_price('venice', job.model, image_resolution_tier(job.width,job.height)); actual=pricing.quote_usd(db,img.standard_rate_usd,{'feature':'image_generation','model':job.model})
                event=AiUsageEvent(user_id=job.user_id,feature='image_generation',provider='venice',model=job.model,input_tokens=0,output_tokens=0,status='success')
                db.add(event); db.flush(); billing.settle(db, charge=charge, actual_quote=actual, usage_event=event)
            logger.info("IMAGE_GENERATION_COMPLETED job_id=%s user_id=%s chat_id=%s attempt_count=%s", job.id, job.user_id, job.chat_id, job.attempt_count)
            db.flush()
        logger.info("IMAGE_TELEGRAM_DELIVERY_STARTED job_id=%s user_id=%s chat_id=%s attempt_count=%s reused_artifact=%s", job.id, job.user_id, job.chat_id, job.attempt_count, reused)
        delivery=await telegram_service.send_photo_bytes(job.chat_id, artifact.image_bytes or b'', filename='moones-image.jpg', mime_type=artifact.mime_type, caption='اینم عکسی که خواستی 🤍', reply_markup={'inline_keyboard':[[{'text':'👍 خوب بود','callback_data':f'imgfb:{job.id}:positive'},{'text':'👎 خوب نبود','callback_data':f'imgfb:{job.id}:negative'}]]})
        mid=getattr(delivery, 'message_id', delivery)
        if not isinstance(mid,int) or mid <= 0:
            raise RuntimeError('telegram_delivery_missing_message_id')
        job.telegram_message_id=mid
        if artifact.image_bytes and not job.thumbnail_bytes:
            job.thumbnail_bytes, job.thumbnail_mime_type = _make_thumbnail(artifact.image_bytes, artifact.mime_type)
        job.status='sent'; job.sent_at=datetime.utcnow(); job.lock_expires_at=None; job.error_code=None; job.error_message=None
        await GeneratedMediaArchiveService().archive_image(db, job)
        if job.archive_status in ('sent','disabled','skipped'): artifact.image_bytes=None; artifact.cleared_at=datetime.utcnow()
        record_media_delivery(db, user_id=job.user_id, media_type='image', request_summary=job.user_request or '', generated_summary=(job.metadata_json or {}).get('context_summary', '') or job.prompt or '', telegram_message_id=mid)
        logger.info("IMAGE_TELEGRAM_DELIVERY_SUCCEEDED job_id=%s user_id=%s chat_id=%s telegram_message_id=%s attempt_count=%s reused_artifact=%s", job.id, job.user_id, job.chat_id, mid, job.attempt_count, reused)
        db.flush(); return job
    except Exception as exc:
        if job.generated_at or (db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id, ImageGenerationArtifact.image_bytes.is_not(None))) is not None):
            logger.warning("IMAGE_TELEGRAM_DELIVERY_FAILED job_id=%s user_id=%s chat_id=%s attempt_count=%s error=%s", job.id, job.user_id, job.chat_id, job.attempt_count, str(exc)[:200])
            job.status='delivery_failed'; job.error_code='telegram_delivery'; job.error_message=str(exc)[:500]
        else:
            non_retryable = isinstance(exc, ImageClientError) and not getattr(exc, 'retryable', False); job.status='failed' if non_retryable or job.attempt_count>=job.max_attempts else 'queued'; job.error_code='provider_failure'; job.error_message=str(exc)[:500]
            if job.status=='failed' and charge: billing.refund(db, charge=charge, error=job.error_message)
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
