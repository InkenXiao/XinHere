from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.orm import Session

from ...core import errors
from ...persistence.models import BizTask, BizTodo
from ...services import cash as cash_svc
from ...services import identity as identity_svc
from ...services import report as report_svc
from ...services import risk as risk_svc
from ...services import todo as todo_svc
from ..events.store import store

logger = logging.getLogger(__name__)


def _emit(session_id: str):
    return lambda type_, payload: store.append(session_id, type_, payload)


def _ai_copy(prompt: str, fallback: str, timeout_s: float = 8.0) -> str:
    """卡处理器内的一次性 LLM 文案（限时，失败回退确定性文案，不阻塞主流程）。"""
    from concurrent.futures import ThreadPoolExecutor

    from .llm import chat_once

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(chat_once, prompt).result(timeout=timeout_s).strip() or fallback
    except Exception:
        logger.warning("AI 文案生成失败/超时（回退确定性文案）")
        return fallback


def _require_assignee(db: Session, todo_id: str, user_id: str) -> BizTodo:
    t = db.get(BizTodo, todo_id)
    if t is None:
        raise errors.not_found("待办不存在")
    if t.assignee_id != user_id:
        raise errors.forbidden("仅待办处理人本人可操作")
    return t


def handle_arrange_confirm(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """安排待办两步卡提交：落 task + 逐处理人 todo（session_id 回写，会话内留痕可追溯）。
    两步卡（props.editable）第一步在卡上填 待办描述/截止时间/处理人：values.title/due/
    assignee_labels 覆盖 props 预填；处理人标签（@名称）在提交时统一经组织目录解析。
    props.template_key 非空 → 模板小流程派发（dispatch_task 工具，走 spawn_task）。"""
    from .tools import do_dispatch

    v = values or {}
    title = str(v.get("title") if v.get("title") is not None else props.get("title") or "").strip()
    if not title:
        raise errors.validation("待办描述不能为空")
    due_raw = str(v.get("due") if v.get("due") is not None else props.get("due") or "").strip()
    due = None
    if due_raw:
        from datetime import datetime as _dt

        try:
            due = _dt.fromisoformat(due_raw)
        except ValueError:
            raise errors.validation(f"截止时间格式不合法：{due_raw}（应为 YYYY-MM-DD）")

    labels = [str(x).strip() for x in (v.get("assignee_labels") or []) if str(x).strip()]
    if labels:
        # 卡上编辑过处理人 → 按标签重新解析（@名称/姓名/角色均可；只读业务库，无需用户凭证）
        resolved = identity_svc.resolve_labels_to_users(labels)
        if resolved["unknown"]:
            raise errors.validation("未能在组织目录中识别处理人：" + "、".join(resolved["unknown"]))
        assignees = resolved["assignees"]
    else:
        assignees = props.get("assignees") or []
        if v.get("assignee_ids") is not None:
            want = {str(i) for i in v["assignee_ids"]}
            assignees = [a for a in assignees if str(a.get("user_id")) in want]
    if not assignees:
        raise errors.validation("处理人为空，无法安排")
    note = str(props.get("note") or "")
    # 模板类型可在卡第一步选择：values 覆盖 props 预填（普通待办 → None）
    template_key = str(v.get("template_key") or props.get("template_key") or "").strip() or None
    period = str(v.get("period") or props.get("period") or "").strip() or None
    summary = do_dispatch(
        db, user_id=user_id, session_id=session_id, title=title, assignees=assignees,
        due=due, review_required=bool(props.get("review_required")), note=note,
        template_key=template_key, anchor=props.get("anchor"),
        parent_task_id=props.get("parent_task_id"), period=period,
    )
    return summary, []


def handle_risk_dispatch_confirm(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    period = props["period"]
    companies = props["companies"]
    batch = risk_svc.create_batch(db, period=period, companies=companies, dispatcher_id=user_id)
    logger.info("风险填报下发 sid=%s batch=%s period=%s 企业数=%d",
                session_id, batch.batch_id, period, len(companies))
    summary = f"已下发 {len(companies)} 家被投企业风险填报任务（归属期：{period}）"
    events = [("risk/fill-start", {"batch_id": batch.batch_id, "period": period,
                                   "companies": companies, "version": 1})]
    return summary, events


def handle_cash_guarantee_form(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    if not values:
        raise errors.validation("缺少填报值")
    form_id = props["form_id"]
    cash_svc.save_draft(db, form_id, values)
    form = cash_svc.submit(db, form_id, dispatcher_id=user_id)
    lamp_txt = {"r": "红", "y": "黄", "g": "绿"}[form["lamp"]]
    logger.info("现金保障提交 sid=%s form=%s ratio=%s lamp=%s",
                session_id, form_id, form["ratio"], form["lamp"])
    summary = f"现金保障倍数 {form['ratio']}，亮{lamp_txt}灯"
    events = [("cash/form-submit", {
        "form_id": form_id,
        "values": {"avail_cash": form["avail_cash"], "pooled_fund": form["pooled_fund"],
                   "avail_credit": form["avail_credit"], "monthly_outflow": form["monthly_outflow"]},
        "ratio": form["ratio"], "lamp": form["lamp"], "summary": summary, "version": 1,
    })]
    return summary, events


def handle_report_confirm(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """报告确认组件（report-confirm）：用户选定期间/模板/企业后落库。
    投后报告：create 报告行（幂等），异步生成由工具 resume 后启动（事件顺序 submit→report-start→update→file）；
    财务风险报告：真实生成 .pptx 并产出 file/record 文件卡事件（下载走 /ppt-reports）。"""
    if not values:
        raise errors.validation("缺少确认值")
    period = str(values.get("period") or props.get("default_period") or "")
    if not period:
        raise errors.validation("请选择报告期间")
    skill = props.get("skill_key")
    template_name = next(
        (t["name"] for t in props.get("templates") or [] if t["key"] == values.get("template_key")),
        None,
    )
    if skill == "post_report":
        company_ids = values.get("company_ids") or []
        if not company_ids:
            raise errors.validation("请选择被投企业")
        r = report_svc.create(db, company_ids=company_ids, period=period,
                              report_id=props.get("report_id"))
        logger.info("投后报告确认落库 sid=%s report=%s period=%s 企业数=%d 模板=%s",
                    session_id, r.report_id, period, len(company_ids), template_name)
        summary = (f"投后管理报告生成中（归属期：{period}，{len(company_ids)} 家企业"
                   f"{f'，模板：{template_name}' if template_name else ''}），完成后将产出 Word 文档")
        return summary, []
    # fin_risk_report：仅时间+模板（设计稿无企业选择），真实生成 .pptx
    from ...services import ppt_reports as ppt_report_svc

    def _llm(prompt: str) -> str:
        from .llm import chat_once

        return chat_once(prompt)

    result = ppt_report_svc.generate_fin_risk(
        db, session_id=session_id, period=period, llm_fn=_llm)
    if "error" in result:
        raise errors.validation(result["error"])
    row = result["row"]
    events = [("file/record", {
        "file_id": str(row.report_id),
        "name": result["file_name"], "file_type": "pptx",
        "url": f"/ppt-reports/{row.report_id}/download",
        "skill_key": "fin_risk_report", "version": 1,
    })]
    logger.info("财务风险报告确认生成 PPT sid=%s period=%s report=%s 模板=%s",
                session_id, period, row.report_id, template_name)
    summary = (f"财务风险报告已生成（归属期：{period}"
               f"{f'，模板：{template_name}' if template_name else ''}），点击对话中的文件卡下载 PPT")
    return summary, events


def handle_todo_done_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """已完成卡：处理人在 AI 会话内提交完成（审核任务→submitted 等审核，否则直接 completed）。"""
    todo_id = str(props.get("todo_id") or "")
    note = str((values or {}).get("completion_text") or "")
    t = _require_assignee(db, todo_id, user_id)
    if t.status not in ("pending", "returned"):
        raise errors.validation(f"待办当前状态为「{t.status}」，不能提交完成")
    r = todo_svc.submit(db, todo_id, user_id, completion_text=note, payload=None, session_id=session_id)
    status = r["status"]
    summary = "已提交，等待派发人审核" if status == "submitted" else "已完成"
    logger.info("AI 会话完成待办 sid=%s todo=%s status=%s", session_id, todo_id, status)
    return summary, []


def handle_todo_na_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """不涉及卡：处理人填原因后关闭待办。"""
    todo_id = str(props.get("todo_id") or "")
    reason = str((values or {}).get("reason") or "").strip()
    if not reason:
        raise errors.validation("请填写不涉及原因")
    _require_assignee(db, todo_id, user_id)
    r = todo_svc.na(db, todo_id, reason)
    logger.info("AI 会话不涉及待办 sid=%s todo=%s", session_id, todo_id)
    return f"待办「{r['title']}」已标记不涉及，等待派发人确认", []


def _guard_dispatcher(db: Session, todo_id: str, user_id: str) -> BizTodo:
    t = db.get(BizTodo, todo_id)
    if t is None:
        raise errors.not_found("待办不存在")
    task = db.get(BizTask, t.task_id)
    if task is None or task.dispatcher_id != user_id:
        raise errors.forbidden("仅任务派发人可操作")
    return t


def handle_todo_review_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """逐条审核卡：decisions=[{todo_id, action: approve|reject|skip, comment}]。"""
    decisions = (values or {}).get("decisions") or []
    if not decisions:
        raise errors.validation("没有审核决定")
    ok = rej = skip = 0
    for d in decisions:
        action = str(d.get("action") or "")
        if action == "skip":
            skip += 1
            continue
        todo_id = str(d.get("todo_id") or "")
        _guard_dispatcher(db, todo_id, user_id)
        if action == "approve":
            todo_svc.review(db, todo_id, user_id, True, d.get("comment"))
            ok += 1
        elif action == "reject":
            c = str(d.get("comment") or "").strip()
            if not c:
                raise errors.validation("退回需填写退回说明")
            todo_svc.review(db, todo_id, user_id, False, c)
            rej += 1
    logger.info("逐条审核 sid=%s 通过=%d 退回=%d 跳过=%d", session_id, ok, rej, skip)
    return f"审核完成：通过 {ok} 项、退回 {rej} 项、跳过 {skip} 项", []


def handle_todo_na_review_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """逐条确认不涉及卡：decisions=[{todo_id, action: confirm|redispatch|skip, comment}]。"""
    decisions = (values or {}).get("decisions") or []
    if not decisions:
        raise errors.validation("没有确认决定")
    conf = redis = skip = 0
    for d in decisions:
        action = str(d.get("action") or "")
        if action == "skip":
            skip += 1
            continue
        todo_id = str(d.get("todo_id") or "")
        _guard_dispatcher(db, todo_id, user_id)
        if action == "confirm":
            todo_svc.na_confirm(db, todo_id)
            conf += 1
        elif action == "redispatch":
            c = str(d.get("comment") or "").strip()
            if not c:  # 规格：再次派发前须填写说明（处理人侧展示）
                raise errors.validation("再次派发须填写说明")
            todo_svc.na_reject(db, todo_id, c)
            redis += 1
    logger.info("逐条确认不涉及 sid=%s 确认=%d 再派发=%d 跳过=%d", session_id, conf, redis, skip)
    parts = [f"确认不涉及 {conf} 项"]
    if redis:
        parts.append(f"再次派发 {redis} 项")
    if skip:
        parts.append(f"跳过 {skip} 项")
    return "，".join(parts), []


def handle_todo_remind_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """逐条催办卡：todo_ids=[...]（勾选项）。催办文案由 AI 生成一句个性化提示，失败回退确定性文案。"""
    ids = (values or {}).get("todo_ids") or []
    if not ids:
        raise errors.validation("未勾选任何催办项")
    reminded = []
    for todo_id in ids:
        v = todo_svc.remind(db, str(todo_id), user_id)
        reminded.append({"title": v["title"], "assignee": v.get("assignee_id", ""),
                         "remind_count": v.get("remind_count") or 0,
                         "due": v.get("due"), "status": v["status"]})
    logger.info("逐条催办 sid=%s 数量=%d", session_id, len(ids))
    detail = "；".join(
        f"《{r['title']}》（{r['assignee']}，第 {r['remind_count']} 次催办）" for r in reminded)
    copy = _ai_copy(
        f"你是任务派发人助手。用一两句中文给以下待办的处理人生成一句友善但明确的催办提示，"
        f"点出事项名和紧迫感，不要编造截止日期之外的信息：{detail}",
        fallback=f"提醒：{detail}，请尽快处理。",
    )
    return f"已对 {len(ids)} 项待办下发催办提醒。催办文案：{copy}", []


def handle_todo_append_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """追加人员卡：task_id + assignee_ids；重复者单列（幂等跳过）。"""
    task_id = str((values or {}).get("task_id") or props.get("task_id") or "")
    ids = [str(i) for i in (values or {}).get("assignee_ids") or []]
    if not task_id or not ids:
        raise errors.validation("缺少任务或追加人员")
    r = todo_svc.append_assignees(db, task_id, user_id, ids)
    skipped_names = identity_svc.user_names(r["skipped"])
    created_names = identity_svc.user_names([t["assignee_id"] for t in r["created"]])
    parts = []
    if created_names:
        parts.append("已追加：" + "、".join(sorted(created_names.values())))
    if skipped_names:
        parts.append("已在任务中、未重复派发：" + "、".join(sorted(skipped_names.values())))
    logger.info("追加承办人 sid=%s task=%s 新增=%d 跳过=%d", session_id, task_id, len(r["created"]), len(r["skipped"]))
    return "；".join(parts) or "没有可追加的人员", []


def handle_todo_end_card(
    db: Session, *, session_id: str, user_id: str, props: dict, values: dict | None
) -> tuple[str, list[tuple[str, dict]]]:
    """结束卡：task_id + note + force（未完成清单须显式勾选强制）。
    结束后生成 AI 一句话闭环周期摘要，追加为任务备注（失败回退确定性统计摘要）。"""
    from ...persistence.models import TaskRemark

    v = values or {}
    task_id = str(v.get("task_id") or props.get("task_id") or "")
    if not task_id:
        raise errors.validation("缺少任务")
    r = todo_svc.end_task(db, task_id, user_id, note=str(v.get("note") or ""), force=bool(v.get("force")))
    logger.info("结束任务 sid=%s task=%s force=%s", session_id, task_id, bool(v.get("force")))
    # 闭环摘要：周期留痕数据 → AI 一句话总结（append-only TaskRemark 留痕）
    from sqlalchemy import select as _select

    stats: dict[str, int] = {}
    for t in db.scalars(_select(BizTodo.status).where(BizTodo.task_id == task_id)).all():
        stats[t] = stats.get(t, 0) + 1
    total = sum(stats.values())
    done = stats.get("completed", 0) + stats.get("na_closed", 0)
    board_desc = "、".join(f"{s} {n}" for s, n in sorted(stats.items()))
    summary = _ai_copy(
        f"你是任务管理助手。任务「{r['title']}」已闭环：共 {total} 项待办（{board_desc}）。"
        f"用一句中文总结本次任务周期的完成情况，客观简洁，不编造细节。",
        fallback=f"任务闭环：共 {total} 项待办，完成/关闭 {done} 项，其余按说明提前结束。",
    )
    db.add(TaskRemark(task_id=task_id, content=f"[闭环摘要] {summary}", author_id=user_id))
    return f"任务「{r['title']}」已结束。闭环摘要：{summary}", []


# kind → submit 业务落库处理器（双写铁律：事件→业务→resume，由调用方编排）
# 插件 kind 经 register() 扩展点在 discover 导入期注册（不写死在此）
HANDLERS: dict[str, Callable] = {
    "risk-dispatch-confirm": handle_risk_dispatch_confirm,
    "cash-guarantee-form": handle_cash_guarantee_form,
    "report-confirm": handle_report_confirm,
    "arrange-confirm": handle_arrange_confirm,
    "todo-done-card": handle_todo_done_card,
    "todo-na-card": handle_todo_na_card,
    "todo-review-card": handle_todo_review_card,
    "todo-na-review-card": handle_todo_na_review_card,
    "todo-remind-card": handle_todo_remind_card,
    "todo-append-card": handle_todo_append_card,
    "todo-end-card": handle_todo_end_card,
}


def register(kind: str, handler: Callable) -> None:
    """插件卡处理器注册扩展点（插件 backend/__init__ 在 discover 导入期调用）。
    幂等：同函数重复注册（模块重执行）不报错；不同函数抢占同名 kind fail-loud。"""
    existed = HANDLERS.get(kind)
    if existed is handler:
        return
    if existed is not None:
        raise RuntimeError(f"组件 kind 处理器重复注册: {kind}")
    HANDLERS[kind] = handler


def apply_update_draft(db: Session, *, kind: str, props: dict, draft: dict) -> tuple[str, dict] | None:
    """组件草稿 update → 对应 *-field-update 事件 payload；不落业务库为 None。"""
    if kind == "cash-guarantee-form":
        for k in ("avail_cash", "pooled_fund", "avail_credit", "monthly_outflow"):
            if k not in draft:
                raise errors.validation(f"草稿缺字段 {k}")
        payload = {
            "form_id": props["form_id"],
            "draft": {k: float(draft[k]) for k in ("avail_cash", "pooled_fund", "avail_credit", "monthly_outflow")},
            "version": 1,
        }
        return "cash/form-field-update", payload
    return None
