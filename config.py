from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str
    PROXY_URL: str | None = None
    ADMIN_USER_ID: int = 1189033386
    # Используется только для однократного импорта старой очереди в SQLite.
    QUEUE_FILE: str = "queue_state.json"
    SUBSCRIPTIONS_ENFORCED: bool = False
    HOST_MAINTENANCE_TTL_SECONDS: int = 1800
    OZON_DIAGNOSTICS_ENABLED: bool = False

    class Config:
        env_file = ".env"


config = Settings()
