from __future__ import annotations
from dataclasses import dataclass
import logging
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SEMANTIC_ROUTER_ENABLED_KEY='image_generation.semantic_router_enabled'
SEMANTIC_ROUTER_PRODUCTION_APPROVED_KEY='image_generation.semantic_router_production_approved'
SEMANTIC_ROUTER_ALLOWED_USER_IDS_KEY='image_generation.semantic_router_allowed_user_ids'
SEMANTIC_ROUTER_SHADOW_MODE_KEY='image_generation.semantic_router_shadow_mode'

@dataclass(frozen=True)
class SemanticRouterFlags:
    execution_enabled: bool
    shadow_enabled: bool
    raw_enabled: bool
    production_approved: bool
    allowed_user_ids: set[int]


def _parse_user_ids(raw: str) -> set[int]:
    out=set()
    for part in str(raw or '').split(','):
        part=part.strip()
        if not part: continue
        out.add(int(part))
    return out


def resolve_semantic_router_flags(db: Session, *, user_id: int) -> SemanticRouterFlags:
    """Resolve semantic router gates; any settings problem fails closed."""
    try:
        from app.services.settings_service import SettingsService
        svc=SettingsService()
        raw_enabled=svc.get_bool(db, SEMANTIC_ROUTER_ENABLED_KEY, False)
        approved=svc.get_bool(db, SEMANTIC_ROUTER_PRODUCTION_APPROVED_KEY, False)
        allow=_parse_user_ids(svc.get_str(db, SEMANTIC_ROUTER_ALLOWED_USER_IDS_KEY, ''))
        shadow=svc.get_bool(db, SEMANTIC_ROUTER_SHADOW_MODE_KEY, False)
        exec_enabled=bool(raw_enabled and approved and int(user_id) in allow)
        return SemanticRouterFlags(exec_enabled, bool(shadow), bool(raw_enabled), bool(approved), allow)
    except Exception as exc:
        logger.info('IMAGE_SEMANTIC_ROUTER_FLAGS_FAILED user_id=%s error=%s', user_id, type(exc).__name__)
        return SemanticRouterFlags(False, False, False, False, set())
