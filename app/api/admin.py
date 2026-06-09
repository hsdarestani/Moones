import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.engine.relationship_engine import ensure_relationship
from app.llm.client import LLMClient
from app.llm.response_processor import post_process_response
from app.memory.memory_manager import memory_summary
from app.models.memory import MemoryItem
from app.models.message import Message
from app.models.relationship import Relationship, RelationshipStage
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    settings = get_settings()
    valid_user = secrets.compare_digest(credentials.username, settings.admin_user)
    valid_password = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    users = db.execute(
        select(User, Relationship, func.count(Message.id).label("total_messages"))
        .outerjoin(Relationship, Relationship.user_id == User.id)
        .outerjoin(Message, Message.user_id == User.id)
        .group_by(User.id, Relationship.id)
        .order_by(User.last_seen_at.desc())
    ).all()
    analytics = _analytics(db)
    return templates.TemplateResponse("admin/dashboard.html", {"request": request, "users": users, "analytics": analytics})


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(
    user_id: int,
    request: Request,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    state = ensure_relationship(user.id, user.relationship_state)
    filters = [Message.user_id == user.id]
    if q:
        filters.append(Message.content.ilike(f"%{q}%"))
    if start:
        filters.append(Message.created_at >= datetime.fromisoformat(start))
    if end:
        filters.append(Message.created_at <= datetime.fromisoformat(end) + timedelta(days=1))
    messages = db.scalars(select(Message).where(and_(*filters)).order_by(Message.created_at.asc())).all()
    memories = db.scalars(select(MemoryItem).where(MemoryItem.user_id == user.id).order_by(MemoryItem.created_at.desc()).limit(25)).all()
    inspector = {
        "relationship_state": state,
        "emotion_state": _latest_emotion(db, user.id),
        "memory_summary": memory_summary(db, user.id),
        "last_prompt": user.last_prompt or "No prompt captured yet.",
        "last_llm_response": user.last_llm_response or "No response captured yet.",
    }
    return templates.TemplateResponse(
        "admin/user_detail.html",
        {"request": request, "user": user, "state": state, "messages": messages, "memories": memories, "inspector": inspector, "stages": [stage.value for stage in RelationshipStage], "q": q or "", "start": start or "", "end": end or ""},
    )


@router.post("/users/{user_id}/reset-state")
def reset_state(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    rel = db.scalar(select(Relationship).where(Relationship.user_id == user_id))
    if rel:
        rel.intimacy = 0.05
        rel.attachment = 0.05
        rel.trust = 0.05
        rel.dependency = 0.0
        rel.attraction = 0.03
        rel.volatility = 0.2
        rel.stage = RelationshipStage.STRANGER.value
        rel.daily_streak = 0
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/reset-memory")
def reset_memory(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    for item in db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id)).all():
        db.delete(item)
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/force-stage")
async def force_stage(user_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    stage = str(form.get("stage", RelationshipStage.STRANGER.value))
    if stage not in {item.value for item in RelationshipStage}:
        raise HTTPException(status_code=400, detail="Invalid stage")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    rel = ensure_relationship(user.id, user.relationship_state)
    if rel.id is None:
        db.add(rel)
    rel.stage = stage
    db.add(MemoryItem(user_id=user.id, type="relationship_milestone", content=f"Admin forced relationship stage to {stage}.", importance_score=0.8))
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/rerun-last-prompt")
async def rerun_last_prompt(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    user = db.get(User, user_id)
    if user is None or not user.last_prompt:
        raise HTTPException(status_code=404, detail="Last prompt not found")
    messages = _parse_captured_prompt(user.last_prompt)
    raw = await LLMClient().complete(messages)
    response = post_process_response(raw)
    user.last_llm_response = raw
    db.add(Message(user_id=user.id, role="assistant_debug", content=response))
    db.commit()
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


def _analytics(db: Session) -> dict[str, float | int]:
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    dau = db.scalar(select(func.count(User.id)).where(User.last_seen_at >= day_ago)) or 0
    total_users = db.scalar(select(func.count(User.id))) or 0
    total_messages = db.scalar(select(func.count(Message.id))) or 0
    avg_messages = round(total_messages / total_users, 2) if total_users else 0
    sessions = db.execute(select(Message.user_id, func.min(Message.created_at), func.max(Message.created_at)).group_by(Message.user_id)).all()
    avg_session = 0
    if sessions:
        avg_session = round(sum((row[2] - row[1]).total_seconds() / 60 for row in sessions) / len(sessions), 2)
    return {
        "dau": dau,
        "avg_messages_per_user": avg_messages,
        "retention_d1": _retention(db, 1),
        "retention_d3": _retention(db, 3),
        "retention_d7": _retention(db, 7),
        "avg_session_length": avg_session,
    }


def _retention(db: Session, days: int) -> float:
    cutoff = datetime.utcnow() - timedelta(days=days)
    cohort = db.scalars(select(User).where(User.created_at <= cutoff)).all()
    if not cohort:
        return 0
    retained = sum(1 for user in cohort if user.last_seen_at >= user.created_at + timedelta(days=days))
    return round((retained / len(cohort)) * 100, 2)


def _latest_emotion(db: Session, user_id: int) -> str:
    message = db.scalar(select(Message).where(Message.user_id == user_id, Message.emotion.is_not(None)).order_by(Message.created_at.desc()).limit(1))
    return message.emotion if message and message.emotion else "neutral"


def _parse_captured_prompt(captured: str) -> list[dict[str, str]]:
    messages = []
    for part in captured.split("\n\n"):
        role, _, content = part.partition(": ")
        if role in {"system", "user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages or [{"role": "user", "content": captured}]
