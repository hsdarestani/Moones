from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class PlanConfig:
    code: str
    price_coins: int
    duration: timedelta | None
    daily_token_limit: int
    daily_voice_limit: int
    daily_sticker_limit: int
    model_tier: str = "normal"
    memory_level: str = "limited"
    romantic_mode: bool = False
    priority: bool = False


def get_plan_configs() -> dict[str, PlanConfig]:
    return {
        "free": PlanConfig("free", 0, None, 20_000, 0, 3),
        "mini": PlanConfig("mini", 5_900, timedelta(days=30), 80_000, 1, 8, "roleplay", "limited", True),
        "basic": PlanConfig("basic", 9_900, timedelta(days=30), 150_000, 2, 15, "roleplay", "improved", True),
        "plus": PlanConfig("plus", 22_900, timedelta(days=30), 2_000_000_000, 8, 60, "roleplay", "full", True, True),
        "vip": PlanConfig("vip", 49_000, timedelta(days=30), 2_000_000_000, 20, 120, "premium_roleplay", "full", True, True),
        # Backward compatible aliases for existing active subscriptions.
        "daily": PlanConfig("daily", 5_900, timedelta(days=1), 80_000, 1, 8, "roleplay", "limited", True),
        "weekly": PlanConfig("weekly", 9_900, timedelta(days=7), 150_000, 2, 15, "roleplay", "improved", True),
        "monthly": PlanConfig("monthly", 22_900, timedelta(days=30), 2_000_000_000, 8, 60, "roleplay", "full", True, True),
        "premium": PlanConfig("premium", 49_000, timedelta(days=30), 2_000_000_000, 20, 120, "premium_roleplay", "full", True, True),
    }
