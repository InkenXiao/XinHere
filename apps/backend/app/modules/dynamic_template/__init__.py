"""动态填报模板模块（Excel 解析 → HTML 模板 → 派发填报闭环）。

注意：本 __init__ 只 import models 触发 SQLAlchemy metadata 注册（alembic
autogenerate 依赖 persistence.models 顶部的自注册 import 回头执行本包）；
api/services 的 router 与函数由调用方直接 import 子模块，避免
api → persistence.models → 本包 __init__ → api 的循环导入。
"""
from __future__ import annotations

from . import models  # noqa: F401  pylint: disable=unused-import
