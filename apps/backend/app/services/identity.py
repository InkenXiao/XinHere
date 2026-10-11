"""身份与组织目录服务：读主数据库 sii_eosts（BIZ_PG_URI，只读）。

主数据来源（芋道风格 SCMS 业务库）：
- system_users（id=SCMS user_id）+ system_role/system_user_role → 角色码
- system_users.enterprise_id → sii_enterprise → 所属企业
- system_dept → 组织架构树；sii_org_company → 业务公司名录
- sii_user_org_company_scope → 用户负责的业务公司范围

角色码 → 内核角色映射（数据驱动，调整只改常量）：
- super_admin / XTBM-JCB(本部计财部) / XT_CWYJSH(财务预警审核) / BBCWRY(本部财务人员)
  → hq_finance（审核/派发/调灯方）
- QYYYRE(企业运营人员) → company_operator（填报方，需求A「各公司运营人员」）
- QYCWRE(企业财务人员) → investee_finance
- 其余 → member
"""
from __future__ import annotations

import json
import logging
import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ..core.config import settings

logger = logging.getLogger(__name__)

CACHE_TTL = 300  # 身份解析缓存 5 分钟（主数据变更低频）

HQ_FINANCE_CODES = {"super_admin", "XTBM-JCB", "XT_CWYJSH", "BBCWRY"}
COMPANY_OPERATOR_CODES = {"QYYYRE"}
INVESTEE_FINANCE_CODES = {"QYCWRE"}

_HQ_ENTERPRISE_ID = 1  # 上海市信息投资股份有限公司（本部）


class _IdentityCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, dict | None]] = {}

    def get(self, key: str) -> dict | None | bool:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return False  # 未缓存
            ts, val = hit
            if time.time() - ts > CACHE_TTL:
                del self._data[key]
                return False
            return val  # dict 或 None（负缓存）

    def put(self, key: str, val: dict | None) -> None:
        with self._lock:
            self._data[key] = (time.time(), val)


_cache = _IdentityCache()
_engine: Engine | None = None
_engine_lock = threading.Lock()


def _biz_engine() -> Engine | None:
    """主数据库只读引擎（懒加载单例；未配置 BIZ_PG_URI 返回 None）。"""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        uri = settings.biz_pg_uri
        if not uri:
            return None
        if uri.startswith("postgresql://"):  # 统一走 psycopg3（venv 未装 psycopg2）
            uri = uri.replace("postgresql://", "postgresql+psycopg://", 1)
        _engine = create_engine(uri, pool_pre_ping=True, pool_size=4, max_overflow=4)
    return _engine


def _map_role(codes: list[str]) -> str:
    cs = set(codes)
    if cs & HQ_FINANCE_CODES:
        return "hq_finance"
    if cs & COMPANY_OPERATOR_CODES:
        return "company_operator"
    if cs & INVESTEE_FINANCE_CODES:
        return "investee_finance"
    return "member"


def _row_to_identity(row) -> dict:
    user_id, username, nickname, role_codes, role_names, enterprise_id, enterprise_name, dept_id = row
    role = _map_role(role_codes or [])
    company = ""
    if enterprise_name and enterprise_id != _HQ_ENTERPRISE_ID:
        company = enterprise_name
    return {
        "user_id": str(user_id),
        "username": username or "",
        "display_name": nickname or username or str(user_id),
        "role": role,
        "role_codes": role_codes or [],
        "role_names": role_names or [],
        "company": company,
        "enterprise_id": enterprise_id,
        "dept_id": dept_id,
    }


_SQL_IDENTITY = text("""
    select u.id, u.username, u.nickname,
           array_agg(distinct r.code) filter (where r.code is not null),
           array_agg(distinct r.name) filter (where r.name is not null),
           u.enterprise_id, e.name, u.org_dept_id
    from system_users u
    left join system_user_role ur on ur.user_id = u.id and ur.deleted = 0
    left join system_role r on r.id = ur.role_id
    left join sii_enterprise e on e.id = u.enterprise_id
    where u.id = :uid and u.deleted = 0 and u.status = 0
    group by u.id, u.username, u.nickname, u.enterprise_id, e.name, u.org_dept_id
""")


def resolve_user(scms_user_id: str | int) -> dict | None:
    """SCMS user_id → 内核身份（角色/公司）。失败返回 None（调用方自行降级）。"""
    key = str(scms_user_id)
    if not key or key == "anonymous":
        return None
    cached = _cache.get(key)
    if cached is not False:
        return cached  # type: ignore[return-value]
    eng = _biz_engine()
    ident: dict | None = None
    if eng is not None:
        try:
            with eng.connect() as conn:
                row = conn.execute(_SQL_IDENTITY, {"uid": int(key)}).first()
                if row is not None:
                    ident = _row_to_identity(row)
        except (ValueError, TypeError):
            ident = None
        except Exception:
            logger.warning("identity 解析失败（降级匿名） uid=%s", key, exc_info=True)
            ident = None
    _cache.put(key, ident)
    return ident


# ---------------- 组织目录（@ 派发对象搜索） ----------------

_DIRECTORY_TYPES = ("user", "role", "dept", "company", "group")


def search_directory(q: str = "", types: list[str] | None = None, limit: int = 200, role_code: str | None = None) -> dict[str, list]:
    """@ 选择器数据源：用户/角色/组织架构/公司/用户组（名称前缀或包含匹配）。
    返回 {type: [{id, label, extra}...]}，id 均为展开目标可直接引用的标识。"""
    eng = _biz_engine()
    if eng is None:
        return {t: [] for t in _DIRECTORY_TYPES}
    want = [t for t in (types or list(_DIRECTORY_TYPES)) if t in _DIRECTORY_TYPES]
    out: dict[str, list] = {}
    with eng.connect() as conn:
        if "user" in want:
            out["user"] = [
                {"id": str(r[0]), "label": r[1] or r[2], "extra": r[3] or ""}
                for r in conn.execute(text("""
                    select u.id, u.nickname, u.username, e.short_name
                    from system_users u left join sii_enterprise e on e.id = u.enterprise_id
                    where u.deleted = 0 and u.status = 0
                      and (cast(:role_code as varchar) is null or exists (
                        select 1 from system_user_role ur join system_role r on r.id = ur.role_id
                        where ur.user_id = u.id and ur.deleted = 0 and r.deleted = 0 and r.code = :role_code
                      ))
                      and (:q = '' or strpos(u.nickname, :q) > 0 or strpos(u.username, :q) > 0)
                    order by u.id limit :lim
                """), {"q": q.strip(), "lim": limit, "role_code": role_code})
            ]
        if "role" in want:
            out["role"] = [
                {"id": r[2], "label": r[1], "extra": ""}
                for r in conn.execute(text("""
                    select id, name, code from system_role
                    where deleted = 0 and status = 0
                      and (:q = '' or strpos(name, :q) > 0 or strpos(code, :q) > 0)
                    order by sort limit :lim
                """), {"q": q.strip(), "lim": limit})
            ]
        if "dept" in want:
            out["dept"] = [
                {"id": str(r[0]), "label": r[1], "extra": str(r[2])}
                for r in conn.execute(text("""
                    select id, name, parent_id from system_dept
                    where deleted = 0 and status = 0
                      and (:q = '' or strpos(name, :q) > 0)
                    order by sort limit :lim
                """), {"q": q.strip(), "lim": limit})
            ]
        if "company" in want:
            out["company"] = [
                {"id": str(r[0]), "label": r[1] or r[2], "extra": ""}
                for r in conn.execute(text("""
                    select company_id, short_name, org_name from sii_org_company
                    where is_deleted = 0 and is_active = 1
                      and (:q = '' or strpos(org_name, :q) > 0 or strpos(short_name, :q) > 0)
                    order by sort_order limit :lim
                """), {"q": q.strip(), "lim": limit})
            ]
        if "group" in want:
            out["group"] = [
                {"id": str(r[0]), "label": r[1], "extra": ""}
                for r in conn.execute(text("""
                    select id, name from bpm_user_group
                    where deleted = 0 and status = 0
                      and (:q = '' or strpos(name, :q) > 0)
                    order by id limit :lim
                """), {"q": q.strip(), "lim": limit})
            ]
    for t in _DIRECTORY_TYPES:
        out.setdefault(t, [])
    return out


_SQL_DEPT_USER_IDS = text("""
    with recursive sub(id) as (
        select :dept_id
        union all
        select d.id from system_dept d join sub s on d.parent_id = s.id where d.deleted = 0
    )
    select distinct u.id from system_users u
    join system_user_role ur on ur.user_id = u.id and ur.deleted = 0
    join system_role r on r.id = ur.role_id
    where u.deleted = 0 and u.status = 0
      and (u.org_dept_id in (select id from sub) or u.dept_id in (select id from sub)
           or r.code = :role_code)
""")


def expand_targets(targets: list[dict]) -> dict:
    """@ 目标 → 具体用户 id 集合（派发用）。
    targets: [{type: user|role|dept|company|group, id}]；返回 {user_ids, labels, unknown}。
    role→挂该角色的用户；dept→子树内用户；company→enterprise_id/company 归属用户；group→user_ids 数组。"""
    eng = _biz_engine()
    if eng is None:
        return {"user_ids": [], "labels": [], "unknown": [t for t in targets]}
    user_ids: set[str] = set()
    labels: list[str] = []
    unknown: list[dict] = []
    with eng.connect() as conn:
        for t in targets:
            typ, tid = t.get("type"), str(t.get("id", ""))
            found: list[str] = []
            label = t.get("label") or tid
            if typ == "user":
                row = conn.execute(text(
                    "select id from system_users where id = :i and deleted = 0 and status = 0"
                ), {"i": _to_int(tid)}).first()
                found = [str(row[0])] if row else []
            elif typ == "role":
                found = [str(r[0]) for r in conn.execute(text("""
                    select u.id from system_users u
                    join system_user_role ur on ur.user_id = u.id and ur.deleted = 0
                    join system_role r on r.id = ur.role_id
                    where r.code = :c and u.deleted = 0 and u.status = 0
                """), {"c": tid})]
                label = (conn.execute(text(
                    "select name from system_role where code = :c and deleted = 0"
                ), {"c": tid}).scalar()) or label
            elif typ == "dept":
                found = [str(r[0]) for r in conn.execute(_SQL_DEPT_USER_IDS, {
                    "dept_id": _to_int(tid), "role_code": None,
                })]
            elif typ == "company":
                found = [str(r[0]) for r in conn.execute(text("""
                    select u.id from system_users u
                    left join sii_enterprise e on e.id = u.enterprise_id
                    where u.deleted = 0 and u.status = 0
                      and (u.enterprise_id = :cid
                           or e.short_name = :name or e.name = :name)
                """), {"cid": _to_int(tid), "name": t.get("label") or tid})]
            elif typ == "group":
                ids = conn.execute(text(
                    "select user_ids from bpm_user_group where id = :i and deleted = 0"
                ), {"i": _to_int(tid)}).scalar()
                found = [str(x) for x in (json.loads(ids) if ids else [])]
            else:
                unknown.append(t)
                continue
            if not found:
                unknown.append(t)
                continue
            user_ids.update(found)
            labels.append(label)
    return {"user_ids": sorted(user_ids), "labels": labels, "unknown": unknown}


def _to_int(v: str) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------- 组织架构树（树选数据源） ----------------

_TREE_TTL = 300  # 全量树进程内缓存（部门/用户主数据低频变更）
_tree_cache: tuple[float, dict | None] = (0.0, None)


def _build_full_tree(eng: Engine) -> dict:
    """全量组织树：部门按 parent_id 组装，用户按 org_dept_id 挂载，count 为子树总人数。
    无部门归属（org_dept_id 为空或指向已删部门）的用户不进树，仅在用户列表/搜索平铺中出现。"""
    with eng.connect() as conn:
        depts = conn.execute(text(
            "select id, name, parent_id from system_dept "
            "where deleted = 0 and status = 0 order by sort, id"
        )).all()
        users = conn.execute(text("""
            select u.id, coalesce(u.nickname, u.username), u.org_dept_id, e.short_name
            from system_users u left join sii_enterprise e on e.id = u.enterprise_id
            where u.deleted = 0 and u.status = 0
        """)).all()

    nodes: dict[int, dict] = {}
    for did, name, parent_id in depts:
        nodes[did] = {"id": str(did), "label": name, "count": 0, "users": [], "children": []}
    roots: list[dict] = []
    for did, name, parent_id in depts:
        node = nodes[did]
        parent = nodes.get(parent_id) if parent_id else None
        (parent["children"] if parent is not None else roots).append(node)

    users_by_dept: dict[int, list[dict]] = {}
    for uid, uname, dept_id, short_name in users:
        users_by_dept.setdefault(dept_id, []).append(
            {"id": str(uid), "label": uname or str(uid), "extra": short_name or ""}
        )
    for did, members in users_by_dept.items():
        if did in nodes:
            nodes[did]["users"] = members

    def _finalize(node: dict) -> int:
        total = len(node["users"])
        for ch in node["children"]:
            total += _finalize(ch)
        node["count"] = total
        return total

    for r in roots:
        _finalize(r)
    return {"tree": roots, "matched_users": []}


def _filter_tree(full: dict, q: str) -> dict:
    """树内搜索：部门名命中 → 保留整棵子树；否则保留含命中用户/子孙的路径。
    同时平铺返回全部命中用户（跨部门，列表兜底）。"""
    qq = q.strip()
    all_users = [u for node in full["tree"] for u in _iter_users(node)]
    matched_users = [u for u in all_users if qq in u["label"]]

    def _prune(node: dict) -> dict | None:
        hit_self = qq in node["label"]
        if hit_self:
            return node  # 整棵子树保留（含未命中子孙）
        hit_users = [u for u in node["users"] if qq in u["label"]]
        children = [c for c in (_prune(ch) for ch in node["children"]) if c is not None]
        if not hit_users and not children:
            return None
        return {**node, "users": hit_users, "children": children}

    return {
        "tree": [n for n in (_prune(r) for r in full["tree"]) if n is not None],
        "matched_users": matched_users[:100],
    }


def _iter_users(node: dict):
    yield from node["users"]
    for ch in node["children"]:
        yield from _iter_users(ch)


def org_tree(q: str = "") -> dict:
    """组织架构树（树选模式数据源）：{tree: [{id,label,count,users,children}], matched_users}。
    q 为空返回全量树；非空返回过滤树（祖先链保留）+ 命中用户平铺。"""
    eng = _biz_engine()
    if eng is None:
        return {"tree": [], "matched_users": []}
    global _tree_cache
    now = time.time()
    full = _tree_cache[1] if now - _tree_cache[0] < _TREE_TTL else None
    if full is None:
        full = _build_full_tree(eng)
        _tree_cache = (now, full)
    qq = q.strip()
    return full if not qq else _filter_tree(full, qq)


def user_names(ids: list[str]) -> dict[str, str]:
    """批量 id → 显示名（安排待办确认卡展示用）。"""
    eng = _biz_engine()
    if eng is None or not ids:
        return {}
    ints = [i for i in (_to_int(str(x)) for x in ids) if i is not None]
    if not ints:
        return {}
    with eng.connect() as conn:
        rows = conn.execute(text(
            "select id, coalesce(nickname, username) from system_users "
            "where deleted = 0 and id = any(:ids)"
        ), {"ids": ints}).all()
    return {str(r[0]): r[1] for r in rows}


def resolve_labels_to_users(labels: list[str]) -> dict:
    """@ 标签 → 处理人明细（AI 安排待办用）：目录精确命中优先、前缀包含兜底，
    非 user 类型（角色/部门/公司/用户组）展开为具体用户。返回 {assignees: [{user_id, name}], unknown}。"""
    ids: set[str] = set()
    unknown: list[str] = []
    for lb in labels:
        lb = lb.strip().lstrip("@").strip()
        if not lb:
            continue
        d = search_directory(lb, None, 20)
        hit: dict | None = None
        for typ in ("user", "role", "dept", "company", "group"):
            for e in d.get(typ) or []:
                if e["label"] == lb or e["label"].startswith(lb):
                    hit = {"type": typ, "id": e["id"], "label": e["label"]}
                    break
            if hit:
                break
        if hit is None:
            unknown.append(lb)
            continue
        r = expand_targets([hit])
        if not r["user_ids"]:
            unknown.append(lb)
            continue
        ids.update(r["user_ids"])
    names = user_names(sorted(ids))
    return {
        "assignees": [{"user_id": i, "name": names.get(i, i)} for i in sorted(ids)],
        "unknown": unknown,
    }
