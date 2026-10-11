"""企业名册：公司名称 → companyId 解析（报告嵌入卡拼参用）。

数据先按 SCMS treeV2 返回写死（业务约定），结构与实时接口一致，
后续切换实时接口仅改本模块。名称匹配：精确简称/全称 → 包含匹配（唯一命中才通过）。
"""
from __future__ import annotations

# (companyId, orgName, shortName) —— 源自 SCMS /admin-api/org-company/treeV2
_COMPANY_TREE: list[dict] = [
    {"companyId": 88, "orgName": "上海市信息投资股份有限公司", "shortName": "上海信投"},
    {"companyId": 108, "orgName": "智能汽车创新发展平台（上海）有限公司", "shortName": "智驾公司"},
    {"companyId": 5, "orgName": "上海市信息管线有限公司", "shortName": "管线公司"},
    {"companyId": 83, "orgName": "上海信投智能科技股份有限公司", "shortName": "信投智科"},
    {"companyId": 33, "orgName": "上海信投建设有限公司", "shortName": "信投建设"},
    {"companyId": 58, "orgName": "上海跨境电子商务公共服务有限公司", "shortName": "跨境公服"},
    {"companyId": 65, "orgName": "上海临港新片区跨境数据科技有限公司", "shortName": "跨境数科"},
    {"companyId": 76, "orgName": "上海数字安全科技有限公司", "shortName": "数安科技"},
    {"companyId": 52, "orgName": "上海信投信息服务有限公司", "shortName": "信投服务"},
    {"companyId": 51, "orgName": "上海产业转型升级私募基金管理有限公司", "shortName": "产业基金公司"},
    {"companyId": 80, "orgName": "上海库帕思科技有限公司", "shortName": "库帕思"},
    {"companyId": 78, "orgName": "上海数据交易所有限公司", "shortName": "数交所"},
]


def roster_line() -> str:
    """候选企业清单（顿号连接，供提示词反问与话术复用）。"""
    return "、".join(c["shortName"] for c in _COMPANY_TREE)


def resolve(name: str) -> dict | None:
    """名称 → 企业（companyId/orgName/shortName）。精确简称/全称优先；包含匹配须唯一，否则 None。"""
    n = (name or "").strip()
    if not n:
        return None
    exact = [c for c in _COMPANY_TREE if n in (c["shortName"], c["orgName"])]
    if len(exact) == 1:
        return exact[0]
    if not exact:
        fuzzy = [c for c in _COMPANY_TREE
                 if n in c["shortName"] or n in c["orgName"] or c["shortName"] in n]
        if len(fuzzy) == 1:
            return fuzzy[0]
    return None


def unmatched_hint(name: str, what: str) -> str:
    """解析失败的工具返回话术：列出全部企业供用户重新确认。"""
    names = "、".join(c["shortName"] for c in _COMPANY_TREE)
    return f"未唯一匹配到企业「{name}」，无法生成{what}。可选企业：{names}。请确认企业名称后重试"
