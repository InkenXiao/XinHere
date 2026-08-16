from __future__ import annotations

import uuid

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from ..core.config import settings
from ..core.context import get_ctx
from ..platform.audit.log import scrub
from .base import BusinessBase
from .models import PlatformOperationLog

engine = create_engine(settings.database_url, pool_size=10, max_overflow=20, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)

# 是否处于“已接管执行”标记键（do_orm_execute 内 invoke_statement 防递归）
_EXEC_FLAG = "_xinhere_executed"


def _is_business_entity(mapper) -> bool:
    try:
        return issubclass(mapper.class_, BusinessBase)
    except TypeError:
        return False


def _business_entities_of(stmt) -> list[type]:
    """提取 select 语句涉及的 BusinessBase 实体。"""
    found: list[type] = []
    try:
        for cd in stmt.column_descriptions:
            ent = cd.get("entity")
            if ent is not None and isinstance(ent, type) and issubclass(ent, BusinessBase):
                found.append(ent)
    except Exception:
        pass
    return found


@event.listens_for(Session, "do_orm_execute")
def _on_orm_execute(orm_execute_state):
    stmt = orm_execute_state.statement

    # 红线 1：select 默认过滤 is_delete=false
    if (
        orm_execute_state.is_select
        and not orm_execute_state.execution_options.get("include_deleted", False)
    ):
        stmt = stmt.options(
            with_loader_criteria(
                BusinessBase,
                lambda cls: cls.is_delete.is_(False),
                include_aliases=True,
            )
        )
        orm_execute_state.statement = stmt

    # 红线 3：业务表 select 落操作日志（条件摘要 + 行数）
    if (
        orm_execute_state.is_select
        and not orm_execute_state.execution_options.get(_EXEC_FLAG, False)
        and not orm_execute_state.is_column_load
        and not orm_execute_state.is_relationship_load
    ):
        entities = _business_entities_of(stmt)
        if not entities:
            return
        try:
            criteria = str(stmt.compile(compile_kwargs={"literal_binds": False}))[:400]
        except Exception:
            criteria = "<uncompilable>"
        result = orm_execute_state.invoke_statement(
            orm_execute_state.statement, execution_options={_EXEC_FLAG: True}
        )
        frozen = result.freeze()
        try:
            rows = len(frozen().all())
        except Exception:
            rows = -1
        ctx = get_ctx()
        log = PlatformOperationLog(
            user_id=ctx.user_id,
            session_id=uuid.UUID(ctx.session_id) if ctx.session_id else None,
            channel=ctx.channel,
            actor=ctx.actor,
            entity=",".join(sorted({e.__tablename__ for e in entities})),
            operation="select",
            record_key=None,
            detail=scrub({"criteria": criteria, "rows": rows, **ctx.detail_context}),
            client_ip=ctx.client_ip,
            entry_point=ctx.entry_point,
            request_id=ctx.request_id,
        )
        orm_execute_state.session.add(log)
        return frozen()
    return None


def _record_key(obj) -> str | None:
    """优先取主键列值（before_flush 阶段 identity 尚未生成）。"""
    state = inspect(obj)
    if state.identity:
        return "|".join(str(v) for v in state.identity)
    pks = state.mapper.primary_key
    if len(pks) == 1:
        v = getattr(obj, pks[0].key, None)
        return str(v) if v is not None else None
    vals = [str(getattr(obj, p.key, "")) for p in pks]
    return "|".join(vals) if any(vals) else None


@event.listens_for(Session, "before_flush")
def _on_before_flush(session, flush_context, instances):
    ctx = get_ctx()
    logs: list[PlatformOperationLog] = []

    for obj in session.new:
        if not isinstance(obj, BusinessBase):
            continue
        # 红线 2：审计字段自动填充
        if not obj.created_by or obj.created_by == "system":
            obj.created_by = ctx.user_id
        obj.updated_by = ctx.user_id
        state = inspect(obj)
        new_vals = {
            a.key: getattr(obj, a.key)
            for a in state.mapper.column_attrs
            if a.key not in ("created_at", "updated_at")
        }
        logs.append(
            PlatformOperationLog(
                user_id=ctx.user_id,
                session_id=uuid.UUID(ctx.session_id) if ctx.session_id else None,
                channel=ctx.channel,
                actor=ctx.actor,
                entity=obj.__tablename__,
                operation="insert",
                record_key=_record_key(obj),
                detail=scrub({"new": new_vals, **ctx.detail_context}),
                client_ip=ctx.client_ip,
                entry_point=ctx.entry_point,
                request_id=ctx.request_id,
            )
        )

    for obj in session.dirty:
        if not isinstance(obj, BusinessBase) or not session.is_modified(obj, include_collections=False):
            continue
        obj.updated_by = ctx.user_id
        state = inspect(obj)
        changes = {}
        for attr in state.attrs:
            hist = attr.load_history()
            if not hist.has_changes():
                continue
            old = hist.deleted[0] if hist.deleted else None
            new = attr.value
            if old is None and new is None:
                continue
            changes[attr.key] = {"old": old, "new": new}
        if not changes:
            continue
        logs.append(
            PlatformOperationLog(
                user_id=ctx.user_id,
                session_id=uuid.UUID(ctx.session_id) if ctx.session_id else None,
                channel=ctx.channel,
                actor=ctx.actor,
                entity=obj.__tablename__,
                operation="update",
                record_key=_record_key(obj),
                detail=scrub({"changes": changes, **ctx.detail_context}),
                client_ip=ctx.client_ip,
                entry_point=ctx.entry_point,
                request_id=ctx.request_id,
            )
        )

    for log in logs:
        session.add(log)


def get_db():
    """FastAPI 依赖：请求级 session，结束统一 commit（含操作日志）。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def new_session() -> Session:
    return SessionLocal()
