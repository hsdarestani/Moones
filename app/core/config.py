from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Mones"
    environment: str = "development"
    telegram_bot_token: str = ""
    telegram_management_bot_token: str = ""
    telegram_management_bot_username: str = ""
    telegram_chat_bot_token: str = ""
    telegram_chat_bot_username: str = ""
    admin_telegram_ids: str = ""
    venice_api_key: str = ""
    venice_api_base_url: str = "https://api.venice.ai/api/v1"
    venice_model: str = "zai-org-glm-5-1"
    venice_timeout_seconds: int = 60
    openrouter_api_key: str = ""
    openrouter_model: str = "cognitivecomputations/dolphin-mistral-24b-venice-edition:free"
    admin_user: str = "admin"
    admin_password: str = "change-me"
    database_url: str = "postgresql+psycopg://postgres:postgres@postgres:5432/mones"
    redis_url: str = "redis://redis:6379/0"
    secret_key: str = "change-me"
    allow_explicit_content: bool = False
    enable_test_wallet_topup: bool = False
    support_username: str = ""
    payment_link: str = "https://www.coffeebede.com/gotomarket"
    default_free_daily_limit: int = 30
    daily_pass_message_limit: int = 500
    weekly_pass_message_limit: int = 500
    monthly_pass_message_limit: int = 500
    premium_message_limit: int = 1000

    @property
    def management_bot_token(self) -> str:
        return self.telegram_management_bot_token or self.telegram_bot_token

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_telegram_ids.split(",") if x.strip().isdigit()}

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
