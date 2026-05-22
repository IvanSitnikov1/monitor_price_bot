from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str
    PROXY_URL: str | None = None
    DEBUG_CAPTCHA: bool = False

    class Config:
        env_file = ".env"


config = Settings()
