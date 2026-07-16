from __future__ import annotations
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.image_generation import ImageGenerationJob
from app.services import image_pipeline_v2 as v2
from app.services.semantic_image_intent_router import (
    ConversationTurnSummary, RecentImageJobSummary, RecentResolvedImagePlanSummary,
    ReplyToMessageMetadata, SemanticImageRouterContext,
)


def _compact(text: str | None, limit: int = 180) -> str:
    text=(text or '').replace('\n',' ').strip()
    return text[:limit]


def build_semantic_image_router_context(db: Session, *, user_id: int, chat_id: int, current_text: str, telegram_message_id: int | None = None, reply_to_message=None, legacy_route_decision=None) -> SemanticImageRouterContext:
    turns=[]
    rows=db.scalars(select(Message).where(Message.user_id==user_id, Message.role.in_(['user','assistant'])).order_by(Message.created_at.desc()).limit(10)).all()
    for m in reversed(rows[-10:]):
        turns.append(ConversationTurnSummary(role=m.role, text_summary=_compact(m.content), message_id=getattr(m,'telegram_message_id',None), created_at=m.created_at.isoformat() if getattr(m,'created_at',None) else None))
    reply_meta=None
    if reply_to_message is not None:
        reply_meta=ReplyToMessageMetadata(message_id=getattr(reply_to_message,'message_id',None), role=None, media_kind='photo' if getattr(reply_to_message,'photo',None) else None, text_summary=_compact(getattr(reply_to_message,'text',None) or getattr(reply_to_message,'caption',None)))
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
        recent_summary=RecentImageJobSummary(job_id=recent.id, status=recent.status, action=getattr(recent,'image_action',None), sent_at=recent.sent_at.isoformat() if recent.sent_at else None, has_retrievable_artifact=retrievable, compact_user_visible_summary=compact)
    return SemanticImageRouterContext(current_user_message=current_text, recent_conversation=turns, reply_to_message=reply_meta, recent_image_job=recent_summary, recent_resolved_image_plan=plan_summary, recent_retrievable_image_exists=retrievable, seconds_since_recent_image=seconds, legacy_route_decision=legacy_route_decision)
