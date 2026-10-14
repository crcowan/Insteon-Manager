"""Configuration settings for Insteon Bridge backend."""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # openHAB Configuration
    openhab_url: str = "http://localhost:8080"
    openhab_token: str = ""
    openhab_thing_uid: str = "insteon:network:local"

    # Insteon Hardware Configuration
    insteon_port: str = "/dev/ttyUSB0"
    insteon_baud: int = 19200

    # Safety Watchdog Settings
    # Auto-release lease after N seconds of silence to ensure openHAB is never left locked out
    session_timeout_seconds: int = 300

    # Cache directory for pyinsteon device/ALDB state
    cache_dir: str = "./insteon_cache"

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
