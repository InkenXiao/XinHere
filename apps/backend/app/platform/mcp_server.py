"""MCP 头（P4）：fastmcp 同进程挂载 /mcp，供外部 agent 客户端经标准 MCP 协议读写任务平台。

- auth 覆盖：全局 auth_and_ctx 中间件先验 Bearer（fail-closed，/mcp 不在豁免清单）；
  工具内再经 get_http_request 取当前请求头二次校验并解析身份（不依赖中间件 contextvar
  传播——fastmcp 工具可能在独立任务中执行）。
- 纯读工具 + propose/commit 写工具（arrange 幂等键防重）；对话内的薄工具只出确认卡
  （platform/agent/tools.py），两条通道不混用。
- 输入输出用业务服务层 dict（task_view/todo_view/board），与 /api/v1 同源。
"""

from __future__ import annotations

import logging
import time
import uuid

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from sqlalchemy import select

from ..persistence.models import BizTask, BizTodo
from ..persistence.session import SessionLocal
from ..services import artifacts as artifact_svc
from ..services import identity as identity_svc
from ..services import todo as todo_svc
from .scms import token_auth
logger = logging.getLogger(__name__)

mcp = FastMCP("xinhere-tasks")

# arrange commit 幂等键缓存（v1 进程内，TTL 30min；重启仅影响去重窗口）
_COMMITS: dict[str, tuple[float, str]] = {}
_COMMIT_TTL = 1800.0


def _current_user_id() -> str:
    """MCP 工具身份：当前 HTTP 请求的 Bearer → check-token（TTL 缓存）→ SCMS 用户 id。"""
    request = get_http_request()
    auth = (request.headers.get("authorization") or "").strip()
    utk = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    ident = token_auth.check_token(utk) if utk else None
    if ident is None:
        raise PermissionError("登录已失效，请重新登录（Authorization: Bearer <token>）")
    return ident.user_id


def _check_user(todo: BizTodo, user_id: str, task: BizTask | None) -> None:
    """读权限：处理人或派发人任一即可（派发人还能看板/催办/结束）。"""
    if todo.assignee_id != user_id and (task is None or task.dispatcher_id != user_id):
        raise PermissionError("无权访问该待办")


# ---------------- 纯读工具 ----------------


@mcp.tool
def list_tasks(box: str = "dispatcher", limit: int = 20) -> list[dict]:
    """列出与我相关的任务/待办。box=dispatcher：我派发的进行中任务（task_view 列表）；
    box=assignee：我名下进行中的待办（todo_view 列表）。"""
    user_id = _current_user_id()
    with SessionLocal() as db:
        if box == "assignee":
            rows = db.scalars(
                select(BizTodo)
                .where(BizTodo.assignee_id == user_id, BizTodo.status != "na_closed")
                .order_by(BizTodo.created_at.desc())
                .limit(max(1, min(limit, 100)))
            ).all()
            return [todo_svc.todo_view(db, t) for t in rows]
        rows = db.scalars(
            select(BizTask)
            .where(BizTask.dispatcher_id == user_id, BizTask.status == "open")
            .order_by(BizTask.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        return [todo_svc.task_view(db, t) for t in rows]


@mcp.tool
def get_board(task_id: str) -> dict:
    """任务看板（派发人）：状态统计 + 各状态明细（todo_id/处理人/完成说明等）。"""
    user_id = _current_user_id()
    with SessionLocal() as db:
        return todo_svc.board(db, task_id, user_id)


@mcp.tool
def get_todo_detail(todo_id: str) -> dict:
    """待办详情：视图字段 + 表单 payload + 附件清单（处理人或派发人）。"""
    user_id = _current_user_id()
    with SessionLocal() as db:
        t = db.get(BizTodo, todo_id)
        if t is None:
            raise LookupError("待办不存在")
        task = db.get(BizTask, t.task_id)
        _check_user(t, user_id, task)
        v = todo_svc.todo_view(db, t, include_payload=True)
        v["na_reason"] = t.na_reason
        v["artifacts"] = artifact_svc.list_by_todo(db, todo_id, user_id)
        return v


@mcp.tool
def search_directory(q: str = "", types: list[str] | None = None, limit: int = 20) -> dict:
    """组织目录搜索（@ 目标解析用）：user/role/dept/company/group 五类，返回 {类型: [{id,label,extra}]}。"""
    _current_user_id()
    return identity_svc.search_directory(q, types, max(1, min(limit, 50)))


# ---------------- propose/commit 写工具（幂等） ----------------


@mcp.tool
def arrange_task_propose(
    title: str,
    assignees: list[str],
    due: str | None = None,
    note: str | None = None,
    review_required: bool = False,
) -> dict:
    """安排待办第一步（propose）：解析 @ 处理人（名称列表 → user_id），返回提交载荷但不落库。
    拿到结果后调用 arrange_task_commit（原样传 assignee_ids 等字段 + 幂等键 idempotency_key）执行。"""
    _current_user_id()
    resolved = identity_svc.resolve_labels_to_users(assignees)
    if not resolved["assignees"]:
        raise ValueError(f"未能解析到处理人：{'、'.join(resolved['unknown']) or '（空）'}")
    return {
        "title": title,
        "assignee_ids": [a["user_id"] for a in resolved["assignees"]],
        "assignee_names": [a["name"] for a in resolved["assignees"]],
        "unknown": resolved["unknown"],
        "due": due,
        "note": note,
        "review_required": review_required,
    }


@mcp.tool
def arrange_task_commit(
    title: str,
    assignee_ids: list[str],
    idempotency_key: str | None = None,
    due: str | None = None,
    note: str | None = None,
    review_required: bool = False,
) -> dict:
    """安排待办第二步（commit，幂等）：同 idempotency_key 重复调用只落库一次，返回首次结果。
    建议键：客户端生成的 uuid。due=YYYY-MM-DD。"""
    user_id = _current_user_id()
    key = idempotency_key or uuid.uuid4().hex
    now = time.monotonic()
    for k in [k for k, (exp, _) in _COMMITS.items() if exp < now]:
        _COMMITS.pop(k, None)
    hit = _COMMITS.get(key)
    if hit is not None:
        return {"duplicated": True, "summary": hit[1]}
    from datetime import datetime

    with SessionLocal() as db:
        task = todo_svc.create_task(
            db, scene="generic", title=title, dispatcher_id=user_id,
            payload={"content": note or "", "via": "mcp"},
            review_required=review_required,
            due=datetime.fromisoformat(due) if due else None,
        )
        for uid in dict.fromkeys(assignee_ids):  # 去重保持顺序
            todo_svc.create_todo(
                db, task=task, assignee_id=uid, kind="action", title=title,
                sub=note or "来自 MCP 安排", ref={"source": "mcp_arrange", "content": note or ""},
                due=datetime.fromisoformat(due) if due else None,
            )
        summary = f"已安排待办「{title}」给 {len(set(assignee_ids))} 人（task_id={task.task_id}）"
    _COMMITS[key] = (now + _COMMIT_TTL, summary)
    logger.info("MCP 安排待办 task=%s 处理人数=%d key=%s", task.task_id, len(set(assignee_ids)), key[:8])
    return {"duplicated": False, "task_id": task.task_id, "summary": summary}


@mcp.tool
def remind_todo(todo_id: str) -> dict:
    """催办一条待办（派发人；仅 pending/returned 可催）。"""
    user_id = _current_user_id()
    with SessionLocal() as db:
        return todo_svc.remind(db, todo_id, user_id)


@mcp.tool
def append_assignees(task_id: str, assignee_ids: list[str]) -> dict:
    """给进行中任务追加承办人（派发人）；已在任务中者单列返回、不重复派发。"""
    user_id = _current_user_id()
    with SessionLocal() as db:
        return todo_svc.append_assignees(db, task_id, user_id, assignee_ids)


@mcp.tool
def end_task(task_id: str, note: str | None = None, force: bool = False) -> dict:
    """结束任务（派发人）：全部完成可结束；存在未完成须 force=True（留痕未完成清单+结束说明）。"""
    user_id = _current_user_id()
    with SessionLocal() as db:
        return todo_svc.end_task(db, task_id, user_id, note=note, force=force)


mcp_app = mcp.http_app(path="/")  # 挂载点 /mcp（main.py app.mount）
