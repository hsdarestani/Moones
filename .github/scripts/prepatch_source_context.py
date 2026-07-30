from pathlib import Path
import re

v2_path=Path('app/services/image_pipeline_v2.py')
v2=v2_path.read_text()
replacement='''def source_job_is_context_eligible(job: ImageGenerationJob, *, user_id:int, chat_id:int, ttl_minutes:int=360) -> bool:
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
pattern=r"def source_job_is_retrievable\(.*?\n(?=def _restore_dataclass)"
updated,count=re.subn(pattern,replacement,v2,flags=re.S)
if count != 1:
    raise RuntimeError(f'source function prepatch count={count}')
v2_path.write_text(updated)

patcher_path=Path('.github/scripts/patch_image_acceptance_regressions.py')
patcher=patcher_path.read_text()
old='v2 = replace_once(v2, old_sources, new_sources, "source context retention")'
new='''if "def source_job_is_context_eligible" not in v2:
    v2 = replace_once(v2, old_sources, new_sources, "source context retention")'''
if old not in patcher:
    raise RuntimeError('patcher source replacement line missing')
patcher_path.write_text(patcher.replace(old,new,1))
