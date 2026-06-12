from dataclasses import dataclass
from datetime import timedelta

from app.core.config import get_settings


@dataclass(frozen=True)
class PlanConfig:
    code: str
    price_usd: float
    duration: timedelta | None
    daily_message_limit: int
    model_tier: str
    memory_level: str
    romantic_mode: bool
    priority: bool = False


def get_plan_configs() -> dict[str, PlanConfig]:
    settings = get_settings()
    return {
        "free": PlanConfig("free", 0, None, settings.default_free_daily_limit, "normal", "limited", False),
        "daily": PlanConfig("daily", 0.99, timedelta(days=1), settings.daily_pass_message_limit, "roleplay", "improved", True),
        "weekly": PlanConfig("weekly", 3.99, timedelta(days=7), settings.weekly_pass_message_limit, "roleplay", "improved", True),
        "monthly": PlanConfig("monthly", 14.99, timedelta(days=30), settings.monthly_pass_message_limit, "roleplay", "full", True),
        "premium": PlanConfig("premium", 24.99, timedelta(days=30), settings.premium_message_limit, "premium_roleplay", "full", True, True),
    }
