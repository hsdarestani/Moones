from __future__ import annotations
import hashlib, logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.image_generation import ImageGenerationJob

logger=logging.getLogger(__name__)

class ImageRequestState(StrEnum):
    IDLE='idle'; PENDING_NEW_IMAGE='pending_new_image'; PENDING_REFINE_PREVIOUS='pending_refine_previous'; PENDING_VARIATION='pending_variation'; PENDING_RESEND='pending_resend'; AWAITING_CLARIFICATION='awaiting_clarification'; AWAITING_WALLET_TOPUP='awaiting_wallet_topup'; QUEUED='queued'; GENERATING='generating'; DELIVERED='delivered'; FAILED='failed'; CANCELLED='cancelled'

@dataclass
class ImageRequestChain:
    request_chain_id: str
    current_image_state: str = ImageRequestState.IDLE
    parent_request_id: int|None = None
    clarification_target: dict[str,Any]|None = None
    resumed_after_topup: bool = False
    original_user_intent_snapshot: dict[str,Any] = field(default_factory=dict)
    last_user_command_hash: str|None = None
    last_user_command_at: str|None = None
    acknowledgement_sent: bool = False

STATE_KEY='image_request_chain_state_v1'
DEDUP_WINDOW=timedelta(seconds=20)

def _hash(text:str)->str: return hashlib.sha256((text or '').strip().encode()).hexdigest()[:16]
def _chain_id(user_id:int, parent:int|None, text:str)->str: return hashlib.sha256(f'{user_id}:{parent or 0}:{_hash(text)}'.encode()).hexdigest()[:16]

def action_to_state(action:str)->str:
    return {'generate_new':ImageRequestState.PENDING_NEW_IMAGE,'new_generation':ImageRequestState.PENDING_NEW_IMAGE,'refine_previous':ImageRequestState.PENDING_REFINE_PREVIOUS,'refinement':ImageRequestState.PENDING_REFINE_PREVIOUS,'variation':ImageRequestState.PENDING_VARIATION,'resend_exact':ImageRequestState.PENDING_RESEND}.get(str(action), ImageRequestState.IDLE)

JOB_TO_CHAIN_STATE={'queued':ImageRequestState.QUEUED,'processing':ImageRequestState.GENERATING,'generating':ImageRequestState.GENERATING,'sent':ImageRequestState.DELIVERED,'failed':ImageRequestState.FAILED,'delivery_failed':ImageRequestState.FAILED,'cancelled':ImageRequestState.CANCELLED}
ACTIVE_CHAIN_STATES={ImageRequestState.AWAITING_CLARIFICATION, ImageRequestState.AWAITING_WALLET_TOPUP, ImageRequestState.QUEUED, ImageRequestState.GENERATING}

def sync_image_request_chain_state(job:ImageGenerationJob, terminal_state:str|None=None)->ImageRequestChain|None:
    if not job: return None
    state=str(terminal_state or JOB_TO_CHAIN_STATE.get(str(getattr(job,'status',None)), getattr(job,'current_image_state',None) or ImageRequestState.IDLE))
    meta=dict(getattr(job,'metadata_json',None) or {})
    raw=meta.get(STATE_KEY) if isinstance(meta.get(STATE_KEY), dict) else {}
    chain=ImageRequestChain(**{**raw, 'request_chain_id': raw.get('request_chain_id') or getattr(job,'request_chain_id',None) or _chain_id(getattr(job,'user_id',0), getattr(job,'parent_request_id',None), getattr(job,'user_request','') or ''), 'current_image_state': state})
    job.current_image_state=state
    if getattr(job,'request_chain_id',None) is None: job.request_chain_id=chain.request_chain_id
    meta.update(metadata_for_chain(chain)); job.metadata_json=meta
    if getattr(job,'clarification_target',None) and state in {ImageRequestState.DELIVERED, ImageRequestState.FAILED, ImageRequestState.CANCELLED}: job.clarification_target=None
    logger.info('IMAGE_CHAIN_TERMINAL_STATE_SYNCED user_id=%s job_id=%s request_chain_id=%s action=%s job_status=%s reason_codes=%s', getattr(job,'user_id',None), getattr(job,'id',None), chain.request_chain_id, getattr(job,'image_action',None), getattr(job,'status',None), (getattr(job,'metadata_json',{}) or {}).get('final_qa_reason_codes'))
    return chain

def load_active_image_chain(db:Session, *, user_id:int)->ImageRequestChain|None:
    if db is None: return None
    job=db.scalar(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(1))
    if not job: return None
    chain=sync_image_request_chain_state(job)
    return chain if chain and chain.current_image_state in ACTIVE_CHAIN_STATES else None

def begin_or_update_chain(db:Session, *, user_id:int, action:str, text:str, parent_request_id:int|None=None, now:datetime|None=None, active:ImageRequestChain|None=None)->ImageRequestChain:
    now=now or datetime.utcnow(); h=_hash(text); active=active or load_active_image_chain(db,user_id=user_id)
    if active and active.current_image_state in {ImageRequestState.AWAITING_CLARIFICATION, ImageRequestState.AWAITING_WALLET_TOPUP} or (active and active.current_image_state in {ImageRequestState.QUEUED, ImageRequestState.GENERATING} and action in {'clarification_answer','modify_pending','wallet_topup_resume','cancel_pending','status_query'}):
        chain=active; chain.current_image_state=action_to_state(action); chain.last_user_command_hash=h; chain.last_user_command_at=now.isoformat()
        logger.info('IMAGE_REQUEST_CHAIN_UPDATED user_id=%s request_chain_id=%s action=%s reason_code=%s fulfillment_failure_codes=%s continuity_mode=%s', user_id, chain.request_chain_id, action, 'active_chain_update', [], action)
        return chain
    chain=ImageRequestChain(_chain_id(user_id,parent_request_id,text), action_to_state(action), parent_request_id, None, False, {'action':action,'request_hash':h}, h, now.isoformat(), False)
    logger.info('IMAGE_REQUEST_CHAIN_CREATED user_id=%s request_chain_id=%s action=%s reason_code=%s fulfillment_failure_codes=%s continuity_mode=%s', user_id, chain.request_chain_id, action, 'new_chain', [], action)
    return chain

def is_duplicate_command(chain:ImageRequestChain|None, text:str, *, now:datetime|None=None)->bool:
    if not chain or chain.last_user_command_hash != _hash(text) or not chain.last_user_command_at: return False
    try: last=datetime.fromisoformat(chain.last_user_command_at)
    except Exception: return False
    return (now or datetime.utcnow())-last <= DEDUP_WINDOW and chain.current_image_state not in {ImageRequestState.FAILED, ImageRequestState.DELIVERED}

def mark_state(chain:ImageRequestChain, state:str, **updates)->ImageRequestChain:
    chain.current_image_state=str(state)
    for k,v in updates.items(): setattr(chain,k,v)
    return chain

def metadata_for_chain(chain:ImageRequestChain)->dict: return {STATE_KEY: asdict(chain), 'request_chain_id': chain.request_chain_id, 'current_image_state': chain.current_image_state, 'parent_request_id': chain.parent_request_id, 'clarification_target': chain.clarification_target, 'resumed_after_topup': chain.resumed_after_topup, 'original_user_intent_snapshot': chain.original_user_intent_snapshot}
