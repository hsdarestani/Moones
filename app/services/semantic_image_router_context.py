from __future__ import annotations
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact
from app.services import image_pipeline_v2 as v2
from app.services.semantic_image_intent_router import (
    ConversationTurnSummary, RecentImageJobSummary, RecentResolvedImagePlanSummary,
    ReplyToMessageMetadata, SemanticImageRouterContext,
)


def _compact(text: str | None, limit: int = 180) -> str:
    text=(text or '').replace('\n',' ').strip()
    return text[:limit]


ACTIVE_IMAGE_JOB_STATUSES = {"queued", "processing", "generating", "sending", "delivery_failed"}

def _job_summary(db: Session, job: ImageGenerationJob | None, *, compact_user_visible_summary: str | None = None) -> RecentImageJobSummary | None:
    if not job:
        return None
    artifact = db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id == job.id).limit(1))
    return RecentImageJobSummary(job_id=job.id, status=job.status, action=getattr(job, "image_action", None), created_at=job.created_at.isoformat() if getattr(job, "created_at", None) else None, started_at=job.started_at.isoformat() if getattr(job, "started_at", None) else None, sent_at=job.sent_at.isoformat() if getattr(job, "sent_at", None) else None, failed_at=job.failed_at.isoformat() if getattr(job, "failed_at", None) else None, error_code=getattr(job, "error_code", None), request_chain_id=getattr(job, "request_chain_id", None) or (job.metadata_json or {}).get("request_chain_id"), has_retrievable_artifact=bool(artifact and artifact.image_bytes), compact_user_visible_summary=compact_user_visible_summary)

def build_semantic_image_router_context(db: Session, *, user_id: int, chat_id: int, current_text: str, telegram_message_id: int | None = None, reply_to_message=None, legacy_route_decision=None) -> SemanticImageRouterContext:
    turns=[]
    rows=db.scalars(select(Message).where(Message.user_id==user_id, Message.role.in_(['user','assistant'])).order_by(Message.created_at.desc()).limit(10)).all()
    for m in reversed(rows[-10:]):
        turns.append(ConversationTurnSummary(role=m.role, text_summary=_compact(m.content), message_id=getattr(m,'telegram_message_id',None), created_at=m.created_at.isoformat() if getattr(m,'created_at',None) else None))
    reply_meta=None
    if reply_to_message is not None:
        reply_meta=ReplyToMessageMetadata(message_id=getattr(reply_to_message,'message_id',None), role=None, media_kind='photo' if getattr(reply_to_message,'photo',None) else None, text_summary=_compact(getattr(reply_to_message,'text',None) or getattr(reply_to_message,'caption',None)))
    active=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status.in_(ACTIVE_IMAGE_JOB_STATUSES)).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(1))
    latest=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(1))
    recent=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status=='sent').order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))
    recent_summary=None; plan_summary=None; retrievable=False; seconds=None
    if recent:
        retrievable=v2.source_job_is_retrievable(recent, user_id=user_id, chat_id=chat_id)
        if recent.sent_at: seconds=max(0, int((datetime.utcnow()-recent.sent_at).total_seconds()))
        plan=v2.deserialize_resolved_plan(getattr(recent,'resolved_plan_json',None) or ((recent.metadata_json or {}).get('resolved_plan') if recent.metadata_json else None))
        compact = None
        if plan:
            compact=f"action={plan.action}; scene={getattr(plan.scene,'value',None)}; pose={getattr(plan.pose,'value',None)}; objects={getattr(plan.required_objects,'value',[])}"
            plan_summary=RecentResolvedImagePlanSummary(job_id=recent.id, action=plan.action, scene=getattr(plan.scene,'value',None), location=getattr(plan.location,'value',None), pose=getattr(plan.pose,'value',None), visible_fields=['scene','pose','required_objects'], invariant_codes=(plan.validation_results or {}).get('errors',[]))
        recent_summary=_job_summary(db, recent, compact_user_visible_summary=compact)
    active_summary=_job_summary(db, active)
    latest_summary=_job_summary(db, latest)
    if active_summary:
        import logging; logging.getLogger(__name__).info("IMAGE_ACTIVE_JOB_CONTEXT_ATTACHED user_id=%s job_id=%s request_chain_id=%s action=%s job_status=%s", user_id, active_summary.job_id, active_summary.request_chain_id, active_summary.action, active_summary.status)
    return SemanticImageRouterContext(current_user_message=current_text, recent_conversation=turns, reply_to_message=reply_meta, active_image_job=active_summary, latest_image_job=latest_summary, recent_image_job=recent_summary, recent_resolved_image_plan=plan_summary, recent_retrievable_image_exists=retrievable, seconds_since_recent_image=seconds, legacy_route_decision=legacy_route_decision)
