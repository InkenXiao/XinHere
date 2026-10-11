"""SCMS 里程碑审核代理：前端审核卡的动作通道（组件不直连 SCMS，凭证不出服务端）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from pydantic import BaseModel

from ...core import errors
from ...core.context import get_ctx
from .. import scms as scms_client
from .deps import current_user

router = APIRouter(prefix="/scms", tags=["scms"])
logger = logging.getLogger(__name__)

PROOF_ZH = {
    "系统截图": "系统截图", "合同台账清单": "合同台账清单", "企业盖章说明": "企业盖章说明",
    "主管部门盖章文件": "主管部门盖章文件", "验收报告": "验收报告", "转账凭证": "转账凭证",
    "产权交割凭证": "产权交割凭证", "决议文件": "决议文件", "其他": "其他",
}
STATUS_ZH = {"0": "未开始", "1": "进行中", "2": "已完成", "3": "已延期", "4": "异常"}
AUDIT_ZH = {"0": "待审核", "1": "已退回", "2": "已通过"}


def _row_view(m: dict) -> dict:
    return {
        "id": str(m.get("id") or ""),
        "milestone_name": str(m.get("milestoneName") or ""),
        "due_date": str(m.get("dueDate") or ""),
        "proof": str(m.get("milestoneDesc") or ""),
        "status": str(m.get("status") or "0"),
        "audit_status": str(m.get("baseAuditStatus") or "0"),
    }


class MilestonesOut(BaseModel):
    rule_id: str
    rows: list[dict]


@router.get("/milestones")
def milestones(rule_id: str, audit_status: str = "0", user=Depends(current_user)):
    """审核卡刷新数据：某指标下指定审核状态的里程碑。"""
    try:
        rows = scms_client.milestone_page(rule_id, audit_status)
    except Exception as exc:
        raise errors.validation(f"SCMS 里程碑查询失败：{exc}")
    return {"rule_id": rule_id, "rows": [_row_view(m) for m in rows]}


class AuditIn(BaseModel):
    rule_id: str
    action: str  # approve / reject
    reason: str = ""  # 退回原因（落备注）
    milestone_ids: list[str] | None = None  # 空=该指标全部待审核行


@router.post("/milestones/audit")
def milestone_audit(body: AuditIn, user=Depends(current_user)):
    """审核动作：approve → baseAuditStatus=2；reject → baseAuditStatus=1 + 退回原因落备注。"""
    if body.action not in ("approve", "reject"):
        raise errors.validation("action 必须是 approve 或 reject")
    if body.action == "reject" and not body.reason.strip():
        raise errors.validation("退回必须填写原因")
    try:
        pending = scms_client.milestone_page(body.rule_id, "0")
    except Exception as exc:
        raise errors.validation(f"SCMS 里程碑查询失败：{exc}")
    targets = [m for m in pending
               if not body.milestone_ids or str(m.get("id")) in body.milestone_ids]
    if not targets:
        raise errors.validation("没有待审核的里程碑")
    status = "2" if body.action == "approve" else "1"
    rows = []
    for m in targets:
        r = dict(m)
        r["baseAuditStatus"] = status
        r["baseAuditUserId"] = user.user_id
        r["baseAuditUserName"] = user.username
        rows.append(r)
    try:
        scms_client.milestone_update(rows, operator=user.username)
        if body.action == "reject":
            for m in targets:
                scms_client.milestone_remark_create(
                    str(m.get("id")), f"审核退回：{body.reason}", author=user.username,
                )
    except Exception as exc:
        logger.warning("里程碑审核提交失败 rule=%s action=%s err=%r", body.rule_id, body.action, exc)
        raise errors.validation(f"SCMS 审核提交失败：{exc}")
    logger.info("里程碑审核完成 rule=%s action=%s 行数=%d user=%s",
                body.rule_id, body.action, len(rows), user.username)
    return {
        "ok": True,
        "action": body.action,
        "count": len(rows),
        "summary": f"已{'通过' if body.action == 'approve' else '退回'} {len(rows)} 条里程碑",
    }
