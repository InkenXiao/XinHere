from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",), extra="ignore", env_ignore_empty=True
        # 本地开发回退 apps/backend/.env；容器内配置由 compose env_file（.docker.env）注入真实环境变量
    )

    # ---------- 数据库 ----------
    database_url: str = ""
    checkpoint_url: str = ""
    # .docker.env 兼容：无 DATABASE_URL 时由 POSTGRES_* 拼接（凭据必填，不再带默认口令）
    postgres_host: str = "localhost"
    postgres_port: int = 11000
    postgres_db: str = "xinhere_dev"
    postgres_user: str = ""
    postgres_password: str = ""

    # ---------- LLM 网关（多通道） ----------
    main_api_url: str = "http://localhost:8000/v1"
    main_api_key: str = ""
    main_model: str = "glm-5.2-fp8"
    main_max_tokens: int = 8192  # 主推理模型单次生成输出上限（推理型模型必须 >=4096）
    llm_thinking: bool = False  # 模型思考模式：默认源头关闭（思考文本会剥标签混入正文流）
    llm_reasoning_effort: str = "low"  # 推理思考深度 low/medium/high/max
    # 轻量快推通道 (对话页「快速」模型; 未配置或与主模型同名时不暴露)
    small_api_url: str = ""
    small_api_key: str = ""
    small_model: str = ""
    small_max_tokens: int = 4096
    # 代码生成通道 (预留: AI coding; 未配置时回退主推理模型)
    coder_api_url: str = ""
    coder_api_key: str = ""
    coder_model: str = ""
    coder_max_tokens: int = 8192
    # 视觉多模态通道 (对话附件图片识别)
    vision_api_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    vision_max_tokens: int = 4096
    # mineru 算力网关 (扫描件 PDF 深度解析; 留空则跳过该级)
    mineru_api_url: str = ""

    # ---------- Agent 执行护栏 ----------
    agent_recursion_limit: int = 60  # 单轮 graph 步数上限（拦截死循环烧 token）
    agent_input_guardrail: bool = True  # 输入注入检测（fail-soft，不阻断）
    # agent 外部 MCP 服务器（JSON 数组 [{"name":"kb","url":"http://…/mcp","headers":{}}]）；
    # 工具并入对话工具集（前缀 mcp_<server>_）。未配置=不接；不可达 fail-soft 跳过
    agent_mcp_servers: str = ""

    # ---------- Langfuse LLM 观测（可选；三项齐备即启用） ----------
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ---------- 语音转写 ----------
    # ASR 提供方：xuanpu=经玄圃 pro-cowork 转写（默认，WorkBuddy 语音输入链路）；
    # gateway=经 LLM 网关同源 ASR（scms 会议管线/声纹采样链路）
    asr_provider: str = "xuanpu"

    # ---------- 玄圃 XuanPu 集成 ----------
    xuanpu_api_url: str = "http://xuanpu-a:8091"  # 语音转写代理目标（pro-cowork）
    xuanpu_perm_url: str = ""  # 统一权限判定端点（空=SSO 登录回落旧逻辑）
    xuanpu_perm_token: str = ""
    kb_mcp_url: str = "https://localhost:8093/mcp"
    xuanpu_mcp_url: str = "https://nginx:8094/xuanpu/mcp"  # 平台 MCP 网关（mcp-cowork 8094）
    data_mcp_url: str = "https://nginx:8090/data/xuanpu/mcp"  # 数据网关（AI 问数只读）

    # ---------- 统一身份登录 SSO (XuanPu sso-server :8095) ----------
    sso_auth_url: str = "https://localhost:8090/sso/authorize"
    sso_token_url: str = "http://localhost:8095/sso/token"
    sso_ticket_url: str = "http://localhost:8095/sso/api/ticket"
    sso_verify_url: str = "http://localhost:8095/sso/api/verify"
    sso_client_id: str = "xinhere"
    sso_client_secret: str = ""
    sso_callback_url: str = "http://localhost:8096/api/v1/auth/sso/callback"
    frontend_url: str = "http://localhost:8096"
    xuanpu_public_url: str = "https://localhost:8090"  # Xin台 launch 浏览器可达根地址

    # ---------- SCMS 出站（默认全关；未配置=对应功能入口隐藏/守护不启动） ----------
    scms_nl2sql_url: str = ""  # 问数 nl2sql 服务完整 URL
    scms_nl2sql_token: str = ""  # 静态 token；配置服务账号后以服务账号优先
    scms_query_data_url: str = ""  # 问数评估流（SSE）
    scms_ai_backend: str = ""  # ai-session-service 基址（web_search/web_fetch、简报等路径代码拼接）
    scms_auth_base: str = ""  # 服务账号登录代理 + check-token 校验（同源）
    scms_login_username: str = ""  # 服务账号（未配置=只用静态 token）
    scms_login_password: str = ""
    scms_tenant_name: str = ""  # 登录代理租户名；未配置=不带 tenant-id 头
    scms_check_basic: str = ""  # check-token Basic 凭据（仅服务端持有，严禁进前端）
    scms_token_cache_ttl: int = 300  # 验真缓存上限秒
    scms_api_base: str = ""  # SCMS 业务接口（指标/里程碑）基址

    # ---------- 报告嵌入 ----------
    report_embed_base: str = ""  # 报告嵌入页（模板工坊）iframe 拼参目标
    report_tpl_annual_id: str = ""  # 企业年度项目管理报告（word-generator）
    report_tpl_fin_risk_id: str = ""  # 财务风险报告（ppt-generator）
    report_ppt_embed_base: str = ""  # PPT 专用基址（未配置回落 report_embed_base）

    # ---------- 看板/导航 ----------
    dashboard_embed_base: str = ""  # 看板 iframe 完整前缀（含 ?token=）；空=看板 iframe 入口隐藏
    nav_biz_url: str = ""  # 顶栏导航（空=隐藏）
    nav_kb_url: str = ""
    nav_cowork_url: str = ""
    nav_cowork_login_url: str = ""

    # ---------- 逆回购行情提醒 ----------
    repo_alert_ws_url: str = ""  # 公共 WS 流；空=接入关闭（不连守护）

    # ---------- 外部权限判权（驾驶舱卡片 perm_key 显隐；空=放行） ----------
    perm_check_url: str = ""
    perm_check_timeout_seconds: float = 3.0

    # ---------- 快捷登录出票（AES 简易票据） ----------
    kb_sso_base: str = ""  # 青山知识库门户根地址（空=隐藏入口、拒绝出票）
    kb_sso_aes_key: str = ""
    kb_sso_ttl_hours: int = 8
    scms_sso_base: str = ""  # SCMS 财务后台（空=隐藏导航入口、拒绝出票）
    scms_sso_aes_key: str = ""
    scms_sso_ttl_hours: int = 24
    cowork_code_aes_key: str = ""  # cowork 免登 code 兑换 AES（与 kb_sso 独立）

    # ---------- 业务主库（只读；空=组织目录/OA 菜单/ cowork 免登降级不可用） ----------
    biz_pg_uri: str = ""

    # ---------- 知识库数据接入网关（CoWork 通道；空=知识库范围功能降级） ----------
    kb_import_base: str = ""
    kb_import_service_id: str = ""
    kb_import_key: str = ""

    # ---------- 知识库直连问答（实验；默认关） ----------
    kb_qa_enabled: bool = False
    kb_qa_url: str = ""
    kb_qa_context_messages: int = 10
    kb_qa_timeout: int = 172800

    # ---------- AI 派发免卡自动执行白名单（仅 dev 生效） ----------
    auto_dispatch_scenes: str = ""

    # ---------- 对外 MCP 服务端（/mcp；默认关） ----------
    mcp_server_enabled: bool = False

    # ---------- 附件与对象存储 ----------
    artifact_dir: str = ""  # 任务交付物本地存储；空=apps/backend/data/artifacts
    artifact_max_mb: int = 20
    chat_attachment_max_mb: int = 20
    minio_endpoint: str = ""  # 三者齐备即启用（bucket 缺省 xinhere），否则回退本地磁盘
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "xinhere"
    minio_secure: bool = False
    minio_region: str = ""

    # ---------- 会议看门狗超时（分钟） ----------
    meeting_recording_timeout_minutes: int = 30
    meeting_paused_timeout_minutes: int = 60
    meeting_processing_timeout_minutes: int = 3  # 归档卡死重派兜底

    # ---------- 声纹识别（默认关；说话人回退纯聚类编号） ----------
    voiceprint_enabled: bool = False
    voiceprint_model_path: str = "data/models/campplus.onnx"
    voiceprint_match_threshold: float = 0.54
    voiceprint_match_margin: float = 0.05
    voiceprint_min_identify_seconds: float = 2.0
    voiceprint_alias_match_threshold: float = 0.50
    voiceprint_alias_min_update_seconds: float = 1.0
    voiceprint_min_enroll_seconds: float = 10.0
    voiceprint_max_samples: int = 5
    biometric_protocol_version: str = "v1"

    token_ttl_hours: int = 72
    allowed_hosts: str = "localhost,127.0.0.1"
    cors_origins: str = "http://localhost:8095,http://localhost:5173"

    @property
    def minio_enabled(self) -> bool:
        return bool(self.minio_endpoint and self.minio_access_key and self.minio_secret_key)

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_host and self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auto_dispatch_scene_list(self) -> set[str]:
        return {s.strip() for s in (self.auto_dispatch_scenes or "").split(",") if s.strip()}

    def model_post_init(self, __context) -> None:
        if not self.database_url:
            if not (self.postgres_user and self.postgres_password):
                raise RuntimeError("数据库未配置：需 DATABASE_URL 或完整 POSTGRES_*（用户/密码必填）")
            self.database_url = (
                f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        if not self.checkpoint_url:
            self.checkpoint_url = self.database_url.replace("postgresql+psycopg://", "postgresql://")


settings = Settings()
