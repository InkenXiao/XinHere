"""P2 改造回归：插件事件注册 + 组件处理器注册表（平台层无业务实现）。"""
from __future__ import annotations

import pytest

from app.platform.agent import builtin_handlers, component_handlers
from app.platform.events.registry import REGISTRY, register_plugin_events, validate_payload


@pytest.fixture(autouse=True)
def _registry_cleanup():
    """注册表是进程级全局，测试后回卷，避免污染其他用例。"""
    before_events = dict(REGISTRY)
    before_handlers = dict(component_handlers.HANDLERS)
    before_drafts = dict(component_handlers.DRAFT_HANDLERS)
    yield
    REGISTRY.clear()
    REGISTRY.update(before_events)
    component_handlers.HANDLERS.clear()
    component_handlers.HANDLERS.update(before_handlers)
    component_handlers.DRAFT_HANDLERS.clear()
    component_handlers.DRAFT_HANDLERS.update(before_drafts)


def test_plugin_event_registration_and_validation():
    manifests = [{"name": "demo", "events": ["demo/alpha"], "event_ignorable": ["demo/alpha"]}]
    register_plugin_events(manifests)
    payload = validate_payload("demo/alpha", {"x": 1})
    assert payload["x"] == 1  # 宽松模型 extra=allow


def test_plugin_event_conflict_with_builtin_fail_loud():
    conflict = next(iter(REGISTRY))
    with pytest.raises(RuntimeError, match="重复注册"):
        register_plugin_events([{"name": "bad", "events": [conflict]}])


def test_builtin_handlers_registered_via_register_channel():
    builtin_handlers.register_builtin_handlers()  # 幂等
    assert "risk-dispatch-confirm" in component_handlers.HANDLERS
    assert "todo-end-card" in component_handlers.HANDLERS
    assert "cash-guarantee-form" in component_handlers.DRAFT_HANDLERS
    builtin_handlers.register_builtin_handlers()  # 二次注册不抛


def test_handler_kind_conflict_fail_loud():
    builtin_handlers.register_builtin_handlers()

    def _other(**kw):  # noqa: ANN003
        raise AssertionError("不应被调用")

    with pytest.raises(RuntimeError, match="重复注册"):
        component_handlers.register("risk-dispatch-confirm", _other)


def test_apply_update_draft_dispatch():
    builtin_handlers.register_builtin_handlers()
    props = {"form_id": "F1"}
    draft = {"avail_cash": 1.0, "pooled_fund": 2.0, "avail_credit": 3.0, "monthly_outflow": 4.0}
    r = component_handlers.apply_update_draft(None, kind="cash-guarantee-form", props=props, draft=draft)
    assert r == ("cash/form-field-update", {"form_id": "F1", "draft": draft, "version": 1})
    assert component_handlers.apply_update_draft(None, kind="unknown-kind", props={}, draft={}) is None
