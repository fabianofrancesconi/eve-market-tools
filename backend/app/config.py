"""Application configuration via environment variables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:dev@localhost/eve_market_tools"
    token_encryption_key: str = "dev-key-not-for-production-32bytes!"
    eve_client_id: str = ""
    eve_callback_url: str = "http://localhost:5173/auth/callback"
    cache_dir: str = ".eve_scanner_cache"
    session_ttl_days: int = 30
    cors_origins: str = "http://localhost:5173"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
