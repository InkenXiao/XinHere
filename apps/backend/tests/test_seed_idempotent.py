from __future__ import annotations

from sqlalchemy import func, select

from app import seed
from app.persistence.models import KbSource, SysUser

USERNAMES = ["hq01"] + [f"inv{i:02d}" for i in range(1, 12)]


def test_seed_idempotent(db):
    seed.main()
    seed.main()
    users = db.scalar(
        select(func.count()).select_from(SysUser).where(SysUser.username.in_(USERNAMES))
    )
    kbs = db.scalar(select(func.count()).select_from(KbSource))
    assert users == 12
    assert kbs == 7
