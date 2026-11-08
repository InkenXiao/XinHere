"""系统提示词组装器 —— DSH system-prompt 思想的 Python 轻量移植（P1/P5 改造）。

DSH 原理：提示词不是写死在 agent loop 里的字符串，而是 section 注册表在装配期
按 order 排序、插值、经瀑布提交；prompt 是「派生产物」而非请求字段。
xinhere 移植：
- PromptAssembler：进程级 section 注册表（同名 fail-loud，override 供测试/热更）；
- 内置层（order 10-50）：base_persona / xuanpu_link / task_routing / data_routing /
  guardrails——内容逐段迁移自旧 SYSTEM_PROMPT，语义不变；
- 插件层（order 60）：discover 后把 manifest.agent_prompt 注册为 plugin:<name> section；
- 技能层（P5 渐进披露，每 run 动态）：
  · 有技能轮次 → 单一数据驱动的 skill:* section（替代旧 executor 三分支 if/elif 拼接）；
  · 无技能轮次 → skills:catalog 只注技能目录（name+desc+trigger），不注执行正文——
    技能正文与技能工具必须图构建期装配，由路由层（skill_intent）在下一轮按需加载。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class PromptSection:
    name: str
    order: int
    render: Callable[[], str]

    def text(self) -> str:
        return self.render().strip()


class PromptAssembler:
    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}

    def register(self, section: PromptSection, *, override: bool = False) -> None:
        if not override and section.name in self._sections:
            raise RuntimeError(f"提示词 section 重复注册: {section.name}")
        self._sections[section.name] = section

    def unregister(self, name: str) -> None:
        self._sections.pop(name, None)

    def sections(self, extra: list[PromptSection] | None = None) -> list[PromptSection]:
        merged = dict(self._sections)
        for s in extra or []:
            if s.name in merged and merged[s.name] is not s:
                raise RuntimeError(f"提示词 section 重复注册: {s.name}")
            merged[s.name] = s
        return sorted(merged.values(), key=lambda s: (s.order, s.name))

    def assemble(self, extra: list[PromptSection] | None = None) -> str:
        return "\n\n".join(s.text() for s in self.sections(extra) if s.text())


prompt_registry = PromptAssembler()

# ---------------- 内置层（内容迁移自旧 SYSTEM_PROMPT，语义不变） ----------------

_BASE_PERSONA = """你是 XinHere（新在这里，心在这里）的财务智能助手，服务本部财务与被投企业财务。
你可以：检索知识库、发起风险预警财务指标填报、现金保障倍数填报、经营者考核填报、
里程碑反馈、亮灯调整、生成投后报告、派发通用任务、查询任务执行统计。"""

_XUANPU_LINK = """你还可以联动 XuanPu 平台：与平台智能体对话（xuanpu_chat）、查建待办（xuanpu_todos / xuanpu_create_todo）、
看数据看板（xuanpu_dashboard）、执行平台技能（xuanpu_skills → xuanpu_skill_run；
上架技能 xuanpu_skill_info 读执行指引并严格照做，指引中的包内脚本用 xuanpu_skill_script 执行）、
调用平台工具（xuanpu_tools → xuanpu_tool_run）、调用用户注册的 MCP 服务（xuanpu_mcp_servers → xuanpu_mcp_call）、
收发 IM 消息（xuanpu_im_send 发送 / xuanpu_im_messages 拉取企微对话记录）、
发起与处理填报任务（xuanpu_fill_create 发起 / xuanpu_fill_my 查我的待填 / xuanpu_fill_submit 填写提交）、
检索平台接入的知识库（xuanpu_kb_search）。
联动前若不确定名称，先调对应列表工具查询，不要臆造名称。"""

_TASK_ROUTING = """任务处理：安排/派发待办用 arrange_task（出两步卡，参数不全也先出卡，不要文字追问）；
查询自己的待办用 list_my_todos，完成后用 request_todo_done 申请完成（不涉及用 request_todo_na）；
派发人审核用 review_submissions（AI 预审 pre_review_todos），不涉及确认用 confirm_na_requests，
催办用 remind_pending，追加处理人用 append_task_assignees，结束任务用 end_task_flow，全貌用 task_overview。"""

_DATA_ROUTING = """数据查询：用户要查数据/统计/趋势时调 query_data（传用户原话；答案由系统直接流式呈现，
工具返回后不要复述答案）；Excel 填报任务派发优先用 arrange_task 让用户在卡上选动态模板。"""

_GUARDRAILS = """规则：涉及数据填报/下发的动作必须先调用对应工具；不要臆造企业名称，先用 list_companies 确认；
归属期用 YYYY-MM 或用户给定的自然期间。回答用中文，简明专业。"""


def register_builtin_sections() -> None:
    """进程启动装配期调用（幂等：override=True 允许重复执行）。"""
    for name, order, text in (
        ("base_persona", 10, _BASE_PERSONA),
        ("xuanpu_link", 20, _XUANPU_LINK),
        ("task_routing", 30, _TASK_ROUTING),
        ("data_routing", 40, _DATA_ROUTING),
        ("guardrails", 50, _GUARDRAILS),
    ):
        prompt_registry.register(PromptSection(name, order, lambda t=text: t), override=True)


register_builtin_sections()

# ---------------- 插件层（manifest.agent_prompt → plugin:<name>，启动装配期注册） ----------------


def register_plugin_sections(manifests: list[dict]) -> None:
    for m in manifests:
        prompt = str(m.get("agent_prompt") or "").strip()
        name = f"plugin:{m.get('name', '?')}"
        if not prompt:
            prompt_registry.unregister(name)
            continue
        prompt_registry.register(
            PromptSection(name, 60, lambda p=prompt: p), override=True
        )


# ---------------- 技能层（P5 渐进披露；每 run 动态 sections，不改全局注册表） ----------------

# 技能轮次输出呈现约定（前端对话流已支持 Markdown 渲染，与技能流程的断点/交付规范配套）
SKILL_OUTPUT_STYLE = (
    "输出呈现约定（前端支持 Markdown 渲染）："
    "① 过程可见：每个阶段开始先输出一行阶段标题（如「**S0: 调研范围确认**」），"
    "每批工具调用前用一句话说明本步目的，不要连续静默调用多个工具；"
    "② 结构化表达：确认表、核对清单、对比结论一律用 Markdown 表格/列表呈现，关键结论加粗；"
    "③ 最终汇报：交付物用表格列出（文件、说明），随后给出执行摘要与质量/缺口说明。"
)

_SKILL_LANG_RULE = (
    "技能约定不改变输出语言：无论指引、脚本输出为何种语言，对用户的一切输出必须是中文"
    "（专有名词、代码、命令、路径除外）。"
)
_SKILL_REAL_RULE = (
    "必须真实调用工具获取结果，不得凭空假设脚本内容或输出。"
    "指引要求向用户确认或补充信息时，先输出问题等待用户回复，再继续推进。"
)


def _local_pkg_rules() -> str:
    return (
        "技能约定补充：本技能的文件读写与脚本执行一律使用本地技能包三工具——"
        "读取包内参考文件用 read_skill_file；执行包内脚本（如 orchestrator.sh）用 run_skill_script"
        "（script 传包内相对路径，arguments 传参数数组）；"
        "写中间文件放 .work/ 子目录，交付产物按指引路径用 write_skill_file 落盘"
        "（如 reports/ 下的报告，单文件上限 4MB，长文可 append 分块追加）。"
        "不要调用 xuanpu_skill_script / xuanpu_skill_run，脚本一律经 run_skill_script 在本地执行，"
        "产物（报告 MD 与 PDF）落在本地技能包内并真实落盘。"
    )


def _local_pkg_rules_short() -> str:
    return (
        "技能约定补充：指引中要求执行包内脚本（如 orchestrator.sh）时，"
        "必须调用 run_skill_script 工具实际执行（script 传指引中的包内相对路径，"
        "arguments 传参数数组）；要求读取包内参考文件时用 read_skill_file；"
        "写中间文件放 .work/ 子目录，交付产物按指引路径用 write_skill_file 落盘"
        "（如 reports/ 下的报告，单文件上限 4MB，长文可 append 分块追加）。"
    )


def _market_rules() -> str:
    return (
        "技能约定补充：指引中要求执行包内脚本（如 orchestrator.sh）时，"
        "必须调用 xuanpu_skill_script 工具实际执行（skill 传技能名，script 传指引中的包内相对路径，"
        "arguments 传参数数组的 JSON 字符串）；要求发起技能运行时用 xuanpu_skill_run。"
    )


def skill_sections(skill_key: str, skill_source: str, skill_prompt: str | None) -> list[PromptSection]:
    """技能轮次动态 section（P5：旧 executor 三分支收敛为数据驱动单实现）。

    - xuanpu + 本地同名包：指引来自市场，文件/脚本由本地包三工具承接（真实落盘）；
    - 本地技能：包内脚本必须 run_skill_script 实际执行；
    - 仅市场指引：包内脚本走 xuanpu_skill_script。
    """
    if not (skill_key or skill_prompt):
        return []
    if skill_key and skill_source == "xuanpu":
        title, supplement = (
            f"技能市场技能，本地同名技能包 {skill_key} 提供文件工具",
            _local_pkg_rules(),
        )
    elif skill_key:
        title, supplement = f"本地技能 {skill_key}", _local_pkg_rules_short()
    else:
        title, supplement = "技能市场技能", _market_rules()
    body = f"# 当前技能约定（{title}）\n{skill_prompt}\n\n{supplement}{_SKILL_REAL_RULE}{_SKILL_LANG_RULE}{SKILL_OUTPUT_STYLE}"
    return [PromptSection(f"skill:{skill_key or 'xuanpu'}", 80, lambda b=body: b)]


def skills_catalog_section() -> PromptSection | None:
    """无技能轮次时的目录披露（DSH 渐进披露第 1 层：只注目录不注正文）。"""
    from ...services import skill_files

    skills = skill_files.list_file_skills()
    if not skills:
        return None
    lines = "\n".join(
        f"- {s['name']}（key={s['skill_key']}）：{s['desc'] or '—'}；触发方式：{s['trigger']}"
        for s in skills
    )
    text = (
        "# 可用技能目录\n"
        "以下预设技能可端到端完成对应业务流程（多阶段执行与文件交付）。"
        "用户明确想完成这类事项时，提示其可通过技能入口或直接对话（如「用 XX 技能处理…」）发起，"
        "系统会在下一轮自动装配该技能并按其执行指引推进；本轮不要臆造技能的内部步骤。\n" + lines
    )
    return PromptSection("skills:catalog", 70, lambda t=text: t)
