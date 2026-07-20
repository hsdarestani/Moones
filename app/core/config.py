from functools import lru_cache
from urllib.parse import quote

from pydantic import model_validator
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
    tts_female_default_voice: str = "Aoede"
    tts_female_playful_voice: str = "Aoede"
    tts_male_default_voice: str = "Iapetus"
    tts_male_playful_voice: str = "Puck"
    tts_male_calm_voice: str = "Iapetus"
    sticker_catalog_json: str = ""
    llm_debug: bool = False
    prompt_mode: str = "simple_partner_v2"
    simple_chat_mode: bool = True
    openrouter_api_key: str = ""
    openrouter_model: str = "cognitivecomputations/dolphin-mistral-24b-venice-edition:free"
    admin_user: str = "admin"
    admin_password: str = "change-me"
    admin_basic_fallback_enabled: bool = False
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "mones"
    db_host: str = "postgres"
    db_port: int = 5432
    database_url: str = ""
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
    required_channel_enabled: bool = True
    required_channel_username: str = "@MoonesAI"
    required_channel_url: str = "https://t.me/MoonesAI"
    admin_max_credit_amount: int = 2_000_000_000
    billing_usd_to_toman: int = 60000
    billing_profit_margin_percent: int = 100

    image_input_enabled: bool = True
    vision_provider: str = "venice"
    vision_model: str = "qwen3-vl-235b-a22b"
    vision_fallback_model: str = "e2ee-qwen3-vl-30b-a3b-p"
    image_generation_fallback_model: str = "seedream-v5-lite"
    image_generation_adult_model: str = "lustify-sdxl"
    voice_input_enabled: bool = True
    stt_provider: str = "venice"
    stt_model: str = "openai/whisper-large-v3"
    stt_fallback_model: str = "stt-xai-v1"
    free_plan_media_enabled: bool = False
    store_raw_user_images: bool = False
    store_image_summary: bool = True
    store_telegram_file_id: bool = False
    support_media_forward_enabled: bool = True
    support_media_chat_id: str = ""
    support_forward_free_media: bool = False
    admin_media_forward_enabled: bool = True
    admin_media_review_chat_id: str = ""
    management_bot_username: str = "moonesaibot"
    management_bot_url: str = "https://t.me/moonesaibot"
    max_image_bytes: int = 8000000
    max_voice_bytes: int = 12000000
    max_voice_seconds: int = 120

    @model_validator(mode="after")
    def derive_database_url(self) -> "Settings":
        if not self.database_url and self.db_password:
            password = quote(self.db_password, safe="")
            self.database_url = f"postgresql+psycopg://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}"
        return self

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
