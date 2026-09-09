"""对话附件服务 · 上传存储 + 深度解析

- 图片 → 视觉多模态模型 (VISION_*, OpenAI 兼容 chat completions, base64 data URL)
- PDF → PyMuPDF 文本层 → 空则 mineru 算力网关 (扫描件深度解析)
- 文本类 (txt/md/csv/json, ≤8KB) → utf-8 内联
- 其余类型 → 仅注入文件名标记 (模型可见但读不了内容)
- 统一约束: 单文件注入 ≤20000 字符; 单附件失败降级不阻塞消息
"""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from fastapi import UploadFile

from ..core.config import settings

logger = logging.getLogger(__name__)

MAX_UPLOAD_MB = 20
MAX_INLINE_CHARS = 20000          # 单文件注入硬上限 (技术红线)
TEXT_EXTS = {".txt", ".md", ".csv", ".json"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
DEFAULT_IMAGE_QUESTION = (
    "请详细描述这张图片的内容。若图片包含文字/表格/图表, 请完整转录关键信息; "
    "若为界面截图, 请说明页面结构与关键数据。"
)


def files_dir() -> Path:
    d = Path(__file__).resolve().parents[2] / "data" / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def save_upload(user_id: str, upload: UploadFile) -> dict:
    raw = await upload.read()
    if not raw:
        raise ValueError("文件内容为空")
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"文件超过 {MAX_UPLOAD_MB}MB 限制")
    display = Path(upload.filename or "").name or "file"
    stem = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", display)
    stored = f"{user_id[:8]}-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}-{stem}"
    path = files_dir() / stored
    if path.resolve().parent != files_dir():  # 防路径穿越兜底
        raise ValueError("非法文件名")
    path.write_bytes(raw)
    return {"ok": True, "name": stored, "display_name": display, "size": len(raw)}


def _display_name(stored: str) -> str:
    """从存储名还原可读文件名 (第 3 个 - 之后的原始名部分)"""
    parts = (stored or "").split("-", 3)
    return parts[3] if len(parts) == 4 else stored


def _clip(text: str) -> str:
    if len(text) <= MAX_INLINE_CHARS:
        return text
    return text[:MAX_INLINE_CHARS] + "\n（内容过长，已截取前 20000 字）"


async def build_attachment_block(
    user_id: str,
    names: list[str],
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """解析附件集合, 返回注入 LLM 的参考文本块 (无附件或全部失败返回空串)"""
    parts: list[str] = []
    for name in names or []:
        safe = Path(name).name
        path = files_dir() / safe
        display = _display_name(safe)
        if not path.exists():
            parts.append(f"【附件 {display}】(文件不存在)")
            continue
        ext = path.suffix.lower()
        size_kb = max(1, path.stat().st_size // 1024)
        try:
            if ext in IMAGE_EXTS:
                _notify(on_progress, f"正在解析图片「{display}」…")
                text = await _recognize_image(path)
            elif ext == ".pdf":
                _notify(on_progress, f"正在解析 PDF「{display}」，扫描件解析较慢请稍候…")
                text = await _parse_pdf(path)
            elif ext in TEXT_EXTS:
                text = path.read_bytes()[:8192].decode("utf-8", errors="replace")
            else:
                parts.append(f"### 附件：{display}（{size_kb}KB，该类型文件暂不支持内容解析）")
                continue
        except Exception as exc:  # noqa: BLE001 - 单附件失败降级
            logger.warning("附件解析失败 name=%s: %s", safe, exc)
            parts.append(f"【附件 {display}】(解析失败：{str(exc)[:120]})")
            continue
        if text:
            parts.append(f"### 附件：{display}\n{_clip(text)}")
        else:
            parts.append(f"【附件 {display}】(未能解析出内容)")
    return "\n\n".join(p for p in parts if p)


def _notify(cb: Callable[[str], None] | None, text: str) -> None:
    if cb:
        try:
            cb(text)
        except Exception:  # noqa: BLE001
            pass


# ---------- 图片 → 视觉模型 ----------

async def _recognize_image(path: Path) -> str:
    if not (settings.vision_api_url and settings.vision_api_key and settings.vision_model):
        raise RuntimeError("视觉模型未配置，图片暂不能识别")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    async with httpx.AsyncClient(timeout=120) as cli:
        resp = await cli.post(
            settings.vision_api_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.vision_api_key}"},
            json={
                "model": settings.vision_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": DEFAULT_IMAGE_QUESTION},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }],
            },
        )
        resp.raise_for_status()
        return (resp.json()["choices"][0]["message"]["content"] or "").strip()


# ---------- PDF → 文本层 → mineru ----------

def _pdf_text_layer(path: Path) -> tuple[str, int]:
    import fitz  # PyMuPDF

    parts: list[str] = []
    with fitz.open(str(path)) as doc:
        pages = doc.page_count
        for i, page in enumerate(doc, start=1):
            text = (page.get_text("text") or "").strip()
            if text:
                parts.append(f"----- 第 {i} 页 -----\n{text}")
    return "\n\n".join(parts), pages


async def _mineru_parse(path: Path) -> str:
    url = settings.mineru_api_url.rstrip("/") + "/api/v1/parse/pdf"
    async with httpx.AsyncClient(timeout=600) as cli:
        with open(path, "rb") as f:
            resp = await cli.post(url, files={"file": (path.name, f, "application/pdf")})
    if resp.status_code != 200:
        raise RuntimeError(f"解析网关返回 {resp.status_code}")
    md = (resp.json().get("content") or "").strip()
    if not md:
        raise RuntimeError("解析网关返回空内容")
    return md


async def _parse_pdf(path: Path) -> str:
    text, pages = await asyncio.to_thread(_pdf_text_layer, path)
    if len(text.strip()) >= 50:
        return f"（PDF 共 {pages} 页）\n{text}"
    if settings.mineru_api_url:
        md = await _mineru_parse(path)
        if md:
            return f"（扫描版 PDF 共 {pages} 页，深度解析）\n{md}"
    return text
