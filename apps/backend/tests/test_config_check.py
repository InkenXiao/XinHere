"""config_check 单测：域开关 → 必填项矩阵（Fail-Fast）"""
from __future__ import annotations

from types import SimpleNamespace

from app.core import config_check


def _s(**kw) -> SimpleNamespace:
    """最小可跑配置基线：全部域关闭。"""
    base = dict(
        asr_provider="xuanpu", xuanpu_api_url="http://x:8091", main_api_url="http://x:8000/v1",
        minio_endpoint="", minio_access_key="", minio_secret_key="",
        langfuse_host="", langfuse_public_key="", langfuse_secret_key="",
        scms_nl2sql_url="", scms_nl2sql_token="", scms_query_data_url="", scms_ai_backend="",
        scms_api_base="", scms_auth_base="", scms_login_username="", scms_login_password="",
        scms_check_basic="",
        kb_sso_base="", kb_sso_aes_key="", scms_sso_base="", scms_sso_aes_key="",
        kb_import_base="", kb_import_service_id="", kb_import_key="",
        kb_qa_enabled=False, kb_qa_url="",
        report_tpl_annual_id="", report_tpl_fin_risk_id="", report_embed_base="",
        agent_mcp_servers="",
        voiceprint_enabled=False, voiceprint_model_path="data/models/campplus.onnx",
        meeting_processing_timeout_minutes=3,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_all_off_baseline_clean():
    problems, warnings = config_check._check(_s())
    assert problems == []
    assert warnings == []


def test_asr_provider_gateway_requires_llm():
    problems, _ = config_check._check(_s(asr_provider="gateway", main_api_url=""))
    assert any("MAIN_API_URL" in p for p in problems)


def test_minio_partial_config_rejected():
    problems, _ = config_check._check(_s(minio_endpoint="x:9000"))
    assert any("MINIO" in p for p in problems)


def test_langfuse_partial_config_rejected():
    problems, _ = config_check._check(_s(langfuse_host="http://lf"))
    assert any("LANGFUSE" in p for p in problems)


def test_scms_domain_requires_credentials():
    problems, _ = config_check._check(_s(scms_nl2sql_url="http://x"))
    assert any("SCMS" in p for p in problems)
    # 服务账号齐全 → 通过
    problems, warnings = config_check._check(_s(
        scms_nl2sql_url="http://x", scms_auth_base="http://a",
        scms_login_username="u", scms_login_password="p", scms_check_basic="b",
    ))
    assert problems == [] and warnings == []
    # 仅静态 token → 放行 + 提示
    problems, warnings = config_check._check(_s(
        scms_nl2sql_url="http://x", scms_auth_base="http://a",
        scms_nl2sql_token="t", scms_check_basic="b",
    ))
    assert problems == [] and any("静态 token" in w for w in warnings)


def test_sso_and_kb_import_cross_checks():
    problems, _ = config_check._check(_s(kb_sso_base="http://kb"))
    assert any("KB_SSO_AES_KEY" in p for p in problems)
    problems, _ = config_check._check(_s(kb_import_base="http://imp"))
    assert any("KB_IMPORT" in p for p in problems)


def test_agent_mcp_servers_json_validity():
    problems, _ = config_check._check(_s(agent_mcp_servers="{bad"))
    assert any("AGENT_MCP_SERVERS" in p for p in problems)
    problems, _ = config_check._check(
        _s(agent_mcp_servers='[{"name":"kb","url":"http://x/mcp"}]'))
    assert not any("AGENT_MCP_SERVERS" in p for p in problems)


def test_voiceprint_enabled_model_missing_warns_not_fails():
    problems, warnings = config_check._check(
        _s(voiceprint_enabled=True, voiceprint_model_path="data/models/__nope.onnx"))
    assert problems == []
    assert any("声纹模型" in w for w in warnings)
