from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str
    PROXY_URL: str | None = None
    ADMIN_USER_ID: int = 1189033386
    QUEUE_FILE: str = "queue_state.json"

    class Config:
        env_file = ".env"


config = Settings()
