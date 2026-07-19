from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    llm_provider: str = "offline"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_fallback_enabled: bool = True
    work_order_base_url: str = "http://localhost:8080"
    knowledge_path: Path = Path("knowledge/policies")
    ai_database_url: str = (
        "postgresql+asyncpg://ai_app:ai_app_dev@postgres:5432/workorders"
    )
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimensions: Literal[512] = 512
    fastembed_cache_path: Path = Path("/models")
    embedding_base_url: str = ""
    embedding_api_key: SecretStr | None = None
    embedding_timeout_seconds: float = 30.0

    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def parse_embedding_dimensions(cls, value: object) -> object:
        if value == "512":
            return 512
        return value

    def api_key_value(self) -> str:
        return self.llm_api_key.get_secret_value().strip() if self.llm_api_key else ""

    def embedding_api_key_value(self) -> str:
        return (
            self.embedding_api_key.get_secret_value().strip()
            if self.embedding_api_key
            else ""
        )
