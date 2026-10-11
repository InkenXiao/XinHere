"""AI 生成 HTML 报告的读取端点：GET /html-reports/{report_id} 直出 text/html。

鉴权走标准 current_user（Authorization Bearer / ?token= query 均可，auth 中间件统一提取）；
响应直出 HTML 本体（EnvelopeRoute 对非 JSONResponse 直通不包封），
并强制 Content-Security-Policy: script-src 'none'——LLM 产物属不可信内容，
提示词约束不写 <script> 之外的第二道保险：URL 带 ?token= 鉴权，放行脚本
等于允许 prompt 注入生成的页面窃取 token 调用 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session


from app.persistence.models import SysUser
from app.persistence.session import get_db
from app.platform.api.deps import current_user

from ...services import html_reports

router = APIRouter(prefix="/html-reports", tags=["html-reports"])


@router.get("/{report_id}")
def get_report(
    report_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """读取本人 HTML 报告（不存在/越权一律 404），text/html 直出 + 禁脚本 CSP。"""
    row = html_reports.get_owned(db, report_id, user.user_id)
    return Response(
        content=html_reports.load_content(row),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "script-src 'none'",
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
        },
    )
