from __future__ import annotations

import bcrypt
from sqlalchemy import select

from .core.context import AuditCtx, set_ctx
from .persistence.models import KbSource, SysUser
from .persistence.session import SessionLocal
from .services.common import COMPANIES, INV_COMPANY

PASSWORD = "Xin@2026"
ADMIN_PASSWORD = "Xin@here#1234"

KB_TREE = [
    ("kb-enterprise", "企业知识库", None, "internal"),
    ("kb-department", "部门知识库", None, "internal"),
    ("kb-project", "项目知识库", None, "internal"),
    ("kb-project-xgf", "信投股份", "kb-project", "internal"),
    ("kb-project-xxjs", "信息建设", "kb-project", "internal"),
    ("kb-external", "外部知识库", None, "external"),
    ("kb-personal", "个人知识库", None, "internal"),
]


def main() -> None:
    set_ctx(AuditCtx(user_id="system", channel="system", actor="seed", entry_point="python -m app.seed"))
    password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    with SessionLocal() as db:
        # 用户：hq01 + inv01..inv11
        created_users = 0
        hq = db.scalars(select(SysUser).where(SysUser.username == "hq01")).first()
        if hq is None:
            db.add(SysUser(username="hq01", password_hash=password_hash,
                           display_name="李工", role="hq_finance", company=None))
            created_users += 1
        # 管理员：hq_finance 角色（本部全量权限），独立口令
        if db.scalars(select(SysUser).where(SysUser.username == "admin")).first() is None:
            db.add(SysUser(
                username="admin",
                password_hash=bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode(),
                display_name="系统管理员", role="hq_finance", company=None,
            ))
            created_users += 1
        for i in range(1, 12):
            username = f"inv{i:02d}"
            if db.scalars(select(SysUser).where(SysUser.username == username)).first() is None:
                db.add(SysUser(username=username, password_hash=password_hash,
                               display_name=f"{INV_COMPANY[username]}财务",
                               role="investee_finance", company=INV_COMPANY[username]))
                created_users += 1
        # 知识库树
        created_kb = 0
        for kb_id, name, parent_id, kb_type in KB_TREE:
            if db.get(KbSource, kb_id) is None:
                db.add(KbSource(kb_id=kb_id, name=name, parent_id=parent_id, kb_type=kb_type))
                created_kb += 1
        db.commit()
    print(f"seed 完成：新增用户 {created_users}，新增知识库节点 {created_kb}；公司 {len(COMPANIES)} 家")


if __name__ == "__main__":
    main()
