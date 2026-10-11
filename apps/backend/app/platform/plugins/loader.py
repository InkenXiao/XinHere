from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from ..agent.tool_base import ToolCtx

_PLUGINS_ROOT = Path(__file__).resolve().parents[5] / "plugins"

_plugins: list[dict] | None = None


def discover() -> list[dict]:
    """发现装配 plugins/*/backend；重名工具/事件/组件 kind fail-loud。"""
    global _plugins
    if _plugins is not None:
        return _plugins
    found: list[dict] = []
    seen_tools: set[str] = set()
    seen_events: set[str] = set()
    seen_kinds: set[str] = set()
    seen_names: set[str] = set()
    for plugin_dir in sorted(_PLUGINS_ROOT.iterdir() if _PLUGINS_ROOT.exists() else []):
        manifest_path = plugin_dir / "plugin.json"
        backend_init = plugin_dir / "backend" / "__init__.py"
        if not manifest_path.exists() or not backend_init.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        name = manifest["name"]
        if name in seen_names:
            raise RuntimeError(f"插件重名: {name}")
        seen_names.add(name)
        for t in manifest.get("tools", []):
            if t in seen_tools:
                raise RuntimeError(f"工具重名: {t} (插件 {name})")
            seen_tools.add(t)
        for e in manifest.get("events", []):
            if e in seen_events:
                raise RuntimeError(f"事件重名: {e} (插件 {name})")
            seen_events.add(e)
        comp = manifest.get("component") or {}
        for kind in comp.get("kinds", [comp.get("kind")] if comp.get("kind") else []):
            if kind in seen_kinds:
                raise RuntimeError(f"组件 kind 重名: {kind} (插件 {name})")
            seen_kinds.add(kind)
        # 插件名含 "-" 且 backend/__init__.py 用相对导入：须按包加载并注册 sys.modules
        mod_name = f"xinhere_plugin_{name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(
            mod_name, backend_init, submodule_search_locations=[str(backend_init.parent)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        found.append({"manifest": manifest, "module": module})
    _plugins = found
    return found


def plugin_manifests() -> list[dict]:
    return [
        {
            "name": p["manifest"]["name"],
            "version": p["manifest"]["version"],
            "domain": p["manifest"]["domain"],
            "tools": p["manifest"].get("tools", []),
            "events": p["manifest"].get("events", []),
        }
        for p in discover()
    ]


def plugin_set_locked() -> tuple[list[dict], str]:
    """会话创建时锁定的插件清单 + hash。"""
    items = sorted(
        ({"name": p["manifest"]["name"], "version": p["manifest"]["version"]} for p in discover()),
        key=lambda x: x["name"],
    )
    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:32]
    return items, digest


def agent_plugins() -> list[dict]:
    """声明 agent_tools=true 的插件：工具进对话图 + agent_prompt 进系统提示词。
    未声明的插件（如演示期 risk-warning）保持不装配，平台行为不变。"""
    return [p for p in discover() if p["manifest"].get("agent_tools")]


def plugin_tools(ctx: ToolCtx) -> list:
    tools: list = []
    for p in agent_plugins():
        make = getattr(p["module"], "make_tools", None)
        if make:
            tools.extend(make(ctx))
    return tools


def plugin_agent_prompt() -> str:
    """拼接 agent_tools 插件的路由规则提示词（无插件返回空串，SYSTEM_PROMPT 原样）。"""
    parts = [str(p["manifest"].get("agent_prompt") or "").strip() for p in agent_plugins()]
    parts = [x for x in parts if x]
    return ("\n" + "\n".join(parts) + "\n") if parts else ""


def plugin_routers() -> list:
    """插件自带 REST：backend 暴露非空 router 才挂载（空占位 router 跳过）。"""
    out = []
    for p in discover():
        r = getattr(p["module"], "router", None)
        if r is not None and getattr(r, "routes", None):
            out.append(r)
    return out
