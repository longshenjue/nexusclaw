from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache
import secrets


class Settings(BaseSettings):
    app_name: str = "NexusClaw"
    app_env: str = "development"
    secret_key: str = ""
    fernet_key: str = ""  # Generated via Fernet.generate_key()

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/nexusclaw"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # AI
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""  # Optional proxy, e.g. https://your-proxy.example.com
    openai_api_key: str = ""
    openai_base_url: str = ""  # Optional proxy for OpenAI-compatible APIs

    # GitHub (legacy — use access_token_encrypted on KnowledgeSource for per-repo tokens) (legacy — use access_token_encrypted on KnowledgeSource for per-repo tokens)
    github_token: str = ""

    # Data paths
    upload_dir: str = "./data/uploads"
    repos_dir: str = "/app/data/repos"

    @model_validator(mode="after")
    def validate_required_secrets(self) -> "Settings":
        if not self.secret_key or self.secret_key.startswith("change-this"):
            raise ValueError(
                "SECRET_KEY is not set. Generate one with: "
                "python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if not self.fernet_key or self.fernet_key.startswith("change-this"):
            raise ValueError(
                "FERNET_KEY is not set. Generate one with: "
                "python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
