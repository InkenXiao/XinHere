# 工具展示名注册表（单一事实源，后端侧）
# event_translator 在 tool/call、tool/result 事件里附带 display_name，前端 ToolCallCard
# 优先渲染 display_name，其次前端字典 TOOL_ZH（apps/frontend/src/constants/index.ts，双端同步）。
# 新增工具时：@tool 注册名加进这里 + 前端 TOOL_ZH 同步一条；动态来源（MCP/技能脚本）
# 可在装配处调用 tool_display_name(name, args) 做细分映射。
TOOL_DISPLAY_ZH: dict[str, str] = {
    # 通用待办四件套 + 派发（tools.py build_common_tools）
    "arrange_task": "安排待办",
    "list_my_todos": "查询我的待办",
    "request_todo_done": "提交待办完成",
    "request_todo_na": "发起不涉及申请",
    "review_submissions": "审核待办提交",
    "confirm_na_requests": "确认不涉及申请",
    "remind_pending": "催办待办",
    "append_task_assignees": "追加任务处理人",
    "end_task_flow": "结束任务流程",
    "task_overview": "任务总览",
    "dispatch_task": "派发任务",
    "fill_todo_payload": "填报待办数据",
    "pre_review_todos": "待办预审",
    # 数据/报表工具（tools.py build_data_tools）
    "query_data": "问数查询",
    "draw_chart": "绘制图表",
    "open_post_report": "打开投后报告",
    "open_fin_risk_report": "打开财务风险报告",
    "open_milestone_audit": "打开里程碑审计",
    "open_indicator_view": "打开指标视图",
    "query_milestone_stats": "里程碑统计",
    "generate_html_report": "生成 HTML 报告",
    "generate_fin_risk_report": "生成财务风险报告",
    "generate_ppt": "生成 PPT",
    # 技能文件工具（skill_tools.py）
    "read_skill_file": "读取技能文件",
    "run_skill_script": "运行技能脚本",
    "write_skill_file": "写入技能文件",
    # 联网工具（web_tools.py）
    "web_search": "联网搜索",
    "web_fetch": "网页读取",
    # oa-workflow 插件工具（移除插件时同步删除）
    "oa_create_task": "OA派发任务",
    "oa_submit_request": "OA发起审批",
    "oa_my_dashboard": "OA工作台查询",
    "oa_audit": "OA审批",
}


def tool_display_name(name: str, arguments: dict | None = None) -> str | None:
    """工具技术名 → 中文展示名；未注册返回 None（事件里不带 display_name，前端回落自身字典/英文名）。
    arguments 预留给动态细分（如按脚本名细分 run_skill_script），当前仅查静态表。"""
    return TOOL_DISPLAY_ZH.get(name)
