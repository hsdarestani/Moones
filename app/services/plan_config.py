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
        "mini": PlanConfig("mini", 590_000, timedelta(days=30), 80_000, 1, 8, "roleplay", "limited", True),
        "basic": PlanConfig("basic", 990_000, timedelta(days=30), 150_000, 2, 15, "roleplay", "improved", True),
        "plus": PlanConfig("plus", 2_290_000, timedelta(days=30), 500_000, 8, 30, "roleplay", "full", True, True),
        "vip": PlanConfig("vip", 4_900_000, timedelta(days=30), 1_200_000, 20, 60, "premium_roleplay", "full", True, True),
        # Backward compatible aliases for existing active subscriptions.
        "daily": PlanConfig("daily", 590_000, timedelta(days=1), 80_000, 1, 8, "roleplay", "limited", True),
        "weekly": PlanConfig("weekly", 990_000, timedelta(days=7), 150_000, 2, 15, "roleplay", "improved", True),
        "monthly": PlanConfig("monthly", 2_290_000, timedelta(days=30), 500_000, 8, 30, "roleplay", "full", True, True),
        "premium": PlanConfig("premium", 4_900_000, timedelta(days=30), 1_200_000, 20, 60, "premium_roleplay", "full", True, True),
    }
