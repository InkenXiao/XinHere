from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from ...persistence.base import BusinessBase


def _dyn_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class DynamicTemplate(BusinessBase):
    """动态填报模板表（解析 Excel 产物 + 渲染 HTML 双份缓存）。"""

    __tablename__ = "dynamic_template"

    template_id = Column(
        String(64), primary_key=True, default=lambda: _dyn_id("tpl"),
        comment="动态模板 ID（前缀 tpl_ + uuid hex）",
    )
    name = Column(String(128), nullable=False, comment="模板名称（派发下拉展示）")
    source_file_name = Column(String(255), comment="原始 Excel 文件名")
    source_artifact_id = Column(String(64), comment="原始附件 artifact_id（/artifacts/{id}）")
    parse_result_json = Column(
        JSONB, nullable=False, default=dict,
        comment="Excel 解析结构化结果：{sheet_name, columns:[{key,label,type,required,options}], sample_rows:[]}",
    )
    form_html = Column(
        Text, nullable=False, default="",
        comment="填报页 HTML（Jinja2 渲染后字符串缓存；可重渲染刷新）",
    )
    summary_html = Column(
        Text, nullable=False, default="",
        comment="汇总预览 HTML（上传确认页用，只读字段列表）",
    )
    visibility = Column(
        String(16), nullable=False, default="system", server_default="system",
        comment="可见性：system=系统级共享(本部财务可见) / private=仅创建者可见",
    )
    owner_id = Column(
        String(64), nullable=False, default="system", server_default="system",
        comment="创建者用户 ID",
    )
    status = Column(
        String(16), nullable=False, default="draft", server_default="draft",
        comment="状态：draft=草稿（用户未激活）/ active=启用（出现在派发下拉）/ disabled=停用",
    )

    __table_args__ = (
        Index("ix_dyn_tpl_status_visibility", "status", "visibility"),
        {"comment": "动态填报模板：Excel 解析 + HTML 双缓存，派发下拉动态项源"},
    )


class DynamicFillData(BusinessBase):
    """动态填报数据表：每个承办人的一次填报提交（1 todo_id → 1 fill）。"""

    __tablename__ = "dynamic_fill_data"

    fill_id = Column(
        String(64), primary_key=True, default=lambda: _dyn_id("fill"),
        comment="填报记录 ID（前缀 fill_ + uuid hex）",
    )
    todo_id = Column(
        String(36), nullable=False, index=True, unique=True,
        comment="关联待办 ID（一个待办只允许一次成功提交）",
    )
    task_id = Column(String(36), nullable=False, comment="关联任务 ID（冗余，查询加速）")
    template_id = Column(
        String(64), nullable=False, comment="所属动态模板 ID（关联 dynamic_template.template_id）",
    )
    data_json = Column(
        JSONB, nullable=False, default=dict,
        comment="实际填报字段数据：{<column.key>: <value>, ...}",
    )
    submitted_by = Column(
        String(64), nullable=False, default="system", server_default="system",
        comment="填报提交人 ID（业务语义字段，与审计 created_by 并行存在，显式赋值）",
    )
    submitted_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="提交时间",
    )
    status = Column(
        String(16), nullable=False, default="draft", server_default="draft",
        comment="draft=草稿（仅 iframe 内暂存，暂不用）/ submitted=已提交",
    )

    __table_args__ = (
        Index("ix_dyn_fill_task_tpl", "task_id", "template_id"),
        {"comment": "动态填报提交数据：一次填报一条，JSONB 存真实字段值"},
    )
