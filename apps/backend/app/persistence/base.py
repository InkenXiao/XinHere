from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """平台表基类（无审计字段，append-only）。"""


class BusinessBase(Base):
    """业务表基类：红线1 逻辑删除 + 红线2 审计四件套。

    updated_at 由 DB 触发器 set_updated_at() 维护；created_by/updated_by
    由 session 工厂 before_flush 自动填充。
    """

    __abstract__ = True

    is_delete = Column(Boolean, nullable=False, default=False, server_default="false")
    created_by = Column(String(64), nullable=False, default="system", server_default="system")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_by = Column(String(64), nullable=False, default="system", server_default="system")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
