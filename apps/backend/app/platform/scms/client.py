"""SCMS 业务接口客户端（指标/里程碑，字段对齐 scms-frontend company-management-indicator 模块）。

凭证（透传模式）：优先当前请求的用户 accessToken（token_auth 上下文），无用户上下文
（后台恢复等）回落服务账号；header 走 X-SCMS-Access-Token + tenant-id（租户号启动时懒取一次）。
写操作（审核/备注）始终以当前凭证身份执行，不做 401 中途换号——权限模型归 SCMS。
仅审核方链路：企业树 → 指标配置 → 里程碑分页 → 审核更新/备注。
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

from ...core.config import settings

logger = logging.getLogger(__name__)

_tenant_lock = threading.Lock()
_tenant_id: str | None = None


def _token() -> str:
    # 凭证单点：用户 token 优先（tools._nl2sql_token 同源逻辑，独立导入避免环），
    # 无用户上下文回落服务账号
    from ..agent.tools import _nl2sql_token

    return _nl2sql_token()


def _token_source() -> str:
    from ..agent.tools import _token_source

    return _token_source()


def _tenant() -> str:
    global _tenant_id
    with _tenant_lock:
        if _tenant_id is not None:
            return _tenant_id
        try:
            # 查租户自身不带 tenant-id 头：否则 _request→_tenant→_request 同线程重入锁死锁
            data = _request("GET", "/admin-api/system/tenant/get-id-by-name",
                            query={"name": settings.scms_tenant_name}, _with_tenant=False)
            _tenant_id = str(data or "")
        except Exception as exc:
            logger.warning("SCMS 租户换号失败（空 tenant 继续重试后续请求） err=%r", exc)
            _tenant_id = ""
        return _tenant_id


def _request(method: str, path: str, *, query: dict | None = None, body: dict | list | None = None,
             timeout: int = 20, _with_tenant: bool = True) -> dict | list | None:
    """SCMS REST 请求：yudao 包裹体 {code,msg,data}，code!=0 抛 RuntimeError。"""
    base = settings.scms_api_base.rstrip("/")
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {
        "Content-Type": "application/json",
        # admin-api 为 yudao 标准鉴权：Authorization Bearer；X-SCMS-Access-Token 一并带上
        # 兼容自定义过滤器（问数 ai-api 同名头约定）
        "Authorization": f"Bearer {_token()}",
        "X-SCMS-Access-Token": _token(),
    }
    if _with_tenant:
        headers["tenant-id"] = _tenant()
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            wrapped = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("msg", "")
        except Exception:
            msg = exc.reason or ""
        raise RuntimeError(f"SCMS HTTP {exc.code}: {msg}（凭证:{_token_source()}）") from None
    if wrapped.get("code") != 0:
        raise RuntimeError(f"SCMS {path} 失败: {wrapped.get('msg')}（凭证:{_token_source()}）")
    return wrapped.get("data")


# ---------- 指标分类体系（SCMS category 编码） ----------
# 01 经营 / 02 战略 / 03 管理 / 04 约束 / 05 党建（财务指标为独立模块，无 category）
CATEGORY_CODE = {"经营": "01", "战略": "02", "管理": "03", "约束": "04", "党建": "05"}


def category_code(name: str | None) -> str:
    """中文名/category 码 → 码；未知名回落 03（管理，与审核链路同源）。"""
    if not name:
        return "03"
    return CATEGORY_CODE.get(name, name if name in CATEGORY_CODE.values() else "03")


# ---------- 业务封装 ----------

def company_tree(code: str = "03") -> list[dict]:
    """企业树（code=指标分类码）：[{companyId, shortName, ...}]。"""
    data = _request("GET", "/admin-api/org-company/treeV2", query={"code": code})
    return data or []


def find_company_id(short_name: str, code: str = "03") -> str | None:
    """按企业短名/全名找 companyId。企业树按指标分类返回：先查目标分类，
    未命中再扫其余分类兜底（同一企业可挂不同分类，任一树命中即用）。"""
    codes = [code] + [c for c in CATEGORY_CODE.values() if c != code]
    for c in codes:
        for node in company_tree(c):
            if node.get("shortName") == short_name or node.get("companyName") == short_name:
                return str(node.get("companyId"))
    return None


# 指标名同义组：财务口径叫法与指标体系叫法不一致（问数/用户话语 vs SCMS 配置）
_ALIAS_GROUPS: list[set[str]] = [
    {"营业收入", "主营营业收入", "主营业务收入", "营收"},
    {"净利润", "归母净利润", "母公司净利润", "净利润（归母）", "净利润(归母)"},
]


def indicator_match(rule_name: str, want: str) -> bool:
    """指标名宽松匹配：同义组判等 → 剥前缀（主营/归母）双向包含。
    直接子串匹配对「营业收入 vs 主营业务收入」这类口径差必失败，模型会反复换名重试。"""
    a_raw, b_raw = (rule_name or "").strip(), (want or "").strip()
    if not a_raw or not b_raw:
        return False
    for g in _ALIAS_GROUPS:
        if a_raw in g and b_raw in g:
            return True

    def norm(s: str) -> str:
        t = s
        changed = True
        while changed:
            changed = False
            for p in ("主营", "归母"):
                if t.startswith(p) and len(t) > len(p):
                    t = t[len(p):]
                    changed = True
        return t

    a, b = norm(a_raw), norm(b_raw)
    return a in b or b in a


def indicator_rules(company_id: str, year: int, category: str = "03") -> list[dict]:
    """公司指标配置（category：01经营/02战略/03管理/04约束/05党建）。"""
    data = _request("GET", "/admin-api/org-company/indicator-config",
                    query={"companyId": company_id, "year": year, "category": category})
    return data or []


def indicator_detail(rule_id: str) -> dict:
    """指标详情（view/edit 共用）：指标元数据 + 考核规则（分值/考核内容）。"""
    data = _request("GET", "/admin-api/alert-indicator/company-indicator/detail",
                    query={"id": rule_id})
    return data or {}


def milestone_stats(year: int, category: str, company_id: str | None = None) -> list[dict]:
    """拆分/审核进度统计（按企业）：indicatorCount、milestoneCount、unfinishedMilestoneCount、
    indicatorProgress、redCount/yellowCount 等；不传 companyId = 全部企业。"""
    query: dict = {"year": year, "categoryL1Code": category}
    if company_id:
        query["companyId"] = company_id
    data = _request("GET", "/admin-api/system/indicator-review/milestone/stats", query=query)
    return data or []


def milestone_page(rule_id: str, base_audit_status: str) -> list[dict]:
    """里程碑分页（baseAuditStatus：0待审核 / 1退回 / 2通过）。"""
    data = _request("GET", "/admin-api/sii/perf-assessment-milestone/page",
                    query={"ruleId": rule_id, "baseAuditStatus": base_audit_status,
                           "pageNo": 1, "pageSize": 100})
    if isinstance(data, dict):
        return data.get("list") or []
    return data or []


def milestone_update(rows: list[dict], *, operator: str) -> None:
    """审核更新（批量）：rows 携带 baseAuditStatus（2通过 / 1退回）与审计字段。"""
    for r in rows:
        r.setdefault("updaterCode", operator)
        r.setdefault("updaterName", operator)
    _request("PUT", "/admin-api/sii/perf-assessment-milestone/update", body=rows)


def milestone_remark_create(milestone_id: str, content: str, *, author: str) -> None:
    """里程碑备注（退回原因落备注，审核方可见）。"""
    _request("POST", "/admin-api/admin/perf/assessment-milestone-remark/create", body={
        "milestoneId": milestone_id,
        "content": content,
        "authorName": author,
    })
