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

    kb_mcp_url: str = "https://localhost:8093/mcp"
    # XuanPu 平台 MCP 网关（mcp-cowork 8094 子挂载；经 ai_network 内 nginx TLS 入口）
    xuanpu_mcp_url: str = "https://nginx:8094/xuanpu/mcp"

    # ---------- 统一身份登录 SSO (XuanPu sso-server :8095) ----------
    # 授权入口走浏览器可达的 nginx https; token/ticket 接口走容器内网直达
    sso_auth_url: str = "https://192.168.1.161:8090/sso/authorize"
    sso_token_url: str = "http://xuanpu:8095/sso/token"
    sso_ticket_url: str = "http://xuanpu:8095/sso/api/ticket"
    sso_client_id: str = "xinhere"
    sso_client_secret: str = ""
    sso_callback_url: str = "http://192.168.1.161:8096/api/v1/auth/sso/callback"
    frontend_url: str = "http://192.168.1.161:8096"
    # Xin台 launch 的浏览器可达 XuanPu 根地址 (nginx https 入口)
    xuanpu_public_url: str = "https://192.168.1.161:8090"

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
