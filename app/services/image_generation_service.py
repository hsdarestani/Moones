from __future__ import annotations
import hashlib
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.llm.image_client import VeniceImageClient, image_resolution_tier, DEFAULT_IMAGE_MODEL, DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_STEPS, DEFAULT_CFG_SCALE, DEFAULT_SEED
from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact, ImageGenerationFeedback
from app.models.user import User
from app.models.usage import AiUsageEvent
from app.services.addon_service import user_has_addon
from app.services.coin_pricing_service import CoinPricingService
from app.services.generated_media_archive_service import GeneratedMediaArchiveService
from app.services.provider_pricing_registry import get_price
from app.services.usage_billing_service import UsageBillingService, InsufficientCoins, new_correlation_id
from app.services.image_prompt_engine import IMAGE_ADDON_KEY, build_image_prompt, ensure_visual_profile, adult_requested

logger=logging.getLogger(__name__)

class ImageGenerationDenied(Exception): pass

def image_generation_quote(db: Session):
    pricing=CoinPricingService(); img=get_price('venice', DEFAULT_IMAGE_MODEL, image_resolution_tier(DEFAULT_WIDTH, DEFAULT_HEIGHT))
    prompt=pricing.quote_tokens(db, provider='venice', model='qwen-3-6-plus', feature='chat', input_tokens=1500, output_tokens=500)
    image=pricing.quote_usd(db, img.standard_rate_usd, {'feature':'image_generation','model':DEFAULT_IMAGE_MODEL,'resolution':'1024x1280','tier':image_resolution_tier(DEFAULT_WIDTH,DEFAULT_HEIGHT)})
    return pricing.quote_usd(db, prompt.provider_cost_usd + image.provider_cost_usd, {'bundle':['image_prompt','image_generation'], 'image': image.pricing_snapshot, 'prompt': prompt.pricing_snapshot})

def enqueue_image_request(db: Session, *, user: User, chat_id:int, source_telegram_message_id:int, user_request:str) -> ImageGenerationJob:
    if not user_has_addon(db, user.id, IMAGE_ADDON_KEY): raise ImageGenerationDenied('addon_required')
    profile=ensure_visual_profile(db,user)
    result=build_image_prompt(db,user=user,user_request=user_request,visual_profile=profile,adult_mode_requested=adult_requested(user_request))
    if result.safety_decision!='allow': raise ImageGenerationDenied(result.safety_reason or 'blocked')
    idem=f'tg:image:{user.telegram_id}:{source_telegram_message_id}'
    existing=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.idempotency_key==idem))
    if existing: return existing
    correlation=new_correlation_id('image')
    quote=image_generation_quote(db)
    charge=UsageBillingService().reserve(db,user=user,idempotency_key=idem,feature='image_generation_bundle',provider='venice',model=DEFAULT_IMAGE_MODEL,quote=quote,correlation_id=correlation,metadata={'label_fa':'ساخت تصویر مونس'})
    job=ImageGenerationJob(idempotency_key=idem,correlation_id=correlation,user_id=user.id,chat_id=chat_id,source_telegram_message_id=source_telegram_message_id,content_mode=result.content_mode,user_request=user_request,prompt=result.prompt,negative_prompt=result.negative_prompt,prompt_engine_version=result.prompt_engine_version,visual_profile_version=profile.version,usage_charge_id=charge.id,metadata_json={'context_summary':result.input_context_summary,'influenced_by_job_ids':result.influenced_by_job_ids},model=DEFAULT_IMAGE_MODEL,width=DEFAULT_WIDTH,height=DEFAULT_HEIGHT,steps=DEFAULT_STEPS,cfg_scale=DEFAULT_CFG_SCALE,seed=DEFAULT_SEED)
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
            job.started_at=datetime.utcnow(); client=image_client or VeniceImageClient(); res=await client.generate(job.prompt or '', job.negative_prompt or '')
            if not artifact:
                artifact=ImageGenerationArtifact(job_id=job.id,mime_type=res.mime_type,checksum='',byte_size=0,image_bytes=None); db.add(artifact)
            artifact.mime_type=res.mime_type; artifact.checksum=hashlib.sha256(res.image_bytes).hexdigest(); artifact.byte_size=len(res.image_bytes); artifact.image_bytes=res.image_bytes; artifact.cleared_at=None
            job.generated_at=datetime.utcnow(); job.provider_request_id=res.request_id; job.metadata_json={**(job.metadata_json or {}),'provider_latency':res.latency_seconds,'response_type':res.response_type}
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
        if artifact.image_bytes and not job.thumbnail_bytes: job.thumbnail_bytes=artifact.image_bytes[:8192]; job.thumbnail_mime_type=artifact.mime_type
        job.status='sent'; job.sent_at=datetime.utcnow(); job.lock_expires_at=None; job.error_code=None; job.error_message=None
        await GeneratedMediaArchiveService().archive_image(db, job)
        if job.archive_status == 'sent': artifact.image_bytes=None; artifact.cleared_at=datetime.utcnow()
        logger.info("IMAGE_TELEGRAM_DELIVERY_SUCCEEDED job_id=%s user_id=%s chat_id=%s telegram_message_id=%s attempt_count=%s reused_artifact=%s", job.id, job.user_id, job.chat_id, mid, job.attempt_count, reused)
        db.flush(); return job
    except Exception as exc:
        if job.generated_at or (db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id, ImageGenerationArtifact.image_bytes.is_not(None))) is not None):
            logger.warning("IMAGE_TELEGRAM_DELIVERY_FAILED job_id=%s user_id=%s chat_id=%s attempt_count=%s error=%s", job.id, job.user_id, job.chat_id, job.attempt_count, str(exc)[:200])
            job.status='delivery_failed'; job.error_code='telegram_delivery'; job.error_message=str(exc)[:500]
        else:
            job.status='failed' if job.attempt_count>=job.max_attempts else 'queued'; job.error_code='provider_failure'; job.error_message=str(exc)[:500]
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
