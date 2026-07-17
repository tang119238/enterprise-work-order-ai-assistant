from pathlib import Path

from pydantic import SecretStr
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

    def api_key_value(self) -> str:
        return self.llm_api_key.get_secret_value().strip() if self.llm_api_key else ""

