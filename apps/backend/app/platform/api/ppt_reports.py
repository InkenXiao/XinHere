"""AI 生成 PPT 报告的下载端点：GET /ppt-reports/{report_id}/download 直出 .pptx。

鉴权走标准 current_user（Authorization Bearer / ?token= query 均可，auth 中间件统一提取）；
正文为对象存储权威二进制（无 DB 回退），EnvelopeRoute 对非 JSONResponse 直通不包封。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session


from app.persistence.models import SysUser
from app.persistence.session import get_db
from app.platform.api.deps import current_user

from ...services import ppt_reports

router = APIRouter(prefix="/ppt-reports", tags=["ppt-reports"])

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@router.get("/{report_id}/download")
def download_report(
    report_id: str,
    user: SysUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """下载本人 PPT 报告（不存在/越权一律 404），attachment 直出（RFC 5987 中文文件名）。"""
    row = ppt_reports.get_owned(db, report_id, user.user_id)
    data = ppt_reports.read_bytes(row)
    return Response(
        content=data,
        media_type=_PPTX_MIME,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.file_name)}",
            "Cache-Control": "private, no-store",
        },
    )
