"""配置健康检查（Fail-Fast）：启动时按「域开关 → 必填项」矩阵校验。

- problems（启动即拒绝）：开关已开启但依赖缺失 / 同域配置残缺不齐
- warnings（放行但提示）：开关关闭却残留孤儿配置 / 可选资源缺失（如声纹模型文件）
main.py 启动时调用 enforce()；结果摘要经 /healthz 暴露。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger(__name__)

_last_result: dict = {"ok": True, "problems": [], "warnings": []}


def _check(s: "Settings") -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []

    # --- ASR 提供方 ---
    if s.asr_provider not in ("xuanpu", "gateway"):
        problems.append(f"ASR_PROVIDER 非法取值 {s.asr_provider!r}（可选 xuanpu|gateway）")
    elif s.asr_provider == "gateway" and not s.main_api_url:
        problems.append("ASR_PROVIDER=gateway 需 MAIN_API_URL（LLM 网关同源 ASR）")
    elif s.asr_provider == "xuanpu" and not s.xuanpu_api_url:
        problems.append("ASR_PROVIDER=xuanpu 需 XUANPU_API_URL")

    # --- MinIO 三件套要么全配要么全空 ---
    minio_parts = [bool(s.minio_endpoint), bool(s.minio_access_key), bool(s.minio_secret_key)]
    if any(minio_parts) and not all(minio_parts):
        problems.append("MINIO 配置残缺：endpoint/access_key/secret_key 须同时配置（缺一回退本地但视为配置错误）")

    # --- Langfuse 三件套 ---
    lf_parts = [bool(s.langfuse_host), bool(s.langfuse_public_key), bool(s.langfuse_secret_key)]
    if any(lf_parts) and not all(lf_parts):
        problems.append("LANGFUSE 配置残缺：host/public_key/secret_key 须同时配置")

    # --- SCMS 域：任一项配置 ⇒ 认证三要素齐备 ---
    scms_any = any([
        s.scms_nl2sql_url, s.scms_query_data_url, s.scms_ai_backend, s.scms_api_base,
    ])
    if scms_any:
        missing = []
        if not s.scms_auth_base:
            missing.append("SCMS_AUTH_BASE")
        if not (s.scms_login_username and s.scms_login_password) and not s.scms_nl2sql_token:
            missing.append("SCMS 服务账号（LOGIN_USERNAME/PASSWORD）或静态 SCMS_NL2SQL_TOKEN")
        if not s.scms_check_basic:
            missing.append("SCMS_CHECK_BASIC")
        if missing:
            problems.append(f"SCMS 出站已启用但缺：{'、'.join(missing)}")
        elif not (s.scms_login_username and s.scms_login_password):
            warnings.append("SCMS 仅有静态 token（无服务账号）：用户 token 失效后无回落")

    # --- 快捷登录出票 ---
    if s.kb_sso_base and not s.kb_sso_aes_key:
        problems.append("KB_SSO_BASE 已配置但缺 KB_SSO_AES_KEY")
    if s.scms_sso_base and not s.scms_sso_aes_key:
        problems.append("SCMS_SSO_BASE 已配置但缺 SCMS_SSO_AES_KEY")

    # --- 知识库接入网关 ---
    if s.kb_import_base and not (s.kb_import_service_id and s.kb_import_key):
        problems.append("KB_IMPORT_BASE 已配置但缺 KB_IMPORT_SERVICE_ID / KB_IMPORT_KEY")
    if s.kb_import_service_id and not s.kb_import_base:
        warnings.append("KB_IMPORT_SERVICE_ID/KEY 残留但 KB_IMPORT_BASE 未配置（知识库范围功能关闭）")

    # --- KB 直连问答 ---
    if s.kb_qa_enabled and not (s.kb_qa_url or s.kb_import_base):
        problems.append("KB_QA_ENABLED=true 需 KB_QA_URL 或 KB_IMPORT_BASE")

    # --- 报告嵌入 ---
    if (s.report_tpl_annual_id or s.report_tpl_fin_risk_id) and not s.report_embed_base:
        problems.append("REPORT_TPL_* 已配置但缺 REPORT_EMBED_BASE")

    # --- agent MCP 服务器 JSON 合法性 ---
    if s.agent_mcp_servers:
        try:
            arr = json.loads(s.agent_mcp_servers)
            if not isinstance(arr, list) or not all(
                isinstance(x, dict) and x.get("name") and x.get("url") for x in arr
            ):
                raise ValueError("结构应为 [{name,url,headers?}] 数组")
        except Exception as exc:
            problems.append(f"AGENT_MCP_SERVERS 非法 JSON：{exc}")

    # --- 声纹：开关开启 → 模型路径必填、文件缺失 WARN（引擎 fail-soft 降级） ---
    if s.voiceprint_enabled:
        if not s.voiceprint_model_path:
            problems.append("VOICEPRINT_ENABLED=true 但 VOICEPRINT_MODEL_PATH 为空")
        else:
            model_path = Path(__file__).resolve().parents[2] / s.voiceprint_model_path
            if not model_path.is_file():
                warnings.append(
                    f"声纹模型文件不存在：{model_path}（引擎将自动降级，说话人回退聚类编号；"
                    f"可执行 scripts/download_voiceprint_model.py 下载）"
                )

    # --- 会议看门狗超时合理性 ---
    if s.meeting_processing_timeout_minutes < 1:
        problems.append("MEETING_PROCESSING_TIMEOUT_MINUTES 须 >=1（归档重派兜底）")

    return problems, warnings


def run() -> dict:
    """执行检查并缓存结果（/healthz 读取）。"""
    from .config import settings

    global _last_result
    problems, warnings = _check(settings)
    _last_result = {"ok": not problems, "problems": problems, "warnings": warnings}
    return _last_result


def enforce() -> dict:
    """启动期强制校验：problems 非空 → 逐项 ERROR 并拒绝启动。"""
    result = run()
    for w in result["warnings"]:
        logger.warning("[config-check] %s", w)
    if not result["ok"]:
        for p in result["problems"]:
            logger.error("[config-check] %s", p)
        raise RuntimeError(
            "配置健康检查未通过（共 %d 项，见上方逐条日志；修正 .docker.env / .env 后重启）"
            % len(result["problems"])
        )
    if result["warnings"]:
        logger.warning("[config-check] 通过（含 %d 条提示）", len(result["warnings"]))
    else:
        logger.info("[config-check] 通过")
    return result


def last_result() -> dict:
    return _last_result
