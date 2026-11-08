"""P1/P5 改造回归：提示词组装器（section 注册表）+ 技能渐进披露。"""
from __future__ import annotations

import pytest

from app.platform.agent import prompt as P


@pytest.fixture(autouse=True)
def _prompt_cleanup():
    before = {s.name: s for s in P.prompt_registry.sections()}
    yield
    P.prompt_registry._sections.clear()
    P.prompt_registry._sections.update(before)


def test_builtin_sections_assembled_in_order():
    text = P.prompt_registry.assemble()
    assert "XinHere（新在这里，心在这里）的财务智能助手" in text
    assert "任务处理" in text and "规则" in text
    assert text.index("XinHere") < text.index("任务处理") < text.index("规则")


def test_duplicate_section_fail_loud_and_override():
    s1 = P.PromptSection("x", 99, lambda: "v1")
    P.prompt_registry.register(s1)
    with pytest.raises(RuntimeError, match="重复注册"):
        P.prompt_registry.register(P.PromptSection("x", 99, lambda: "v2"))
    P.prompt_registry.register(P.PromptSection("x", 99, lambda: "v2"), override=True)
    assert "v2" in P.prompt_registry.assemble()


def test_extra_sections_sorted_and_conflict_fail_loud():
    extra = [P.PromptSection("b", 1, lambda: "B"), P.PromptSection("a", 1, lambda: "A")]
    text = P.prompt_registry.assemble(extra)
    assert text.startswith("A\n\nB")
    with pytest.raises(RuntimeError, match="重复注册"):
        P.prompt_registry.assemble([P.PromptSection("guardrails", 50, lambda: "zzz")])


def test_skill_sections_data_driven_branches():
    mkt = P.skill_sections("fin-report", "xuanpu", "指引正文")
    assert mkt[0].name == "skill:fin-report"
    assert "技能市场技能，本地同名技能包 fin-report" in mkt[0].text()
    assert "run_skill_script" in mkt[0].text()
    local = P.skill_sections("fin-report", "", "指引正文")
    assert "本地技能 fin-report" in local[0].text()
    assert "run_skill_script" in local[0].text() and "xuanpu_skill_run" not in local[0].text()
    market_only = P.skill_sections("", "xuanpu", "指引正文")
    assert "# 当前技能约定（技能市场技能）" in market_only[0].text()
    assert "xuanpu_skill_script" in market_only[0].text() and "run_skill_script" not in market_only[0].text()
    assert P.skill_sections("", "", None) == []
    assert P.skill_sections("", "", "") == []


def test_skills_catalog_progressive_disclosure():
    s = P.skills_catalog_section()
    if s is not None:  # 仓库带 skills/ 目录时
        assert s.name == "skills:catalog"
        assert "不要臆造技能的内部步骤" in s.text()
        assert "触发方式" in s.text()  # 只注目录（name+desc+trigger），不含执行正文


def test_register_plugin_sections():
    P.register_plugin_sections([{"name": "demo-plugin", "agent_prompt": "插件路由规则"}])
    text = P.prompt_registry.assemble()
    assert "插件路由规则" in text
    P.register_plugin_sections([{"name": "demo-plugin", "agent_prompt": ""}])  # 清空 → 注销
    assert "插件路由规则" not in P.prompt_registry.assemble()
