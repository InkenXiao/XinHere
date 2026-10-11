"""任务模板声明机制（大流程固定、小流程可插拔的核心）。

模板放在 plugins/*/task-templates/*.json，声明：表单 schema、审核要求、
期间规则、发起角色、标题/副标题规则。启动时 fail-loud 加载校验；
派发/填报时按模板校验 payload。新增业务 = 新增模板 JSON，不改内核。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core import errors

_TPL_ROOT = Path(__file__).resolve().parents[4] / "plugins"

_FIELD_TYPES = ("text", "textarea", "number", "date", "select", "table")
_TEMPLATES: dict[str, dict] | None = None


def _validate_template(tpl: dict, src: Path) -> None:
    """fail-loud：模板结构不合法直接抛异常，拒绝带病启动。"""
    where = f"任务模板 {src.name}"
    for k in ("template_key", "name", "scene"):
        if not tpl.get(k):
            raise RuntimeError(f"{where} 缺少必填字段 {k}")
    form = tpl.get("form")
    if not isinstance(form, dict) or not isinstance(form.get("fields"), list) or not form["fields"]:
        raise RuntimeError(f"{where} form.fields 必须为非空数组")
    seen: set[str] = set()
    for f in form["fields"]:
        fk, ft = f.get("k"), f.get("type")
        if not fk or ft not in _FIELD_TYPES:
            raise RuntimeError(f"{where} 字段 {fk!r} 的 type 非法（{_FIELD_TYPES}）")
        if fk in seen:
            raise RuntimeError(f"{where} 字段 k={fk} 重复")
        seen.add(fk)
        if ft == "select" and not isinstance(f.get("options"), list):
            raise RuntimeError(f"{where} 字段 {fk} 为 select 但缺少 options")
        if ft == "table" and not isinstance(f.get("columns"), list):
            raise RuntimeError(f"{where} 字段 {fk} 为 table 但缺少 columns")


def discover_templates() -> dict[str, dict]:
    """扫描 plugins/*/task-templates/*.json；重名 template_key fail-loud。"""
    global _TEMPLATES
    if _TEMPLATES is not None:
        return _TEMPLATES
    found: dict[str, dict] = {}
    for src in sorted(_TPL_ROOT.glob("*/task-templates/*.json")):
        try:
            tpl = json.loads(src.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"任务模板 {src} 解析失败：{exc}") from exc
        _validate_template(tpl, src)
        key = tpl["template_key"]
        if key in found:
            raise RuntimeError(f"任务模板 template_key 重复：{key}")
        found[key] = tpl
    _TEMPLATES = found
    return _TEMPLATES


def get_template(template_key: str) -> dict:
    tpl = discover_templates().get(template_key)
    if tpl is None:
        raise errors.not_found(f"任务模板不存在：{template_key}")
    return tpl


def list_template_views() -> list[dict]:
    return [
        {
            "template_key": t["template_key"],
            "name": t["name"],
            "scene": t["scene"],
            "review_required": bool(t.get("review_required")),
            "related_system": t.get("related_system"),
            "period_rule": t.get("period_rule", "natural_month"),
            "creator_roles": t.get("creator_roles", []),
        }
        for t in discover_templates().values()
    ]


def render_title(tpl: dict, *, anchor_company: str | None, period: str | None) -> str:
    return tpl.get("title_rule", "{anchor_company} · {name}").format(
        anchor_company=anchor_company or "", name=tpl["name"], period=period or "",
    )


def validate_payload(tpl: dict, payload: dict) -> None:
    """按模板校验填报值：必填项缺失/选项越界 → 400。"""
    for f in tpl["form"]["fields"]:
        v = payload.get(f["k"])
        empty = v is None or v == "" or (isinstance(v, list) and not v) or (isinstance(v, dict) and not v)
        if f.get("required") and empty:
            raise errors.validation(f"「{f['label']}」为必填项")
        if empty:
            continue
        if f["type"] == "select" and v not in f.get("options", []):
            raise errors.validation(f"「{f['label']}」取值不合法")
        if f["type"] == "number" and not isinstance(v, (int, float)):
            raise errors.validation(f"「{f['label']}」需为数字")
