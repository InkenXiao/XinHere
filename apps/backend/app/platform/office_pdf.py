"""Office 文件 → PDF 转换（KB 参考来源预览用）：LibreOffice soffice 无头转换 + 按 file_id 磁盘缓存。

- soffice 可执行缺失（本机未装 / 镜像未装 libreoffice）时 soffice_path() 返回 None，
  调用方降级回退原始文件预览，功能整体不炸。
- 缓存目录 apps/backend/data/office_pdf/<file_id>.pdf：转换一次永久复用（KB 文件按 file_id
  不可变），不加 TTL；容器内写临时层，重建容器后首刷重转，可接受。
- 转换 subprocess 加超时，防异常文件挂死 worker；soffice 同一 profile 并发冲突用文件锁串行化。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()  # soffice 不允许同 profile 并跑，进程级串行即可

# Windows 本机开发默认安装位（Linux 容器走 PATH）
_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


def soffice_path() -> str | None:
    found = shutil.which("soffice")
    if found:
        return found
    for p in _SOFFICE_CANDIDATES:
        if Path(p).is_file():
            return p
    return None


def _cache_dir() -> Path:
    # 测试可注入（monkeypatch _CACHE_DIR）；默认 apps/backend/data/office_pdf
    return _CACHE_DIR()


_CACHE_DIR = lambda: (  # noqa: E731  （默认实现提为模块变量，测试 monkeypatch 点）
    _mk_cache_dir(Path(__file__).resolve().parents[2] / "data" / "office_pdf"))


def _mk_cache_dir(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_pdf(file_id: int) -> Path | None:
    """已转换的缓存 PDF 路径；未转换返回 None。"""
    p = _cache_dir() / f"{file_id}.pdf"
    return p if p.is_file() and p.stat().st_size > 0 else None


def convert_office_to_pdf(file_id: int, src_bytes: bytes, ext: str) -> Path:
    """office 字节流 → PDF，返回缓存文件路径。失败抛 RuntimeError（调用方降级）。"""
    exe = soffice_path()
    if not exe:
        raise RuntimeError("soffice 未安装")
    cached = cached_pdf(file_id)
    if cached:
        return cached

    out_dir = _cache_dir()
    # 同一 file_id 并发转换：锁内二次确认缓存，避免重复转
    with _LOCK:
        cached = cached_pdf(file_id)
        if cached:
            return cached
        with tempfile.TemporaryDirectory(prefix=f"kbpdf-{file_id}-") as tmp:
            src = Path(tmp) / f"src.{ext}"
            src.write_bytes(src_bytes)
            try:
                proc = subprocess.run(
                    [exe, "--headless", "--norestore", "--convert-to", "pdf",
                     "--outdir", tmp, str(src)],
                    capture_output=True, timeout=120,
                )
            except subprocess.TimeoutExpired as exc:
                logger.warning("office→PDF 转换超时 file_id=%s", file_id)
                raise RuntimeError("转换超时") from exc
            pdf = Path(tmp) / "src.pdf"
            if proc.returncode != 0 or not pdf.is_file():
                logger.warning("office→PDF 转换失败 file_id=%s rc=%s stderr=%s",
                               file_id, proc.returncode, (proc.stderr or b"")[:300])
                raise RuntimeError("转换失败")
            dest = out_dir / f"{file_id}.pdf"
            shutil.move(str(pdf), dest)
            return dest
