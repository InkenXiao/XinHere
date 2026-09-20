"""文件夹式技能（文件即技能）：扫描 backend/skills/<key>/SKILL.md，前端弹层可直接调用。

SKILL.md 采用极简 frontmatter（--- 包裹的 key: value 块）+ 正文 prompt：
- name / desc / sort_no 为展示元数据（缺省用目录名）；
- 正文整段作为调用时的引导 prompt 进入对话。
目录不存在或为空时返回 []（能力降级，不报错）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 技能文件根目录：apps/backend/skills/
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def _parse_skill_md(key: str, path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    m = _FM_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[m.end():]
    if not body.strip():
        logger.warning("技能文件缺少 prompt 正文，跳过 key=%s path=%s", key, path)
        return None
    return {
        "skill_key": key,
        "name": meta.get("name") or key,
        "desc": meta.get("desc") or meta.get("description") or "",
        "sort_no": int(meta["sort_no"]) if meta.get("sort_no", "").isdigit() else 999,
        "trigger": meta.get("trigger") or f"使用技能：{meta.get('name') or key}",
        "prompt": body.strip(),
    }


def list_file_skills() -> list[dict]:
    """列出全部文件夹技能（按 sort_no）；目录缺失/为空返回 []。"""
    if not SKILLS_DIR.is_dir():
        return []
    items: list[dict] = []
    for d in sorted(SKILLS_DIR.iterdir()):
        f = d / "SKILL.md"
        if d.is_dir() and f.is_file() and (s := _parse_skill_md(d.name, f)):
            items.append(s)
    return sorted(items, key=lambda x: x["sort_no"])


def get_file_skill(key: str) -> dict | None:
    return next((s for s in list_file_skills() if s["skill_key"] == key), None)
