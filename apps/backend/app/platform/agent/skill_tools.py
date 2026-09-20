"""技能文件工具（仅文件夹式技能轮次装配）：读技能目录下的参考文件、执行包内脚本、写暂存文件。

SKILL.md 正文会指引模型「bash orchestrator.sh [主题]」「读 steps/s0.md」等，
普通轮次没有这些工具，技能轮次按 skill_key 动态装配：

安全边界：
- 路径解析锁定 skills/<skill_key>/ 目录内，拒绝绝对路径与 .. 穿越；
- read_skill_file 只允许文本文件（.md / .json / .txt / .yaml / .yml），输出截断；
- write_skill_file 只能写技能目录下 .work/ 暂存子目录（不碰 SKILL.md / scripts/ 等技能自有文件）；
- run_skill_script 只允许 .py（限 scripts/ 子目录，当前解释器执行）与 .sh（bash 执行，
  编排类脚本如 orchestrator.sh 位于技能根目录），cwd 固定为技能根目录，超时与输出长度封顶。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool

from ...services.skill_files import SKILLS_DIR
from .tool_base import ToolCtx, tool_scope

logger = logging.getLogger(__name__)

# 读取类：允许的文本后缀 / 内容上限
_READ_SUFFIXES = {".md", ".json", ".txt", ".yaml", ".yml"}
_READ_CAP = 20000
# 写入类：.work/ 暂存子目录名与内容上限
_SCRATCH_DIR = ".work"
_WRITE_CAP = 20000
# 执行类：子进程超时（秒）与输出上限（PDF 渲染、编排脚本初始化均在时限内）
_SCRIPT_TIMEOUT = 120
_STDOUT_CAP = 160000
_STDERR_CAP = 4000


def _skill_root(skill_key: str) -> Path | None:
    """技能根目录；key 非法或目录不存在返回 None。"""
    root = (SKILLS_DIR / skill_key).resolve()
    if root.parent != SKILLS_DIR.resolve() or not root.is_dir():
        return None
    return root


def _resolve_in_skill(root: Path, rel: str) -> Path | None:
    """把模型给的路径解析进技能目录内；绝对路径 / .. 穿越 / 目录外一律 None。"""
    if not rel or rel.startswith(("/", "\\")):
        return None
    if ".." in PurePosixPath(rel).parts or ".." in Path(rel).parts:
        return None
    p = (root / rel).resolve()
    return p if p.is_relative_to(root) else None


def _resolve_in_scratch(root: Path, rel: str) -> Path | None:
    """写入路径解析进技能目录的 .work/ 暂存子目录；越界一律 None。

    两种写法等价支持（消除 read 返回路径与 write 入参的不对称，防 .work/.work 嵌套）：
    - "reports/x.md"：相对 .work/（主体写法）；
    - ".work/reports/x.md"：相对技能根（模型常从 read/run 的返回路径原样复用）。
    最终强制落在 .work/ 内，保证模型无法覆盖 SKILL.md / scripts/ 等技能自有文件。
    """
    scratch = root / _SCRATCH_DIR
    if not rel or rel.startswith(("/", "\\")):
        return None
    if ".." in PurePosixPath(rel).parts or ".." in Path(rel).parts:
        return None
    p = (root / rel).resolve()  # 先按技能根相对解析（兼容 ".work/..." 写法）
    if not p.is_relative_to(scratch):
        p = (scratch / rel).resolve()  # 再按 .work/ 相对解析
    return p if p.is_relative_to(scratch) else None


def _list_files(root: Path) -> str:
    """技能目录内文件清单（相对路径，按名排序，剔除 .work/ 与运行期产物），供工具描述让模型知道有什么可读。"""
    skip = {_SCRATCH_DIR, ".research_state", "reports"}
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not (set(rel.parts) & skip):
            files.append(rel.as_posix())
    return "、".join(files) if files else "（空目录）"


def build_skill_tools(ctx: ToolCtx, skill_key: str) -> list:
    """按技能装配 read_skill_file / write_skill_file / run_skill_script；目录缺失返回 []（降级不报错）。"""
    root = _skill_root(skill_key)
    if root is None:
        logger.warning("技能目录不存在，跳过技能工具装配 key=%s", skill_key)
        return []
    files = _list_files(root)

    @tool(
        "read_skill_file",
        description=(
            f"读取当前技能「{skill_key}」目录内的参考文件（相对路径，如 steps/s0.md、rules/source-grading.md）。"
            f"目录下现有文件：{files}。只允许文本文件（.md/.json/.txt/.yaml/.yml），内容超长会截断。"
        ),
    )
    def read_skill_file(path: str, tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "read_skill_file", tool_call_id, {"path": path}):
            p = _resolve_in_skill(root, path)
            if p is None or not p.is_file():
                return f"文件不存在或路径非法：{path}（只能读技能目录内的文本文件）"
            if p.suffix.lower() not in _READ_SUFFIXES:
                return f"文件类型不可读：{p.name}（只支持 {'/'.join(sorted(_READ_SUFFIXES))}）"
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > _READ_CAP:
                origin = len(text)
                text = text[:_READ_CAP] + f"\n…（内容超长已截断，原文 {origin} 字符）"
            return f"[{path}]\n{text}"

    @tool(
        "run_skill_script",
        description=(
            f"执行当前技能「{skill_key}」包内脚本：.sh（如编排脚本 orchestrator.sh，bash 执行）与 "
            f"scripts/ 子目录下的 .py（当前解释器执行）。script 为包内相对路径，arguments 为命令行参数列表"
            f"（如 [\"[调研主题]\"] 或 [\"--complete\", \"s0\", \"--next\", \"s1\"]）。"
            f"脚本在技能根目录执行，超时约 {_SCRIPT_TIMEOUT} 秒，返回 stdout/stderr。"
        ),
    )
    def run_skill_script(script: str, arguments: list[str] | None = None,
                         tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "run_skill_script", tool_call_id, {"script": script, "arguments": arguments or []}):
            p = _resolve_in_skill(root, script)
            if p is None or not p.is_file():
                return f"脚本不存在或不可执行：{script}（只允许技能包内的 .sh 与 scripts/ 下的 .py）"
            suffix = p.suffix.lower()
            if suffix == ".py":
                if "scripts" not in p.relative_to(root).parts:
                    return f"Python 脚本必须在 scripts/ 子目录下：{script}"
                cmd = [sys.executable, str(p), *(arguments or [])]
            elif suffix == ".sh":
                cmd = ["bash", str(p), *(arguments or [])]
            else:
                return f"脚本类型不可执行：{p.name}（只支持 .sh 与 scripts/ 下的 .py）"
            try:
                r = subprocess.run(
                    cmd, cwd=str(root), capture_output=True, text=True, timeout=_SCRIPT_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                return f"脚本执行超时（>{_SCRIPT_TIMEOUT}s）：{script}"
            out = r.stdout or ""
            if len(out) > _STDOUT_CAP:
                origin = len(r.stdout)
                out = out[:_STDOUT_CAP] + f"\n…（stdout 超长已截断，原文 {origin} 字符）"
            part = f"[exit={r.returncode}]\n"
            if out.strip():
                part += out
            if r.stderr and r.stderr.strip():
                err = r.stderr
                if len(err) > _STDERR_CAP:
                    err = err[:_STDERR_CAP] + "…（stderr 已截断）"
                part += f"\n[stderr]\n{err}"
            return part

    @tool(
        "write_skill_file",
        description=(
            f"向当前技能「{skill_key}」写入一个文本文件供脚本引用；文件实际落在 .work/ 暂存子目录"
            f"（不改动技能自有文件）。path 两种写法等价：\"reports/x.md\"（相对 .work/）或 "
            f"\".work/reports/x.md\"（相对技能根，与 read/run 返回的路径写法一致），均指向 "
            f".work/reports/x.md。只允许文本文件（.md/.json/.txt/.yaml/.yml），单次内容超长会拒绝。"
            f"长文组装：先 write_skill_file 写第一块，再以 append=true 逐块追加（每次 ≤18000 字符），"
            f"最后用 run_skill_script 处理成稿。"
        ),
    )
    def write_skill_file(path: str, content: str, append: bool = False,
                         tool_call_id: Annotated[str, InjectedToolCallId] = "") -> str:
        with tool_scope(ctx, "write_skill_file",
                        tool_call_id, {"path": path, "content_len": len(content), "append": append}):
            p = _resolve_in_scratch(root, path)
            if p is None:
                return f"路径非法：{path}（只能写入技能目录 .work/ 子目录内，不接受绝对路径或 ..）"
            if p.suffix.lower() not in _READ_SUFFIXES:
                return f"文件类型不可写：{p.name}（只支持 {'/'.join(sorted(_READ_SUFFIXES))}）"
            if len(content) > _WRITE_CAP:
                return f"内容超长（{len(content)} 字符，上限 {_WRITE_CAP}）：{path}"
            p.parent.mkdir(parents=True, exist_ok=True)
            if append and p.exists():
                with p.open("a", encoding="utf-8") as f:
                    f.write(content)
                rel = p.relative_to(root).as_posix()
                size = p.stat().st_size
                return (f"已追加 {rel}（本次 {len(content)} 字符，现共 {size} 字节）。"
                        f"在 run_skill_script 中引用该文件请用相对路径 \"{rel}\"。")
            p.write_text(content, encoding="utf-8")
            rel = p.relative_to(root).as_posix()
            return f"已写入 {rel}（{len(content)} 字符）。在 run_skill_script 中引用该文件请用相对路径 \"{rel}\"。"

    return [read_skill_file, run_skill_script, write_skill_file]
