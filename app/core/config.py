from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Mones"
    environment: str = "development"
    telegram_bot_token: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    database_url: str = "postgresql+psycopg://postgres:postgres@postgres:5432/mones"
    redis_url: str = "redis://redis:6379/0"
    secret_key: str = "change-me"
    allow_explicit_content: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
