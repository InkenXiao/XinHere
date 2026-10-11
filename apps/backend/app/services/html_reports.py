"""AI 生成 HTML 报告（对话工具 generate_html_report 的领域服务）。

agent 工具内同步调 chat_once 生成单文件完整 HTML（内联 CSS、无脚本、无外部依赖），
落 html_reports 表（content 为 Text 列）并同时写对象存储（MinIO，未配置降级本地磁盘）；
读取走 GET /html-reports/{report_id} 直出 text/html + CSP 禁脚本响应头
（正文优先取对象存储，缺对象回退 DB 列）。

安全基线：LLM 产物属不可信内容，提示词约束不写 <script>，响应端 CSP: script-src 'none'
双保险——URL 带 ?token= 鉴权，放行脚本等于允许 prompt 注入窃取 token 调 API。"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from ..core import errors
from ..persistence.models import HtmlReport
from ..platform.agent.llm import chat_once

logger = logging.getLogger(__name__)

# 生成提示词：约束单文件、无脚本、中文商务风
_PROMPT_TMPL = """你是一名企业级报告排版专家。请生成一份完整的单文件 HTML 报告，要求：

1. 主题：{title}
2. 内容要求：{requirement}
3. 技术约束（必须全部遵守）：
   - 输出 <!DOCTYPE html> 开始、</html> 结束的完整文档，只输出 HTML 本身，不要 markdown 围栏、不要任何解释文字
   - 所有 CSS 写在 <style> 标签内，禁止引用任何外部资源（图片/字体/CSS/JS 一律不允许）
   - 严禁使用 <script> 标签、on* 内联事件、javascript: 协议——页面必须纯静态
   - 中文商务风格：白底、留白充足、层级清晰；标题/正文/表格/要点列表排版规整，配色克制（主色可取深蓝或墨绿）
   - 内容基于需求如实撰写，结构完整（可含摘要、章节、数据表、结论），总量控制在 3000 字以内
   - lang="zh-CN"，viewport 适配
"""


def generate_html(title: str, requirement: str) -> str:
    """调 LLM 生成完整 HTML 文档，剥离可能的 markdown 围栏，失败/异常内容时报错。"""
    prompt = _PROMPT_TMPL.format(title=title, requirement=requirement or "围绕主题撰写一份结构完整、内容充实的报告")
    raw = chat_once(prompt)
    html = _strip_fence(raw)
    if not html:
        raise errors.upstream("HTML 生成结果为空，请稍后重试")
    return html


def _strip_fence(raw: str) -> str:
    """剥离 ```html 围栏与围栏外杂文字：截取 <!DOCTYPE 或 <html 到 </html>。"""
    low = raw.lower()
    start = min(
        (i for i in (low.find("<!doctype"), low.find("<html")) if i >= 0),
        default=-1,
    )
    if start < 0:
        return ""
    end = low.rfind("</html>")
    return raw[start:] if end < 0 else raw[start:end + len("</html>")]


def create_report(db: Session, *, session_id: str | None, title: str, html: str) -> HtmlReport:
    """落库一条报告记录。created_by 由 session 工厂 before_flush 从 AuditCtx 自动填充（即所有权）。"""
    sid = None
    if session_id:
        try:
            sid = uuid.UUID(str(session_id))
        except (ValueError, TypeError):
            sid = None  # 非 UUID 会话标识（异常场景）容错置空
    row = HtmlReport(title=title, session_id=sid, content=html)
    db.add(row)
    db.flush()
    return row


def artifact_key(report_id: str) -> str:
    """报告正文对象键：与 DB 行 id 一一对应，读端点优先取对象存储、缺对象回退 DB 列。"""
    return f"html-reports/{report_id}.html"


def store_artifact(row: HtmlReport) -> None:
    """正文写对象存储（MinIO 未配置时由 storage 降级本地磁盘）。

    失败不抛：DB 列仍是完整事实源，读端点会回退，工具链不因存储抖动中断。"""
    try:
        from . import storage

        storage.put(artifact_key(str(row.report_id)), row.content.encode("utf-8"), "text/html; charset=utf-8")
    except Exception:
        logger.exception("HTML 报告对象存储写入失败 report_id=%s（读时回退 DB 列）", row.report_id)


def load_content(row: HtmlReport) -> str:
    """读正文：优先对象存储（正文权威存放处），缺失/读取失败回退 DB 列。"""
    try:
        from . import storage

        return storage.read(artifact_key(str(row.report_id))).decode("utf-8")
    except Exception:
        logger.exception("HTML 报告对象存储读取失败 report_id=%s（回退 DB 列）", row.report_id)
        return row.content


def get_owned(db: Session, report_id: str, user_id: str) -> HtmlReport:
    """读取本人报告：不存在或不归属一律 404（不泄露存在性）。"""
    row = db.get(HtmlReport, report_id)
    if row is None or row.created_by != user_id:
        raise errors.not_found("报告不存在")
    return row
