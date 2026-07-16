from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


PIPELINE_V2_ENABLED_KEY = 'image_generation.pipeline_v2_enabled'
PIPELINE_V2_PRODUCTION_APPROVED_KEY = 'image_generation.pipeline_v2_production_approved'
PIPELINE_V2_SHADOW_MODE_KEY = 'image_generation.pipeline_v2_shadow_mode'


@dataclass(frozen=True)
class ImagePipelineV2Flags:
    execution_enabled: bool
    shadow_enabled: bool
    raw_enabled: bool
    production_approved: bool


def resolve_image_pipeline_v2_flags(db: Session) -> ImagePipelineV2Flags:
    """Resolve Image Pipeline v2 execution/shadow gates, failing closed."""
    try:
        from app.services.settings_service import SettingsService

        svc = SettingsService()
        raw_enabled = svc.get_bool(db, PIPELINE_V2_ENABLED_KEY, False)
        production_approved = svc.get_bool(db, PIPELINE_V2_PRODUCTION_APPROVED_KEY, False)
        raw_shadow = svc.get_bool(db, PIPELINE_V2_SHADOW_MODE_KEY, False)
        execution_enabled = bool(raw_enabled and production_approved)
        return ImagePipelineV2Flags(
            execution_enabled=execution_enabled,
            shadow_enabled=bool(raw_shadow and not execution_enabled),
            raw_enabled=bool(raw_enabled),
            production_approved=bool(production_approved),
        )
    except Exception as exc:
        logger.info('IMAGE_V2_FLAGS_RESOLVE_FAILED error=%s', type(exc).__name__)
        return ImagePipelineV2Flags(
            execution_enabled=False,
            shadow_enabled=False,
            raw_enabled=False,
            production_approved=False,
        )
