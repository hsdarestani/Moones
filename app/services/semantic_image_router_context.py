from __future__ import annotations
from datetime import datetime, timedelta
import json
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
FAILED_IMAGE_JOB_STATUSES = {"failed", "delivery_failed"}


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed=json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _resolved_value(value):
    return value.get('value') if isinstance(value, dict) and 'value' in value else value


def merge_failed_image_retry_contract(jobs):
    """Return cumulative request text and a VisualIntent-compatible dictionary."""
    merged={}
    requests=[]
    list_fields={"required_visible_environment_elements", "required_body_regions", "forbidden_body_regions", "freeform_visual_constraints"}
    for job in reversed(list(jobs or [])):
        request=str(getattr(job, 'user_request', '') or '').strip()
        if request and request not in requests:
            requests.append(request)
        metadata=_as_dict(getattr(job, 'metadata_json', None))
        plan=_as_dict(getattr(job, 'resolved_plan_json', None)) or _as_dict(metadata.get('resolved_plan'))
        current=_as_dict(plan.get('current_intent'))
        composition=_as_dict(plan.get('composition'))
        requirements=_as_dict(plan.get('visual_requirements')) or _as_dict(metadata.get('visual_requirements'))
        contract=_as_dict(requirements.get('photo_contract')) or _as_dict(composition.get('photo_contract')) or _as_dict(metadata.get('photo_contract'))
        body_visibility=_as_dict(plan.get('body_visibility')) or _as_dict(metadata.get('body_visibility'))
        classification=str(current.get('content_classification') or metadata.get('content_classification') or '').lower()
        nudity_level=None
        for candidate in ('full_nudity','topless','lingerie','suggestive','normal'):
            if candidate in classification:
                nudity_level=candidate
                break
        required_regions=list(requirements.get('required_body_regions') or contract.get('required_body_regions') or [])
        forbidden_regions=list(requirements.get('forbidden_body_regions') or contract.get('forbidden_body_regions') or [])
        for name, region in body_visibility.items():
            region=_as_dict(region)
            if region.get('visibility_requested') or region.get('framing_requested'):
                required_regions.append(name)
            if region.get('visibility_negated'):
                forbidden_regions.append(name)
        if requirements.get('full_body_visible'):
            required_regions.append('full_body')
        must=_as_dict(requirements.get('must_satisfy'))
        adult_intent=_as_dict(current.get('adult_intent'))
        values={
            'scene': _resolved_value(plan.get('scene')) or metadata.get('resolved_scene') or metadata.get('semantic_requested_scene'),
            'location': _resolved_value(plan.get('location')) or metadata.get('resolved_location') or metadata.get('semantic_requested_location'),
            'environment_type': _resolved_value(plan.get('environment_type')),
            'privacy': _resolved_value(plan.get('privacy')),
            'pose': _resolved_value(plan.get('pose')),
            'activity': _resolved_value(plan.get('activity')),
            'wardrobe': _resolved_value(plan.get('wardrobe')) or metadata.get('wardrobe_level'),
            'camera_mode': contract.get('camera_mode'),
            'framing': requirements.get('framing_requirement') or composition.get('framing') or metadata.get('resolved_requested_framing') or contract.get('framing'),
            'partner_visible': contract.get('partner_visible'),
            'face_visible': contract.get('face_visible'),
            'face_hidden': contract.get('face_hidden'),
            'back_to_camera': contract.get('back_to_camera'),
            'primary_subject': contract.get('primary_subject') or 'partner',
            'request_type': contract.get('request_type') or 'partner_photo',
            'current_scene_from_chat': contract.get('current_scene_from_chat'),
            'scene_context_summary': contract.get('scene_context_summary'),
            'nudity_level': nudity_level,
            'explicit_anatomy_focus': bool(requirements.get('anatomy_qa_required') or adult_intent.get('explicit_anatomy_focus')),
            'required_visible_environment_elements': list(contract.get('required_visible_environment_elements') or must.get('required_scene_elements') or []),
            'required_body_regions': required_regions,
            'forbidden_body_regions': forbidden_regions,
            'freeform_visual_constraints': list(current.get('passthrough_visual_details') or []),
        }
        for key, value in values.items():
            if value in (None, '', [], {}):
                continue
            if key in list_fields:
                merged[key]=list(dict.fromkeys(list(merged.get(key) or []) + list(value)))
            else:
                merged[key]=value
    return '؛ سپس '.join(requests), merged

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
    recent_summary=None; plan_summary=None; retrievable=False; exact_artifact=False; seconds=None
    if recent:
        retrievable=v2.source_job_is_context_eligible(recent, user_id=user_id, chat_id=chat_id)
        exact_artifact=v2.source_job_is_retrievable(recent, user_id=user_id, chat_id=chat_id)
        if recent.sent_at: seconds=max(0, int((datetime.utcnow()-recent.sent_at).total_seconds()))
        plan=v2.deserialize_resolved_plan(getattr(recent,'resolved_plan_json',None) or ((recent.metadata_json or {}).get('resolved_plan') if recent.metadata_json else None))
        compact = None
        if plan:
            compact=f"action={plan.action}; scene={getattr(plan.scene,'value',None)}; pose={getattr(plan.pose,'value',None)}; objects={getattr(plan.required_objects,'value',[])}"
            plan_summary=RecentResolvedImagePlanSummary(job_id=recent.id, action=plan.action, scene=getattr(plan.scene,'value',None), location=getattr(plan.location,'value',None), pose=getattr(plan.pose,'value',None), visible_fields=['scene','pose','required_objects'], invariant_codes=(plan.validation_results or {}).get('errors',[]))
        recent_summary=_job_summary(db, recent, compact_user_visible_summary=compact)
    active_summary=_job_summary(db, active)
    latest_summary=_job_summary(db, latest)
    if latest_summary and str(latest_summary.status or '') in FAILED_IMAGE_JOB_STATUSES:
        failed_rows=db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status.in_(FAILED_IMAGE_JOB_STATUSES)).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(3)).all()
        cutoff=datetime.utcnow()-timedelta(hours=2)
        failed_rows=[row for row in failed_rows if not getattr(row, 'created_at', None) or row.created_at >= cutoff]
        retry_chain=[]
        for row in failed_rows:
            if retry_chain:
                newer=retry_chain[-1]
                newer_text=' '.join(str(getattr(newer, 'user_request', '') or '').replace('‌',' ').split())
                if not any(marker in newer_text for marker in ('قبلی','همین','همون','همونو')):
                    break
                newer_at=getattr(newer, 'created_at', None); older_at=getattr(row, 'created_at', None)
                if newer_at and older_at and newer_at-older_at > timedelta(minutes=10):
                    break
            retry_chain.append(row)
        retry_text, retry_visual=merge_failed_image_retry_contract(retry_chain)
        latest_summary.retry_request_text=retry_text or None
        latest_summary.retry_visual_intent=retry_visual or None
    if active_summary:
        import logging; logging.getLogger(__name__).info("IMAGE_ACTIVE_JOB_CONTEXT_ATTACHED user_id=%s job_id=%s request_chain_id=%s action=%s job_status=%s", user_id, active_summary.job_id, active_summary.request_chain_id, active_summary.action, active_summary.status)
    return SemanticImageRouterContext(current_user_message=current_text, recent_conversation=turns, reply_to_message=reply_meta, active_image_job=active_summary, latest_image_job=latest_summary, recent_image_job=recent_summary, recent_resolved_image_plan=plan_summary, recent_retrievable_image_exists=retrievable, recent_exact_artifact_exists=exact_artifact, seconds_since_recent_image=seconds, legacy_route_decision=legacy_route_decision)
