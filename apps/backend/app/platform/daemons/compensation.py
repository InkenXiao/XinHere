"""补偿闭环守护（P6）：组件提交业务落库失败的幂等重放。

对应 sessions._do_component_submit 的补偿写入（业务落库异常 → PlatformCompensation
pending 留痕）。重放语义与原提交一致且防重：

- 只补业务落库（调 component_handlers.HANDLERS[kind]），**不 resume 图**——
  component/submit 事件与 resume 在原请求已照常完成，缺的只是业务数据；
- **确定性幂等键** ``{kind}:{session_id}:{seq}``（PlatformCompensation.idem_key）：
  重放时透传给声明了 ``idem_key`` 形参的业务处理器做唯一约束/upsert；
- **状态机**：pending →（replaying 标记先行落库）→ done / pending(retry+1) /
  needs_manual_review / failed。``replaying`` 先于 handler 执行落库——进程在
  "业务已写、标记未写"窗口崩溃后，残留 replaying 记录下一轮升级为
  needs_manual_review，**绝不盲目重放**；
- **在途未知（in-doubt）**：超时类异常 → 业务结果不可知 → 直接
  needs_manual_review（无只读确认探针前禁止自动重试）；
- 退避窗口 = created_at + 2^retry_count 分钟，无 schema 迁移；
  多副本下由 DB 租约互斥保证单执行方。
"""
from __future__ import annotations

import inspect
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.context import AuditCtx, reset_ctx, set_ctx
from ...persistence.models import PlatformCompensation, PlatformSession
from ...persistence.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_RETRY = 5  # 重放上限：超过转 failed（人工介入）
_INTERVAL = 60.0  # 扫描周期（秒）
_thread: threading.Thread | None = None


def _due_at(row: PlatformCompensation) -> datetime:
    """第 n 次重放的最早时刻：created_at + 2^n 分钟（指数退避，无 schema 变更）。"""
    delay_min = 2 ** min(row.retry_count or 0, 6)  # 上限 64 分钟，防止溢出
    return row.created_at + timedelta(minutes=delay_min)


def _in_doubt(exc: Exception) -> bool:
    """在途未知判定：超时类异常 → 业务侧是否落库成功不可知。"""
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def _wants_idem_key(handler) -> bool:
    """处理器声明了 idem_key 形参 → 重放透传幂等键（业务侧做唯一约束/upsert）。"""
    try:
        return "idem_key" in inspect.signature(handler).parameters
    except (TypeError, ValueError):  # noqa: BLE001 - 内置/C 处理器无签名
        return False


def replay_once(db: Session, owner: str) -> int:
    """单轮扫描：重放到期的 pending 补偿 + 回收残留 replaying，返回本轮处理条数。"""
    rows = db.scalars(
        select(PlatformCompensation)
        .where(PlatformCompensation.status.in_(["pending", "replaying"]))
        .order_by(PlatformCompensation.id)
        .limit(50)
    ).all()
    now = datetime.now(timezone.utc)
    processed = 0
    for row in rows:
        # 残留 replaying：上次重放在"业务已写、标记未写"窗口崩溃，结果未知 → 人工
        if row.status == "replaying":
            row.status = "needs_manual_review"
            db.commit()
            logger.warning("[compensation] 残留 replaying（上次重放中断），转 needs_manual_review id=%s", row.id)
            processed += 1
            continue
        if _due_at(row) > now:
            continue
        processed += 1
        kind = row.plugin_name
        from ..agent.component_handlers import HANDLERS

        handler = HANDLERS.get(kind)
        if handler is None:
            # 插件已卸载/内置卡缺失：不可重放，转 failed 留痕人工介入
            row.status = "failed"
            logger.warning("[compensation] 处理器缺失，转 failed id=%s kind=%s", row.id, kind)
            db.commit()
            continue
        session = db.get(PlatformSession, row.session_id)
        if session is None:
            row.status = "failed"
            logger.warning("[compensation] 会话不存在，转 failed id=%s sid=%s", row.id, row.session_id)
            db.commit()
            continue
        # in-flight 标记先行落库：进程在 handler 执行中崩溃 → 残留 replaying → 下轮 in-doubt
        row.status = "replaying"
        db.commit()
        payload = row.payload or {}
        # OTel：补偿重放独立 span（compensation_kind / retry_count / result），可观测重放行为
        from ..agent import otel

        with otel.compensation_span(session_id=str(row.session_id), kind=kind,
                                    retry_count=row.retry_count or 0,
                                    idem_key=row.idem_key or "") as obs:
            audit = AuditCtx(
                user_id=session.user_id, channel="tool",
                actor=f"{settings.main_model}+compensation_replayer",
                session_id=str(row.session_id), request_id=uuid.uuid4().hex[:16],
                entry_point=f"compensation:{kind}",
                detail_context={"context": {
                    "compensation_id": row.id, "owner": owner, "idem_key": row.idem_key,
                    "component_id": payload.get("component_id"),
                    "retry_count": row.retry_count,
                }},
            )
            token = set_ctx(audit)
            try:
                kwargs = dict(
                    session_id=str(row.session_id), user_id=session.user_id,
                    props=payload.get("props") or {}, values=payload.get("values") or {},
                )
                if _wants_idem_key(handler):
                    kwargs["idem_key"] = row.idem_key
                summary, _biz = handler(db, **kwargs)
                row.status = "done"
                db.commit()
                obs.set_attr("result", "done")
                logger.info("[compensation] 重放成功 id=%s kind=%s key=%s summary=%.80s",
                            row.id, kind, row.idem_key, summary)
            except Exception as exc:
                db.rollback()
                db.refresh(row)  # 回卷后从 DB 重载（replaying 已是持久态）
                if _in_doubt(exc):
                    # 超时类：业务落库可能已成功 → 禁止盲目重试，人工确认后处置
                    row.status = "needs_manual_review"
                    obs.set_attr("result", "needs_manual_review")
                    logger.error("[compensation] 重放超时（在途未知），转 needs_manual_review "
                                 "id=%s kind=%s key=%s err=%r", row.id, kind, row.idem_key, exc)
                else:
                    row.retry_count += 1
                    if row.retry_count >= MAX_RETRY:
                        row.status = "failed"
                        logger.error("[compensation] 重放达上限 %d 次，转 failed id=%s kind=%s err=%r",
                                     row.retry_count, row.id, kind, exc)
                    else:
                        row.status = "pending"
                        logger.warning("[compensation] 重放失败（第 %d 次），留 pending id=%s kind=%s err=%r",
                                       row.retry_count, row.id, kind, exc)
                db.commit()
            finally:
                reset_ctx(token)
    return processed


def start_replayer(interval: float = _INTERVAL) -> None:
    """启动扫描线程（由 main._guarded_daemon 装配：租约互斥由外层负责）。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                with SessionLocal() as db:
                    replay_once(db, owner="replayer")
            except Exception:  # noqa: BLE001 - 单轮异常不拖垮主进程
                logger.exception("[compensation] 扫描轮异常")

    _thread = threading.Thread(target=_loop, name="daemon-compensation-replayer", daemon=True)
    _thread.start()
    logger.info("[compensation] 补偿重放守护已启动 interval=%.0fs", interval)
