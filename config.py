from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str
    PROXY_URL: str | None = None

    class Config:
        env_file = ".env"


config = Settings()
