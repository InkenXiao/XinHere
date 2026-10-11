"""若依/芋道权限解析：直连业务库（BIZ_PG_URI，sii_eosts 只读），无任何内存 mock。

数据源（芋道 ruoyi-vue-pro 标准表）：
- system_users / system_dept                                —— 用户与组织
- system_role（code / data_scope / data_scope_dept_ids）     —— 角色 + 数据范围
- system_user_role / system_role_menu / system_menu.permission —— 角色→菜单→权限串

语义（若依全模式）：
- perms 权限串三段式 module:function:action，支持 * 通配（oa:task:* / *:*:*）；
- role_code == "super_admin" → *:*:* 全权限旁路（若依 isAdmin 口径）；
- data_scope：1=全部 2=自定义(system_role.data_scope_dept_ids) 3=本部门 4=本部门及以下 5=仅本人，
  多角色取最宽口径合成，用于选人目录过滤。
"""
from __future__ import annotations

import json
import logging
import threading
import time

from sqlalchemy import text

from app.core import errors
from app.services import identity as identity_svc

logger = logging.getLogger(__name__)

ALL_PERMISSION = "*:*:*"
ADMIN_ROLE_CODE = "super_admin"
_CACHE_TTL = 30  # 角色/权限缓存（主数据低频变更；种子后可 reset_cache 立即生效）

DATA_SCOPE_ZH = {
    "1": "全部数据", "2": "自定义数据", "3": "本部门数据", "4": "本部门及以下数据", "5": "仅本人数据",
}

# 语义角色别名 → 真实 SCMS 角色 code（模板 approver_role 可用任一形式）
APPROVER_ROLE_ALIASES = {
    "dept_leader": "XT_BBBMGL",  # 本部部门管理者
    "finance": "QYCWRE",         # 企业财务人员
}


def _eng():
    eng = identity_svc._biz_engine()
    if eng is None:
        raise errors.upstream("业务库未配置（BIZ_PG_URI），OA 权限不可用")
    return eng


# ---------------- 缓存 ----------------

_cache: dict[str, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()


def reset_cache() -> None:
    """清空角色/权限缓存（种子变更后 / 测试用）。"""
    with _cache_lock:
        _cache.clear()


def _cached_get(key: str):
    with _cache_lock:
        hit = _cache.get(key)
    if hit is None:
        return False  # 未缓存
    ts, val = hit
    if time.time() - ts > _CACHE_TTL:
        with _cache_lock:
            _cache.pop(key, None)
        return False
    return val


def _cached_put(key: str, val) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), val)


def _to_int(v) -> int | None:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def str_list(xs) -> list[str]:
    return [str(x) for x in xs]


# ---------------- 基础查询 ----------------

def user_by_id(uid) -> dict | None:
    with _eng().connect() as conn:
        row = conn.execute(text("""
            select u.id, u.username, coalesce(u.nickname, u.username),
                   coalesce(u.org_dept_id, u.dept_id), d.name, u.mobile, u.email
            from system_users u
            left join system_dept d on d.id = coalesce(u.org_dept_id, u.dept_id)
            where u.id = :i and u.deleted = 0 and u.status = 0
        """), {"i": _to_int(uid) or -1}).first()
    if row is None:
        return None
    return {"user_id": str(row[0]), "user_name": row[1], "nick_name": row[2],
            "dept_id": row[3], "dept_name": row[4] or "", "phonenumber": row[5] or "",
            "email": row[6] or ""}


def user_by_name(user_name: str) -> dict | None:
    with _eng().connect() as conn:
        row = conn.execute(text("""
            select u.id, u.username, coalesce(u.nickname, u.username),
                   coalesce(u.org_dept_id, u.dept_id), d.name, u.mobile, u.email
            from system_users u
            left join system_dept d on d.id = coalesce(u.org_dept_id, u.dept_id)
            where u.username = :n and u.deleted = 0 and u.status = 0
        """), {"n": user_name}).first()
    if row is None:
        return None
    return {"user_id": str(row[0]), "user_name": row[1], "nick_name": row[2],
            "dept_id": row[3], "dept_name": row[4] or "", "phonenumber": row[5] or "",
            "email": row[6] or ""}


def roles_of(uid) -> list[dict]:
    """用户角色（含 data_scope / data_scope_dept_ids 原文）。"""
    key = f"roles:{uid}"
    hit = _cached_get(key)
    if hit is not False:
        return hit or []
    out: list[dict] = []
    try:
        with _eng().connect() as conn:
            for r in conn.execute(text("""
                select r.id, r.name, r.code, r.sort, r.data_scope, r.data_scope_dept_ids
                from system_user_role ur
                join system_role r on r.id = ur.role_id
                where ur.user_id = :i and ur.deleted = 0
                  and r.deleted = 0 and r.status = 0
                order by r.sort, r.id
            """), {"i": _to_int(uid) or -1}):
                out.append({"role_id": r[0], "role_name": r[1], "role_key": r[2],
                            "role_sort": r[3], "data_scope": str(r[4] or "1"),
                            "data_scope_dept_ids": r[5] or ""})
    except Exception:
        logger.warning("OA 角色查询失败 uid=%s（降级无角色）", uid, exc_info=True)
    _cached_put(key, out)
    return out


def role_ids_of(uid) -> list[int]:
    return [r["role_id"] for r in roles_of(uid)]


# ---------------- 权限串 ----------------

def is_admin(uid) -> bool:
    return any(r["role_key"] == ADMIN_ROLE_CODE for r in roles_of(uid))


def perms_of(uid) -> set[str]:
    if is_admin(uid):
        return {ALL_PERMISSION}
    key = f"perms:{uid}"
    hit = _cached_get(key)
    if hit is not False:
        return set(hit or [])
    out: set[str] = set()
    rids = role_ids_of(uid)
    if rids:
        try:
            with _eng().connect() as conn:
                for r in conn.execute(text("""
                    select distinct m.permission
                    from system_role_menu rm
                    join system_menu m on m.id = rm.menu_id
                    where rm.role_id = any(:rids) and rm.deleted = 0
                      and m.deleted = 0 and m.status = 0
                      and coalesce(m.permission, '') <> ''
                """), {"rids": rids}):
                    out.add(r[0])
        except Exception:
            logger.warning("OA 权限串查询失败 uid=%s（降级无权限）", uid, exc_info=True)
    _cached_put(key, sorted(out))
    return out


def _match(pattern: str, perm: str) -> bool:
    p, t = pattern.split(":"), perm.split(":")
    if len(p) != len(t):
        return False
    return all(pp == "*" or pp == tt for pp, tt in zip(p, t))


def has_perm(uid, perm: str) -> bool:
    return any(_match(p, perm) for p in perms_of(uid))


def require_perm(uid, perm: str) -> None:
    """REST/卡处理器守卫：无权限 → 403 包封（AppError forbidden）。"""
    if not has_perm(uid, perm):
        raise errors.forbidden(f"没有操作权限：{perm}")


# ---------------- 数据范围 ----------------

def _dept_subtree_ids(dept_id) -> set[int]:
    root = _to_int(dept_id)
    if root is None:
        return set()
    with _eng().connect() as conn:
        rows = conn.execute(text("""
            with recursive sub(id) as (
                select :root
                union all
                select d.id from system_dept d join sub s on d.parent_id = s.id
                where d.deleted = 0 and d.status = 0
            )
            select id from sub
        """), {"root": root}).all()
    return {r[0] for r in rows}


def _parse_dept_ids(raw: str | None) -> set[int]:
    """data_scope_dept_ids 为 '[1,2]' 形态 JSON 字符串（芋道自定义数据范围）。"""
    if not raw:
        return set()
    try:
        return {int(x) for x in json.loads(raw)}
    except Exception:
        return set()


def resolve_scope(uid) -> dict:
    """用户数据范围（多角色取最宽）：
    {"scope": "1".."5", "dept_ids": set[int]|None}；scope=1 时 dept_ids=None 表示不限。"""
    roles = roles_of(uid)
    if not roles:
        return {"scope": "5", "dept_ids": set()}
    scope = min(r["data_scope"] for r in roles)  # 数字越小越宽
    if scope == "1":
        return {"scope": "1", "dept_ids": None}
    me = user_by_id(uid) or {}
    dept_id = me.get("dept_id")
    if scope == "2":
        ids: set[int] = set()
        for r in roles:
            if r["data_scope"] == "2":
                ids |= _parse_dept_ids(r["data_scope_dept_ids"])
        return {"scope": "2", "dept_ids": ids}
    if scope == "3":
        return {"scope": "3", "dept_ids": {dept_id} if dept_id else set()}
    if scope == "4":
        return {"scope": "4", "dept_ids": _dept_subtree_ids(dept_id) if dept_id else set()}
    return {"scope": "5", "dept_ids": set()}


def user_names(uids) -> dict[str, str]:
    out: dict[str, str] = {}
    ints = [i for i in (_to_int(x) for x in uids) if i is not None]
    if not ints:
        return out
    with _eng().connect() as conn:
        for r in conn.execute(text("""
            select id, coalesce(nickname, username) from system_users
            where deleted = 0 and id = any(:ids)
        """), {"ids": ints}):
            out[str(r[0])] = r[1]
    return out


# ---------------- 审批人 / 目录 ----------------

def resolve_approver(role_key: str, exclude_uid=None) -> dict | None:
    """按角色 code 解析审批人（别名自动映射）：该角色下状态正常的第一个用户（id 升序）。"""
    code = APPROVER_ROLE_ALIASES.get(role_key, role_key)
    excl = _to_int(exclude_uid)
    cond = "and u.id <> :excl" if excl is not None else ""
    with _eng().connect() as conn:
        row = conn.execute(text(f"""
            select u.id, u.username, coalesce(u.nickname, u.username)
            from system_users u
            join system_user_role ur on ur.user_id = u.id and ur.deleted = 0
            join system_role r on r.id = ur.role_id
            where r.code = :code and u.deleted = 0 and u.status = 0
              and r.deleted = 0 and r.status = 0
              {cond}
            order by u.id limit 1
        """), {"code": code, **({"excl": excl} if excl is not None else {})}).first()
    if row is None:
        return None
    return {"user_id": str(row[0]), "user_name": row[1], "nick_name": row[2]}


def directory(uid, q: str = "", limit: int = 200) -> list[dict]:
    """OA 选人目录（按数据范围过滤）：[{user_id,user_name,nick_name,dept_id,dept_name}]。"""
    sc = resolve_scope(uid)
    qq = (q or "").strip()
    conds = ["u.deleted = 0", "u.status = 0"]
    params: dict = {"lim": limit}
    if qq:
        conds.append("(:q = '' or strpos(coalesce(u.nickname, u.username), :q) > 0"
                     " or strpos(u.username, :q) > 0 or strpos(coalesce(d.name, ''), :q) > 0)")
        params["q"] = qq
    if sc["scope"] == "5":
        conds.append("u.id = :self")
        params["self"] = _to_int(uid) or -1
    elif sc["scope"] in ("2", "3", "4") and sc["dept_ids"] is not None:
        ids = sorted(sc["dept_ids"]) or [-1]
        conds.append("coalesce(u.org_dept_id, u.dept_id) = any(:dids)")
        params["dids"] = ids
    sql = text(f"""
        select u.id, u.username, coalesce(u.nickname, u.username),
               coalesce(u.org_dept_id, u.dept_id), d.name
        from system_users u
        left join system_dept d on d.id = coalesce(u.org_dept_id, u.dept_id)
        where {' and '.join(conds)}
        order by u.id limit :lim
    """)
    with _eng().connect() as conn:
        rows = conn.execute(sql, params).all()
    return [{"user_id": str(r[0]), "user_name": r[1], "nick_name": r[2],
             "dept_id": r[3], "dept_name": r[4] or ""} for r in rows]


# ---------------- 菜单 / me 视图 ----------------

def _oa_menu_rows(rids: list[int], admin: bool) -> list[dict]:
    """用户可见的 OA 菜单（限定 OA中心 子树；admin 全量，否则角色授权集 + 祖先链）。"""
    with _eng().connect() as conn:
        rows = conn.execute(text("""
            with recursive oa(root_id) as (
                select id from system_menu
                where deleted = 0 and parent_id = 0 and path = 'oa' limit 1
            ),
            sub(id) as (
                select root_id from oa
                union all
                select m.id from system_menu m join sub s on m.parent_id = s.id
                where m.deleted = 0 and m.status = 0
            )
            select m.id, m.name, m.permission, m.type, m.sort, m.parent_id,
                   m.path, m.component, m.visible
            from system_menu m
            where m.id in (select id from sub)
        """)).all()
        allowed: set[int] | None = None
        if not admin and rids:
            allowed = {
                r[0] for r in conn.execute(text("""
                    select distinct rm.menu_id from system_role_menu rm
                    where rm.role_id = any(:rids) and rm.deleted = 0
                """), {"rids": rids})
            }
    out = [{"menu_id": r[0], "menu_name": r[1], "perms": r[2] or "", "menu_type": str(r[3]),
            "order_num": r[4], "parent_id": r[5], "path": r[6] or "", "component": r[7],
            "visible": bool(r[8])} for r in rows]
    if allowed is None:
        return out
    keep = set(allowed)
    for _ in range(3):  # 补齐祖先链（目录→菜单→按钮 深度有限）
        for m in out:
            if m["menu_id"] in keep and m["parent_id"]:
                keep.add(m["parent_id"])
    return [m for m in out if m["menu_id"] in keep]


def menus_of(uid) -> list[dict]:
    rows = sorted(_oa_menu_rows(role_ids_of(uid), is_admin(uid)),
                  key=lambda m: (m["parent_id"], m["order_num"], m["menu_id"]))

    def build(parent: int) -> list[dict]:
        return [{**m, "children": build(m["menu_id"])} for m in rows if m["parent_id"] == parent]

    return build(0)


def me_view(uid) -> dict:
    me = user_by_id(uid)
    if me is None:
        # 平台用户不在业务库（如未同步账号）：仅登录身份，无 OA 角色/权限（只读降级）
        return {"user": None, "is_admin": False, "roles": [], "perms": [],
                "menus": [], "data_scope": DATA_SCOPE_ZH["5"]}
    roles = roles_of(uid)
    sc = resolve_scope(uid)
    return {
        "user": {"user_id": me["user_id"], "user_name": me["user_name"],
                 "nick_name": me["nick_name"], "dept_id": me["dept_id"],
                 "dept_name": me["dept_name"], "phonenumber": me["phonenumber"],
                 "email": me["email"]},
        "is_admin": is_admin(uid),
        "roles": [{"role_id": r["role_id"], "role_name": r["role_name"], "role_key": r["role_key"]}
                  for r in roles],
        "perms": sorted(perms_of(uid)),
        "menus": menus_of(uid),
        "data_scope": DATA_SCOPE_ZH.get(sc["scope"], sc["scope"]),
    }
