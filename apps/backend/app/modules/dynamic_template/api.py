"""动态填报模板 API：Excel 解析建模板 / 列表详情 / 填报页 HTML / 填报提交。

挂载于 main.py（一行 import + for 循环一项）：prefix=/dynamic-templates。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session


from app.platform.api.deps import current_user, require_hq

from ...core import errors
from ...persistence.models import BizTodo
from ...persistence.session import get_db
from ...services import todo as todo_svc
from . import services as svc

router = APIRouter(prefix="/dynamic-templates", tags=["dynamic-templates"])

_MAX_UPLOAD = 10 * 1024 * 1024  # Excel 上传上限 10MB
_EXCEL_SUFFIX = (".xls", ".xlsx", ".xlsm")


async def _read_excel(file: UploadFile) -> bytes:
    fname = (file.filename or "").lower()
    if not fname.endswith(_EXCEL_SUFFIX):
        raise errors.validation("仅支持 .xls / .xlsx / .xlsm 格式的 Excel 文件")
    content = await file.read()
    if not content:
        raise errors.validation("文件内容为空")
    if len(content) > _MAX_UPLOAD:
        raise errors.validation("文件超过 10MB 上限")
    return content


@router.post("/parse-excel")
async def parse_excel(file: UploadFile = File(...), user=Depends(current_user)):
    """Excel 解析预览（不落库）：首个 Sheet + 首行表头 → 字段推断 + 样本行。"""
    return svc.parse_excel(await _read_excel(file), file.filename)


@router.post("/from-excel")
async def create_from_excel(
    file: UploadFile = File(...), name: str = Form(...), columns_json: str = Form(""),
    user=Depends(require_hq), db: Session = Depends(get_db),
):
    """由 Excel 创建动态模板（创建即启用，出现在派发下拉）。

    columns_json：预览页用户调整后的 {sheet_name, columns, sample_rows} JSON 字符串；
    不传则按解析推断结果直接创建。
    """
    content = await _read_excel(file)
    parsed = None
    if columns_json.strip():
        try:
            parsed = json.loads(columns_json)
        except json.JSONDecodeError as exc:
            raise errors.validation(f"columns_json 不是合法 JSON：{exc}") from exc
        if not isinstance(parsed, dict) or not parsed.get("columns"):
            parsed = None
    if parsed is None:
        parsed = svc.parse_excel(content, file.filename)
    return svc.create_template(
        db, name=name, columns=parsed["columns"], parse_result=parsed,
        source_file_name=file.filename or "未命名", owner_id=user.user_id,
    )


@router.get("")
def list_templates(user=Depends(current_user), db: Session = Depends(get_db)):
    """管理列表：系统级共享 + 本人私有（含停用），派发下拉另行合并动态项。"""
    return {"items": svc.list_templates(db, user.user_id)}


async def _read_excels(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not 1 <= len(files) <= 10:
        raise errors.validation("请选择 1 至 10 个 Excel 文件")
    result = []
    total = 0
    for file in files:
        content = await _read_excel(file)
        total += len(content)
        if total > 30 * 1024 * 1024:
            raise errors.validation("Excel 文件合计不能超过 30MB")
        result.append((file.filename or "未命名.xlsx", content))
    return result


@router.post("/parse-excels")
async def parse_excels(files: list[UploadFile] = File(...), user=Depends(current_user)):
    return svc.parse_excels(await _read_excels(files))


@router.post("/from-excels")
async def create_from_excels(
    files: list[UploadFile] = File(...), name: str = Form(...), columns_json: str = Form(""),
    user=Depends(require_hq), db: Session = Depends(get_db),
):
    parsed = svc.parse_excels(await _read_excels(files))
    if columns_json.strip():
        try:
            edits = json.loads(columns_json)
        except json.JSONDecodeError as exc:
            raise errors.validation("字段调整数据不是合法 JSON") from exc
        parsed = svc.apply_tab_edits(parsed, edits)
    return svc.create_template(
        db, name=name, columns=parsed["columns"], parse_result=parsed,
        source_file_name=f"{len(files)} 个 Excel 文件", owner_id=user.user_id,
    )


@router.get("/{template_id}")
def detail(template_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    return svc.get_template_view(db, template_id, with_html=True)


class StatusIn(BaseModel):
    status: str


@router.patch("/{template_id}/status")
def patch_status(template_id: str, body: StatusIn,
                 user=Depends(require_hq), db: Session = Depends(get_db)):
    return svc.patch_status(db, template_id, body.status)


@router.delete("/{template_id}")
def remove(template_id: str, user=Depends(require_hq), db: Session = Depends(get_db)):
    return svc.logic_delete(db, template_id)


@router.get("/{template_id}/fill")
def fill_page(template_id: str, todo_id: str = Query(default=None),
              user=Depends(current_user), db: Session = Depends(get_db)):
    """填报页 HTML（JSON 返回 {html}，前端 iframe srcdoc 内嵌）。

    todo_id 提供且已有数据 → 预填（退回重填场景）。
    """
    return svc.render_fill_page(db, template_id, todo_id, user.user_id)


class FillIn(BaseModel):
    todo_id: str
    data: dict


@router.post("/{template_id}/fill/draft")
def save_draft(template_id: str, body: FillIn,
               user=Depends(current_user), db: Session = Depends(get_db)):
    """保存未完成填报，不执行必填校验或状态流转。"""
    return svc.save_fill_draft(
        db, todo_id=body.todo_id, template_id=template_id,
        data=body.data, user_id=user.user_id,
    )


@router.post("/{template_id}/fill")
def fill(template_id: str, body: FillIn,
         user=Depends(current_user), db: Session = Depends(get_db)):
    """填报提交：dynamic_fill_data 落库 + 复用待办两段式（review_required → 待审）。"""
    result = svc.ensure_fill_submitted(
        db, todo_id=body.todo_id, template_id=template_id,
        data=body.data, user_id=user.user_id,
    )
    # 提交阶段产生的通知早于请求级事务提交时，派发人会立即读到旧状态。
    # 先提交，再补发变更事件，确保审核端刷新后直接得到 submitted。
    db.commit()
    todo = db.get(BizTodo, body.todo_id)
    if todo is not None:
        todo_svc._notify_todo(db, todo)
    return result
