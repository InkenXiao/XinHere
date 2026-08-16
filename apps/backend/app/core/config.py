from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../../.docker.env", ".env"), extra="ignore", env_ignore_empty=True
    )

    database_url: str = ""
    checkpoint_url: str = ""
    # .docker.env 兼容：无 DATABASE_URL 时由 POSTGRES_* 拼接
    postgres_host: str = "localhost"
    postgres_port: int = 11000
    postgres_db: str = "xinhere"
    postgres_user: str = "dbuser"
    postgres_password: str = "Siiit2026"

    main_api_url: str = "http://localhost:8000/v1"
    main_api_key: str = ""
    main_model: str = "glm-5.2-fp8"  # LLM 别名上游故障，勿用
    llm_max_tokens: int = 8192  # 推理型模型必须 >=4096

    kb_mcp_url: str = "http://localhost:8093/mcp"
    token_ttl_hours: int = 72
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = "http://localhost:8095,http://localhost:5173"

    def model_post_init(self, __context) -> None:
        if not self.database_url:
            self.database_url = (
                f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        if not self.checkpoint_url:
            self.checkpoint_url = self.database_url.replace("postgresql+psycopg://", "postgresql://")

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
