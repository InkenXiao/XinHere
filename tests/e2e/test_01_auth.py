"""矩阵 1：认证。

- hq01/inv01 登录得 token 且 UserInfo 合契约；
- 错误口令 401 code=UNAUTHORIZED；
- 无 token GET /todos → 401 UNAUTHORIZED。
"""
from __future__ import annotations

import httpx

from .conftest import API, BASE_URL, PASSWORD, Client, login

USERINFO_KEYS = {"user_id", "username", "display_name", "role", "company"}


def test_login_hq01_token_and_userinfo():
    body = login("hq01")
    assert body["token"], "token 非空"
    user = body["user"]
    assert set(user.keys()) == USERINFO_KEYS, f"UserInfo 键偏差: {sorted(user.keys())}"
    assert user["username"] == "hq01"
    assert user["role"] == "hq_finance"
    assert user["company"] is None
    assert user["display_name"]


def test_login_inv01_token_and_userinfo():
    body = login("inv01")
    assert body["token"]
    user = body["user"]
    assert set(user.keys()) == USERINFO_KEYS
    assert user["username"] == "inv01"
    assert user["role"] == "investee_finance"
    assert user["company"] == "信投数科"


def test_login_wrong_password_401():
    r = httpx.post(
        f"{BASE_URL}{API}/auth/login",
        json={"username": "hq01", "password": "wrong-password"},
        timeout=30.0,
    )
    assert r.status_code == 401, f"{r.status_code}: {r.text[:200]}"
    assert r.json()["code"] == "UNAUTHORIZED"


def test_todos_without_token_401():
    c = Client()  # 无 token
    try:
        c.err("GET", "/todos", 401, "UNAUTHORIZED")
    finally:
        c.close()


def test_auth_me_matches_contract(hq, hq_auth):
    me = hq.ok("GET", "/auth/me")
    assert set(me.keys()) == USERINFO_KEYS
    assert me["user_id"] == hq_auth["user"]["user_id"]
    assert me["username"] == "hq01"
