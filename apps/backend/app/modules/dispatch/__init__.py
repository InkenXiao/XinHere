"""非对话派发模块（弹窗入口）。

模板类派发（含动态填报模板 dyn_ 前缀）复用 POST /tasks（platform/api/tasks.py，
GET /tasks/templates 已合并动态项）；本模块只补「普通待办」直派接口。
router 由 main.py 直接 import 子模块挂载，避免与 persistence 产生循环导入。
"""
from __future__ import annotations
