# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    dev_mode: bool = False

    app_slug: str = "ads-generator"

    gateway_login_url: str = "https://app-gateway.rpc.com.br/auth/login"
    gateway_refresh_url: str = "https://app-gateway.rpc.com.br/auth/refresh"
    gateway_introspect_url: str = "https://app-gateway.rpc.com.br/auth/introspect"

    secret_key: str = "replit-dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
