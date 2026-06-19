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
    venice_model: str = "qwen-3-6-plus"
    venice_timeout_seconds: int = 6
    venice_tts_enabled: bool = True
    venice_tts_model: str = "tts-gemini-3-1-flash"
    venice_tts_voice: str = ""
    venice_tts_format: str = "mp3"
    venice_tts_random_voice: bool = False
    sticker_catalog_json: str = ""
    llm_debug: bool = False
    prompt_mode: str = "simple_partner_v2"
    simple_chat_mode: bool = True
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
    free_daily_token_limit: int = 20_000
    mini_daily_token_limit: int = 80_000
    basic_daily_token_limit: int = 150_000
    plus_daily_token_limit: int = 500_000
    vip_daily_token_limit: int = 1_200_000

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
