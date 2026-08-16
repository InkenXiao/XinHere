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


def plugin_tools(ctx: ToolCtx) -> list:
    tools: list = []
    for p in discover():
        make = getattr(p["module"], "make_tools", None)
        if make:
            tools.extend(make(ctx))
    return tools
