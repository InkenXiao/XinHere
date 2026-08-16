#!/usr/bin/env python3
"""XinHere 三条红线 + 分层静态扫描（CI 卡点，stdlib 零依赖）。

红线1：全库禁物理 DELETE（无 db.delete / bulk delete / DELETE FROM / TRUNCATE /
       delete-orphan / FK ondelete）；
红线2：业务表继承 BusinessBase（is_delete + 审计四件套），updated_at 触发器
       由 alembic 按 BusinessBase 动态覆盖；
红线3：select/insert/update 全量落 platform_operation_logs 五要素，detail 过
       脱敏管线（scrub），工具/组件写操作 channel='tool' 带溯源凭证；
分层：后端 plugins 只依赖 platform 公开 API，禁互导/禁 import api 层；
     前端 plugins 禁 import shell，禁跨插件互导。

用法：python scripts/lint_redlines.py  → 全绿 exit 0；违规逐条列出 exit 1。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_APP = ROOT / "apps" / "backend" / "app"
BACKEND_ALEMBIC = ROOT / "apps" / "backend" / "alembic"
BACKEND_PLUGINS = ROOT / "plugins"
FRONTEND_SRC = ROOT / "apps" / "frontend" / "src"

violations: list[str] = []


def _py_files(*dirs: Path) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        if d.exists():
            out.extend(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)
    return out


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


# ---------------------------------------------------------------- 红线 1
def check_r1_no_physical_delete() -> None:
    """生产代码（app/ + plugins/ + alembic/）不得出现物理删除路径。"""
    patterns = [
        (re.compile(r"\b\w*(?:db|session)\w*\.delete\s*\("), "ORM session.delete()"),
        (re.compile(r"\.query\([^)]*\)\.delete\s*\("), "ORM query.bulk_delete()"),
        (re.compile(r"\bdelete\s*\(\s*\w+\s*\)"), "sqlalchemy delete(Model)"),
        (re.compile(r"\bDELETE\s+FROM\b", re.I), "原生 SQL DELETE FROM"),
        (re.compile(r"\bTRUNCATE\b", re.I), "原生 SQL TRUNCATE"),
        (re.compile(r"delete-orphan"), "cascade delete-orphan"),
        (re.compile(r"\bondelete\s*="), "FK ondelete（全库禁 DELETE，禁止级联约束）"),
    ]
    for f in _py_files(BACKEND_APP, BACKEND_PLUGINS, BACKEND_ALEMBIC):
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for rx, label in patterns:
                if rx.search(line):
                    violations.append(f"红线1 [{label}] {_rel(f)}:{i}: {stripped[:120]}")


# ---------------------------------------------------------------- 红线 2
def check_r2_audit_fields() -> None:
    base = (BACKEND_APP / "persistence" / "base.py").read_text(encoding="utf-8")
    for field in ("is_delete", "created_by", "created_at", "updated_by", "updated_at"):
        if not re.search(rf"^\s*{field}\s*=\s*Column", base, re.M):
            violations.append(f"红线2 BusinessBase 缺审计字段 {field}")

    models = BACKEND_APP / "persistence" / "models.py"
    text = models.read_text(encoding="utf-8")
    platform_whitelist = {
        "PlatformSession", "PlatformSessionEvent", "PlatformCompensation",
        "PlatformOperationLog", "PlatformProjection",
    }
    for m in re.finditer(r"^class\s+(\w+)\((\w+)\):", text, re.M):
        cls, parent = m.groups()
        if parent == "Base" and cls not in platform_whitelist:
            violations.append(
                f"红线2 {cls} 直接继承 Base 但不在平台表白名单（业务表必须继承 BusinessBase）"
            )
        elif parent not in ("Base", "BusinessBase"):
            violations.append(f"红线2 {cls} 继承未知基类 {parent}")

    mig = BACKEND_ALEMBIC / "versions" / "0001_initial.py"
    mig_text = mig.read_text(encoding="utf-8")
    for token in ("set_updated_at()", "_business_tables()", "attach_updated_at_trigger"):
        if token not in mig_text:
            violations.append(f"红线2 alembic 0001 缺 updated_at 触发器机制（{token}）")


# ---------------------------------------------------------------- 红线 3
def check_r3_operation_logs() -> None:
    sess = (BACKEND_APP / "persistence" / "session.py").read_text(encoding="utf-8")
    for op in ('"select"', '"insert"', '"update"'):
        if f"operation={op}" not in sess:
            violations.append(f"红线3 session.py 缺 {op} 操作日志")
    for field in ("user_id", "channel", "actor", "entity", "record_key",
                  "client_ip", "entry_point", "request_id"):
        if f"{field}=" not in sess:
            violations.append(f"红线3 session.py 操作日志缺五要素字段 {field}")
    if "detail=scrub(" not in sess:
        violations.append("红线3 session.py 日志 detail 未过脱敏管线 scrub()")

    audit = BACKEND_APP / "platform" / "audit" / "log.py"
    audit_text = audit.read_text(encoding="utf-8")
    if "def scrub" not in audit_text:
        violations.append("红线3 audit/log.py 缺 scrub 脱敏函数")
    for key in ("password", "token", "secret"):
        if key not in audit_text:
            violations.append(f"红线3 scrub 脱敏词表缺 {key}")

    tool_base = (BACKEND_APP / "platform" / "agent" / "tool_base.py").read_text(encoding="utf-8")
    for token in ('channel="tool"', "call_id", "tool_call_arguments", "operator_user_id"):
        if token not in tool_base:
            violations.append(f"红线3 tool_scope 缺 AI 溯源要素（{token}）")

    sessions = (BACKEND_APP / "platform" / "api" / "sessions.py").read_text(encoding="utf-8")
    if 'channel="tool"' not in sessions:
        violations.append("红线3 组件提交（component confirm）未注入 channel='tool' 溯源上下文")


# ---------------------------------------------------------------- 分层
def check_backend_layering() -> None:
    """plugins 只依赖 platform 公开 API；禁互导、禁 import api/agent 内部。"""
    allowed_app = (
        "app.persistence", "app.services", "app.core",
        "app.platform.agent.tool_base",
    )
    third_party_ok = ("langchain", "langgraph", "sqlalchemy", "fastapi", "pydantic")
    for f in _py_files(BACKEND_PLUGINS):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^\s*(?:from|import)\s+([\w.]+)", line)
            if not m:
                continue
            mod = m.group(1)
            if mod.startswith("plugins."):
                violations.append(f"分层 [后端插件互导] {_rel(f)}:{i}: {mod}")
            elif mod.startswith("app."):
                if not any(mod == a or mod.startswith(a + ".") for a in allowed_app):
                    violations.append(f"分层 [插件越权依赖] {_rel(f)}:{i}: {mod}")


def check_frontend_layering() -> None:
    """前端业务组件（src/plugins）禁 import shell / 跨插件互导。"""
    plug_dir = FRONTEND_SRC / "plugins"
    for f in sorted(plug_dir.glob("*.tsx")) + sorted(plug_dir.glob("*.ts")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^\s*import\s+.*from\s+['\"]([^'\"]+)['\"]", line)
            if not m:
                continue
            target = m.group(1)
            if target.startswith("@/shell") or target.startswith("../shell"):
                violations.append(f"分层 [插件 import shell] {_rel(f)}:{i}: {target}")
            if re.match(r"^\./[A-Z]", target):  # 同目录其它插件组件
                violations.append(f"分层 [插件互导] {_rel(f)}:{i}: {target}")


# ---------------------------------------------------------------- 密钥明文
def check_no_hardcoded_secrets() -> None:
    """生产代码禁硬编码密钥/口令（seed.py 演示口令豁免）。"""
    rx = re.compile(
        r"(?:password|passwd|secret|api[_-]?key)\s*=\s*[\"'][^\"'\s]{6,}[\"']", re.I
    )
    for f in _py_files(BACKEND_APP, BACKEND_PLUGINS):
        if f.name == "seed.py":
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if rx.search(line):
                violations.append(f"密钥明文 {_rel(f)}:{i}: {line.strip()[:100]}")


def main() -> int:
    checks = [
        ("红线1 禁物理 DELETE", check_r1_no_physical_delete),
        ("红线2 审计字段+触发器", check_r2_audit_fields),
        ("红线3 操作日志五要素+脱敏", check_r3_operation_logs),
        ("后端插件分层", check_backend_layering),
        ("前端插件分层", check_frontend_layering),
        ("密钥明文扫描", check_no_hardcoded_secrets),
    ]
    for name, fn in checks:
        before = len(violations)
        fn()
        mark = "PASS" if len(violations) == before else f"FAIL({len(violations) - before})"
        print(f"[{mark}] {name}")
    if violations:
        print(f"\n共 {len(violations)} 处违规：")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("\n三红线 + 分层静态扫描全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
