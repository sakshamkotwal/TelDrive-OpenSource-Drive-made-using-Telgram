from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: Optional[str] = None

    # Security
    SECRET_KEY: str = "changeme_in_production_secret_key_for_jwt"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Server
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # File handling
    MAX_CHUNK_SIZE: int = 10 * 1024 * 1024  # 10 MB (safe limit for memory and Telegram)
    DOWNLOAD_BATCH_SIZE: int = 5 # Number of chunks to download in parallel

    # Logging
    LOG_LEVEL: str = "INFO"

    model_config = {
        "env_file": ".env",
        "case_sensitive": True
    }

settings = Settings()
