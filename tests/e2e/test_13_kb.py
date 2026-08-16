"""矩阵 13：知识库。

- GET /kb/sources → 7 节点树（项目知识库含 信投股份/信息建设 两子节点）；
- POST /kb/search {query:'风险'} → 200 hits 数组 或 502 UPSTREAM_ERROR，两分支皆过并记录实际分支。
"""
from __future__ import annotations

import pytest

EXPECTED_NAMES = {
    "企业知识库", "部门知识库", "项目知识库", "信投股份", "信息建设", "外部知识库", "个人知识库",
}


def test_kb_sources_tree(hq):
    items = hq.ok("GET", "/kb/sources")["items"]
    assert len(items) == 7, f"知识库节点应为 7: {len(items)}"
    names = {i["name"] for i in items}
    assert names == EXPECTED_NAMES, f"知识库节点偏差: {names}"
    by_name = {i["name"]: i for i in items}
    proj = by_name["项目知识库"]
    children = [i for i in items if i["parent_id"] == proj["kb_id"]]
    assert {c["name"] for c in children} == {"信投股份", "信息建设"}, "项目知识库子节点偏差"
    assert all(i["kb_type"] in ("internal", "external") for i in items)
    assert by_name["外部知识库"]["kb_type"] == "external"
    roots = [i for i in items if i["parent_id"] is None]
    assert len(roots) == 5, f"根节点应为 5: {[r['name'] for r in roots]}"


def test_kb_search_hit_or_graceful_502(hq):
    r = hq.post("/kb/search", json={"query": "风险"})
    if r.status_code == 200:
        body = r.json()
        assert isinstance(body.get("hits"), list), f"hits 应为数组: {body}"
        for h in body["hits"]:
            assert set(h.keys()) >= {"title", "snippet", "source"}
        print(f"\n[kb-search] 分支=200 hits={len(body['hits'])}")
    elif r.status_code == 502:
        body = r.json()
        assert body.get("code") == "UPSTREAM_ERROR", f"502 错误码偏差: {body}"
        print("\n[kb-search] 分支=502 UPSTREAM_ERROR（MCP 不可达，优雅降级）")
    else:
        pytest.fail(f"/kb/search 非预期状态码 {r.status_code}: {r.text[:300]}")
