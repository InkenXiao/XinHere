"""批次2 测试：SSO 登录链路 + XuanPu 代理双轨鉴权 + Xin台入口/免登

函数级直调（不启 TestClient）：处理器均为普通函数，fake Request / SimpleNamespace 驱动。
X1 sso_login 302 / X2 JIT 建号 / X3 已有账号绑定 / X4 state 防重放 /
X5 双轨鉴权头互斥 + resolve_token 过期判定 / X6 REST 代理参数透传 /
X7 cockpit seed 幂等 + CRUD 权限与校验 / X8 launch 两分支 /
X9 sso/ticket 免登三分支（400 票据失效回落登录页 / ok=False / 上游 5xx）。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlparse

import pytest
from sqlalchemy import select

from app.core import errors
from app.core.config import settings
from app.persistence.models import CockpitEntry, SysAuthToken, SysUser
from app.platform.api import auth as auth_api
from app.platform.api import cockpit as cockpit_api
from app.platform.api import deps as deps_api
from app.platform.api import xuanpu as xuanpu_api
from app.services import xuanpu as xuanpu_svc


# ---------- 测试辅助 ----------


class _FakeRequest:
    """auth 处理器所需的最小 Request 替身。"""

    state = SimpleNamespace(request_id="pytest-rid")
    client = SimpleNamespace(host="127.0.0.1")


def _sso_payload(user_code: str, is_admin: bool = False, token: str = "xp-token") -> dict:
    """sso-server /sso/token 成功响应的替身。"""
    return {
        "access_token": token,
        "expires_in": 3600,
        "user": {
            "user_id": str(uuid.uuid4()),
            "user_code": user_code,
            "user_name": user_code + "名",
            "is_admin": is_admin,
        },
    }


def _soft_cleanup(db, user: SysUser) -> None:
    """业务表红线1：测试数据软删（is_delete=True）收尾。"""
    user.is_delete = True
    for row in db.scalars(select(SysAuthToken).where(SysAuthToken.user_id == user.user_id)).all():
        row.is_delete = True
    db.commit()


def _register_state() -> str:
    state = uuid.uuid4().hex
    auth_api._sso_states[state] = time.time() + 60
    return state


# ---------- X1: sso_login 302 ----------


def test_x1_sso_login_redirect(db):
    before = set(auth_api._sso_states)
    resp = auth_api.sso_login()
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith(settings.sso_auth_url)
    q = parse_qs(urlparse(loc).query)
    assert q["client_id"] == [settings.sso_client_id]
    assert q["redirect_uri"] == [settings.sso_callback_url]
    state = q["state"][0]
    # state 已登记且一次性（弹出即失效）
    assert state in auth_api._sso_states
    assert set(auth_api._sso_states) - before == {state}
    auth_api._sso_states.pop(state, None)


# ---------- X2: 回调 JIT 建号 ----------


def test_x2_sso_callback_jit_creates_user(db, monkeypatch):
    user_code = f"xptest_{uuid.uuid4().hex[:8]}"
    payload = _sso_payload(user_code, is_admin=False, token="xp-tok-2")
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen["form"] = kw.get("data") or {}
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(auth_api.httpx, "post", fake_post)
    state = _register_state()

    resp = auth_api.sso_callback(_FakeRequest(), code="c1", state=state, db=db)
    db.commit()

    assert resp.status_code == 302
    assert resp.headers["location"].startswith(f"{settings.frontend_url}/?sso_token=")
    assert seen["url"] == settings.sso_token_url
    assert seen["form"]["code"] == "c1"
    assert state not in auth_api._sso_states  # 一次性消费

    user = db.scalars(select(SysUser).where(SysUser.username == user_code)).first()
    assert user is not None
    assert user.auth_source == "sso"
    assert user.role == "investee_finance"  # 非管理员 → 被投企业财务
    assert user.xuanpu_user_id == payload["user"]["user_id"]
    assert not user.password_hash  # SSO 账号无本地密码
    tokens = db.scalars(select(SysAuthToken).where(SysAuthToken.user_id == user.user_id)).all()
    assert any(t.xuanpu_token == "xp-tok-2" for t in tokens)
    _soft_cleanup(db, user)


# ---------- X3: 回调绑定已有本地账号 ----------


def test_x3_sso_callback_binds_existing_user(db, monkeypatch):
    user_code = f"xplocal_{uuid.uuid4().hex[:8]}"
    user = SysUser(
        username=user_code, password_hash="legacy-hash",
        display_name="旧名", role="hq_finance", company="",
    )
    db.add(user)
    db.commit()

    payload = _sso_payload(user_code, is_admin=True, token="xp-tok-3")
    monkeypatch.setattr(
        auth_api.httpx, "post",
        lambda url, **kw: SimpleNamespace(status_code=200, json=lambda: payload),
    )
    state = _register_state()
    resp = auth_api.sso_callback(_FakeRequest(), code="c2", state=state, db=db)
    db.commit()

    assert resp.status_code == 302
    db.expire_all()
    user = db.scalars(select(SysUser).where(SysUser.username == user_code)).first()
    assert user.auth_source == "sso"
    assert user.xuanpu_user_id == payload["user"]["user_id"]
    assert user.display_name == payload["user"]["user_name"]  # 姓名同步
    assert user.role == "hq_finance"  # 本地角色不被覆盖
    _soft_cleanup(db, user)


# ---------- X4: state 无效 → 400 且不触达 sso-server ----------


def test_x4_sso_callback_invalid_state(db, monkeypatch):
    called = {"n": 0}

    def fake_post(url, **kw):
        called["n"] += 1
        return SimpleNamespace(status_code=200, json=lambda: {})

    monkeypatch.setattr(auth_api.httpx, "post", fake_post)
    with pytest.raises(errors.AppError) as ei:
        auth_api.sso_callback(_FakeRequest(), code="c3", state="forged", db=db)
    assert ei.value.status == 400
    assert called["n"] == 0


# ---------- X5: 双轨鉴权头互斥 + resolve_token 过期判定 ----------


def test_x5a_dual_auth_headers_mutually_exclusive(db):
    # 无 token：回落 X-User-Name（URL 编码），无 Authorization
    c1 = xuanpu_svc._client("张三", xuanpu_token=None)
    assert c1._extra_headers.get("X-User-Name") == quote("张三", safe="")
    assert "Authorization" not in c1._extra_headers
    # 有 token：Bearer，不再带 X-User-Name
    c2 = xuanpu_svc._client("张三", xuanpu_token="tok")
    assert c2._extra_headers.get("Authorization") == "Bearer tok"
    assert "X-User-Name" not in c2._extra_headers


def test_x5b_resolve_token_prefers_unexpired(db):
    suffix = uuid.uuid4().hex[:8]
    user = SysUser(
        username=f"xpres_{suffix}", password_hash="x",
        display_name="S", role="investee_finance", company="",
    )
    db.add(user)
    db.commit()
    now = datetime.now(timezone.utc)
    db.add_all([
        SysAuthToken(
            token=f"t-dead-{suffix}", user_id=user.user_id,
            expires_at=now + timedelta(hours=1),
            xuanpu_token="tok-dead", xuanpu_token_exp=now - timedelta(hours=1),
        ),
        SysAuthToken(
            token=f"t-fresh-{suffix}", user_id=user.user_id,
            expires_at=now + timedelta(hours=1),
            xuanpu_token="tok-fresh", xuanpu_token_exp=now + timedelta(hours=1),
        ),
    ])
    db.commit()
    assert xuanpu_svc.resolve_token(db, user.user_id) == "tok-fresh"
    _soft_cleanup(db, user)


# ---------- X6: REST 代理参数透传（mock services 层） ----------


def test_x6_proxy_pass_through(db, monkeypatch):
    fake_user = SimpleNamespace(
        user_id=f"u-{uuid.uuid4().hex[:8]}",
        display_name="张三", username="zhang", xuanpu_user_id=None,
    )
    monkeypatch.setattr(xuanpu_svc, "resolve_token", lambda d, uid: None)
    calls: dict[str, tuple] = {}

    def rec(name):
        def _f(*a, **kw):
            calls[name] = (a, kw)
            return {"ok": name}
        return _f

    monkeypatch.setattr(xuanpu_svc, "skills", lambda *a, **kw: [{"id": 1, "name": "s1"}])
    monkeypatch.setattr(xuanpu_svc, "skill_run", rec("skill_run"))
    monkeypatch.setattr(xuanpu_svc, "fill_template_detail", rec("template"))
    monkeypatch.setattr(
        xuanpu_svc, "fill_my_assignments",
        lambda *a, **kw: [{"assignment_id": 7, "title": "T", "status": "pending"}],
    )
    monkeypatch.setattr(xuanpu_svc, "fill_draft", rec("draft"))
    monkeypatch.setattr(xuanpu_svc, "fill_submit", rec("submit"))

    # skills 列表
    assert xuanpu_api.skills(user=fake_user, db=db) == {"items": [{"id": 1, "name": "s1"}]}
    # skill_run：inputs dict → JSON 字符串透传
    xuanpu_api.skill_run(9, xuanpu_api.SkillRunIn(inputs={"q": "营收"}), user=fake_user, db=db)
    a, kw = calls["skill_run"]
    assert a[0] == "9"
    assert json.loads(a[1]) == {"q": "营收"}
    assert kw["xuanpu_token"] is None  # 无 token 回落 None → X-User-Name
    # fill/template
    xuanpu_api.fill_template(3, user=fake_user, db=db)
    assert calls["template"][0][0] == 3
    # fill/assignment：命中返回单条；未命中 404
    hit = xuanpu_api.fill_assignment(7, user=fake_user, db=db)
    assert hit["assignment_id"] == 7
    with pytest.raises(errors.AppError) as ei:
        xuanpu_api.fill_assignment(8, user=fake_user, db=db)
    assert ei.value.status == 404
    # fill/draft、fill/submit：data dict → JSON 字符串透传
    xuanpu_api.fill_draft(xuanpu_api.FillDataIn(assignment_id=7, data={"a": 1}), user=fake_user, db=db)
    a, _ = calls["draft"]
    assert a[0] == 7 and json.loads(a[1]) == {"a": 1}
    xuanpu_api.fill_submit(xuanpu_api.FillDataIn(assignment_id=7, data={"a": 2}), user=fake_user, db=db)
    a, _ = calls["submit"]
    assert a[0] == 7 and json.loads(a[1]) == {"a": 2}


# ---------- X7: cockpit seed 幂等 + CRUD 权限/校验 ----------


def test_x7_cockpit_seed_and_crud(db):
    n1 = cockpit_api.seed_entries(db)
    db.commit()
    n2 = cockpit_api.seed_entries(db)
    db.commit()
    assert n2 == 0  # 幂等：第二次不再新增
    keys = {r.key for r in db.scalars(select(CockpitEntry)).all()}
    assert {"pro", "rag", "mcp"} <= keys
    if n1:
        assert n1 <= 3

    # require_hq：非本部财务 → 403
    with pytest.raises(errors.AppError) as ei:
        deps_api.require_hq(SimpleNamespace(role="investee_finance"))
    assert ei.value.status == 403
    hq = SimpleNamespace(role="hq_finance")
    assert deps_api.require_hq(hq) is hq

    # entry_path 必须以 / 开头
    with pytest.raises(errors.AppError) as ei:
        cockpit_api.create_entry(
            cockpit_api.EntryIn(key=f"bad_{uuid.uuid4().hex[:6]}", name="X", entry_path="pro/"),
            user=hq, db=db,
        )
    assert ei.value.status == 400

    # 正常 CRUD（配置表硬删）
    key = f"pytest_{uuid.uuid4().hex[:6]}"
    e = cockpit_api.create_entry(
        cockpit_api.EntryIn(key=key, name="测试入口", entry_path="/pro/", sort=99),
        user=hq, db=db,
    )
    db.commit()
    assert e["key"] == key
    e2 = cockpit_api.update_entry(
        e["id"], cockpit_api.EntryPatch(name="改名", entry_path="/rag/"), user=hq, db=db,
    )
    assert e2["name"] == "改名" and e2["entry_path"] == "/rag/"
    # key 重复 → 400
    with pytest.raises(errors.AppError) as ei:
        cockpit_api.create_entry(
            cockpit_api.EntryIn(key=key, name="重复", entry_path="/mcp/"), user=hq, db=db,
        )
    assert ei.value.status == 400
    cockpit_api.delete_entry(e["id"], user=hq, db=db)
    db.commit()
    assert db.get(CockpitEntry, e["id"]) is None


# ---------- X8: launch 两分支 ----------


def test_x8_launch_two_branches(db, monkeypatch):
    cockpit_api.seed_entries(db)
    db.commit()

    # 分支1：未绑定统一身份 → 外网直连 + 首登提示
    anon = SimpleNamespace(user_id="u", display_name="张", username="zhang", xuanpu_user_id=None)
    r1 = xuanpu_api.launch(xuanpu_api.LaunchIn(key="pro"), user=anon, db=db)
    assert r1["first_login"] is True
    assert r1["url"] == f"{settings.xuanpu_public_url}/pro/"

    # 分支2：已绑定 → 换一次性 ticket 拼 /sso/launch 免登 URL
    bound = SimpleNamespace(user_id="u", display_name="张", username="zhang", xuanpu_user_id="42")
    monkeypatch.setattr(
        xuanpu_api.httpx, "post",
        lambda url, **kw: SimpleNamespace(status_code=200, json=lambda: {"ticket": "TCK123"}),
    )
    r2 = xuanpu_api.launch(xuanpu_api.LaunchIn(key="pro"), user=bound, db=db)
    assert r2["first_login"] is False
    assert r2["url"].startswith(f"{settings.xuanpu_public_url}/sso/launch?ticket=TCK123&to=")
    assert r2["url"].endswith("/pro/")

    # 入口不存在 → 404
    with pytest.raises(errors.AppError) as ei:
        xuanpu_api.launch(xuanpu_api.LaunchIn(key="nope"), user=bound, db=db)
    assert ei.value.status == 404


# ---------- X9: sso/ticket 免登三分支 ----------


def test_x9_sso_ticket_branches(db, monkeypatch):
    # 分支1：一次性票据无效/过期/重复消费（sso-server 400）→ 静默回落前端登录页，不抛错
    monkeypatch.setattr(
        auth_api.httpx, "post",
        lambda url, **kw: SimpleNamespace(status_code=400, json=lambda: {"detail": "票据无效或已过期"}),
    )
    resp = auth_api.sso_ticket(_FakeRequest(), ticket="dead-ticket", db=db)
    assert resp.status_code == 302
    assert resp.headers["location"] == settings.frontend_url

    # 分支2：校验通过但 ok=False（门户侧拒绝）→ 同样回落登录页
    monkeypatch.setattr(
        auth_api.httpx, "post",
        lambda url, **kw: SimpleNamespace(status_code=200, json=lambda: {"ok": False}),
    )
    resp = auth_api.sso_ticket(_FakeRequest(), ticket="t-rejected", db=db)
    assert resp.status_code == 302
    assert resp.headers["location"] == settings.frontend_url

    # 分支3：sso-server 5xx（真实上游故障）→ 502 UPSTREAM_ERROR 继续暴露
    monkeypatch.setattr(
        auth_api.httpx, "post",
        lambda url, **kw: SimpleNamespace(status_code=500, json=lambda: {}),
    )
    with pytest.raises(errors.AppError) as ei:
        auth_api.sso_ticket(_FakeRequest(), ticket="t-upstream", db=db)
    assert ei.value.status == 502
