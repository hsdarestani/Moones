from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count=text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old,new,1)

svc_path=Path('app/services/image_generation_service.py')
svc=svc_path.read_text()
if 'import asyncio\n' not in svc[:200]:
    svc=replace_once(svc,'from __future__ import annotations\n','from __future__ import annotations\nimport asyncio\n','asyncio import')
svc=replace_once(
    svc,
    'from app.services.partner_photo_contract import attach_world_memory_context, build_partner_photo_contract\n',
    'from app.services.partner_photo_contract import attach_world_memory_context, build_partner_photo_contract, image_status_text\n',
    'status text import',
)
old_helper="""async def _safe_send_image_status(telegram_service, chat_id: int, text: str) -> None:
    if not telegram_service or not hasattr(telegram_service, 'send_text'):
        return
    try:
        await telegram_service.send_text(chat_id, text)
    except Exception:
        logger.exception('IMAGE_FAILURE_NOTICE_SEND_FAILED chat_id=%s', chat_id)
"""
new_helper="""async def _safe_send_image_status(telegram_service, chat_id: int, text: str) -> None:
    if not telegram_service or not hasattr(telegram_service, 'send_text'):
        return
    try:
        await asyncio.wait_for(telegram_service.send_text(chat_id, text), timeout=8)
    except Exception:
        logger.exception('IMAGE_FAILURE_NOTICE_SEND_FAILED chat_id=%s', chat_id)


def terminal_image_failure_text(job) -> str:
    return image_status_text(getattr(job, 'status', None), getattr(job, 'error_code', None)) or 'این بار عکس درست درنیومد؛ سکه‌ات برگشت 🤍'
"""
svc=replace_once(svc,old_helper,new_helper,'safe failure helper')
old_claim="""def claim_next_job(db: Session, *, lock_seconds:int=300) -> ImageGenerationJob|None:
    now=datetime.utcnow(); expires=now+timedelta(seconds=lock_seconds)
    stmt=select(ImageGenerationJob).where(ImageGenerationJob.status.in_(['queued','delivery_failed']), ImageGenerationJob.scheduled_at<=now, ((ImageGenerationJob.lock_expires_at==None) | (ImageGenerationJob.lock_expires_at<now))).order_by(ImageGenerationJob.scheduled_at).with_for_update(skip_locked=True).limit(1)
"""
new_claim="""def claim_next_job(db: Session, *, lock_seconds:int=300) -> ImageGenerationJob|None:
    now=datetime.utcnow(); expires=now+timedelta(seconds=lock_seconds)
    stale_rows=db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.status.in_(['processing','generating','sending']), ImageGenerationJob.lock_expires_at.is_not(None), ImageGenerationJob.lock_expires_at<now).with_for_update(skip_locked=True)).all()
    for stale in stale_rows:
        previous_status=stale.status
        stale.status='delivery_failed' if previous_status == 'sending' else 'queued'
        stale.locked_at=None; stale.lock_expires_at=None
        stale.metadata_json={**(stale.metadata_json or {}),'stale_worker_recovered_at':now.isoformat(),'stale_worker_previous_status':previous_status}
        logger.warning('IMAGE_STALE_JOB_RECOVERED job_id=%s user_id=%s previous_status=%s recovered_status=%s', stale.id, stale.user_id, previous_status, stale.status)
    stmt=select(ImageGenerationJob).where(ImageGenerationJob.status.in_(['queued','delivery_failed']), ImageGenerationJob.scheduled_at<=now, ((ImageGenerationJob.lock_expires_at==None) | (ImageGenerationJob.lock_expires_at<now))).order_by(ImageGenerationJob.scheduled_at).with_for_update(skip_locked=True).limit(1)
"""
svc=replace_once(svc,old_claim,new_claim,'stale claim recovery')
svc=replace_once(
    svc,
    """            if telegram_service and hasattr(telegram_service, 'send_text'):
                await telegram_service.send_text(job.chat_id, qa_failure_user_message(final_codes))
""",
    """            await _safe_send_image_status(telegram_service, job.chat_id, qa_failure_user_message(final_codes))
""",
    'safe QA failure notice',
)
svc=replace_once(
    svc,
    """            if telegram_service and hasattr(telegram_service, 'send_text'):
                await telegram_service.send_text(job.chat_id, 'نتونستم این عکس رو طبق قوانین ارائه‌دهنده بسازم. می‌تونی درخواستت رو کمی تغییر بدی و دوباره امتحان کنی.')
""",
    """            await _safe_send_image_status(telegram_service, job.chat_id, 'این عکس از سمت سرویس تصویر ساخته نشد؛ سکه‌ات برگشت. یه مدل دیگه بگو تا دوباره بگیرم 🤍')
""",
    'safe policy failure notice',
)
old_delivery="""        if job.generated_at or (db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id, ImageGenerationArtifact.image_bytes.is_not(None))) is not None):
            logger.warning("IMAGE_TELEGRAM_DELIVERY_FAILED job_id=%s user_id=%s chat_id=%s attempt_count=%s error=%s", job.id, job.user_id, job.chat_id, job.attempt_count, str(exc)[:200])
            job.status='delivery_failed'; job.error_code='telegram_delivery'; job.error_message=str(exc)[:500]
"""
new_delivery="""        if job.generated_at or (db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id, ImageGenerationArtifact.image_bytes.is_not(None))) is not None):
            logger.warning("IMAGE_TELEGRAM_DELIVERY_FAILED job_id=%s user_id=%s chat_id=%s attempt_count=%s error=%s", job.id, job.user_id, job.chat_id, job.attempt_count, str(exc)[:200])
            exhausted=job.attempt_count >= job.max_attempts
            job.status='failed' if exhausted else 'delivery_failed'
            job.error_code='telegram_delivery_exhausted' if exhausted else 'telegram_delivery'
            job.error_message=str(exc)[:500]
            if exhausted:
                if charge: billing.refund(db, charge=charge, error=job.error_message)
                await _safe_send_image_status(telegram_service, job.chat_id, terminal_image_failure_text(job))
"""
svc=replace_once(svc,old_delivery,new_delivery,'bounded delivery failure')
old_generic="""            if job.status=='failed':
                if qa_transient:
                    await _safe_send_image_status(telegram_service, job.chat_id, 'این یکی رو نتونستم مطمئن بررسی کنم؛ نفرستادمش و سکه‌ات برگشت 🤍')
                if charge: billing.refund(db, charge=charge, error=job.error_message)
"""
new_generic="""            if job.status=='failed':
                if charge: billing.refund(db, charge=charge, error=job.error_message)
                await _safe_send_image_status(telegram_service, job.chat_id, terminal_image_failure_text(job))
"""
svc=replace_once(svc,old_generic,new_generic,'generic terminal notification')
svc_path.write_text(svc)

router_path=Path('app/services/semantic_image_intent_router.py')
router=router_path.read_text()
router=replace_once(router,'if age_seconds <= 600:\n                target=latest','if age_seconds <= 7200:\n                target=latest','failed followup freshness')
router_path.write_text(router)

contract_path=Path('app/services/partner_photo_contract.py')
contract=contract_path.read_text()
contract=replace_once(
    contract,
    """        return "این بار عکس درست درنیومد؛ اگه سکه‌ای رزرو شده بود برگشته."
""",
    """        if error_code == "telegram_delivery_exhausted":
            return "عکس آماده شد ولی ارسالش چند بار گیر کرد؛ نفرستادمش و سکه‌ات برگشت 🤍"
        if error_code in {"provider_failure", "image_qa_transient"}:
            return "این بار سرویس عکس جواب نداد؛ سکه‌ات برگشت. دوباره بگو تا از نو بگیرم 🤍"
        return "این بار عکس درست درنیومد؛ سکه‌ات برگشت 🤍"
""",
    'natural failed status copy',
)
contract_path.write_text(contract)

test_path=Path('tests/test_image_job_terminal_visibility.py')
test_path.write_text('''import asyncio\nfrom datetime import datetime, timedelta\nfrom types import SimpleNamespace\n\n\ndef test_terminal_failure_text_is_natural_and_specific():\n    from app.services.image_generation_service import terminal_image_failure_text\n    assert "سکه" in terminal_image_failure_text(SimpleNamespace(status="failed", error_code="provider_failure"))\n    assert "سرویس" in terminal_image_failure_text(SimpleNamespace(status="failed", error_code="provider_failure"))\n\n\ndef test_safe_status_send_swallows_telegram_failure():\n    from app.services.image_generation_service import _safe_send_image_status\n    class Telegram:\n        async def send_text(self, *args, **kwargs):\n            raise RuntimeError("telegram down")\n    asyncio.run(_safe_send_image_status(Telegram(), 1, "x"))\n\n\ndef test_failed_job_status_copy_after_eighteen_minutes():\n    from app.services.partner_photo_contract import image_status_text\n    assert image_status_text("failed", "provider_failure")\n\n\ndef test_stale_processing_job_is_recovered(db_session, user):\n    from app.models.image_generation import ImageGenerationJob\n    from app.services.image_generation_service import claim_next_job\n    job=ImageGenerationJob(idempotency_key="stale-job", correlation_id="c", user_id=user.id, chat_id=99, source_telegram_message_id=77, status="processing", scheduled_at=datetime.utcnow()-timedelta(minutes=10), lock_expires_at=datetime.utcnow()-timedelta(seconds=1), user_request="عکس بده", prompt="p", negative_prompt="n", model="m", width=1024, height=1024, steps=1, cfg_scale=1, seed=1)\n    db_session.add(job); db_session.commit()\n    claimed=claim_next_job(db_session)\n    assert claimed.id == job.id\n    assert claimed.status == "processing"\n    assert (claimed.metadata_json or {}).get("stale_worker_recovered_at")\n''')
print('patch_image_job_terminal_visibility: ok')
