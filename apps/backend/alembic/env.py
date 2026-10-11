from __future__ import annotations

import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, pool, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.persistence.base import Base  # noqa: E402
from app.persistence import models  # noqa: E402,F401  注册全部表

config = context.config
# configparser 插值会把 URL 里的 %（URL 编码密码）当语法解析，%% 转义；实际连接用 settings 原值
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

logger = logging.getLogger("alembic.runtime.migration")


def _heal_stale_version(connection) -> None:
    """自愈历史脏指针：alembic_version 指向仓库中不存在的 revision（迁移文件曾本地创建
    并执行过、未入库即被删除）时，upgrade 会直接 Can't locate revision 失败，部署被卡死。

    处理：把指针重置为当前代码 head——该指针对应的 DDL 已实际应用于目标库，表结构以
    库内现状为准（不回滚、不重建）；指针缺失（全新库）或全部可识别时不做任何事。"""
    try:
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    except Exception:  # 全新库尚无 alembic_version 表，交由 upgrade 正常从头建
        connection.rollback()  # 清掉探针失败留下的 aborted 事务，避免污染后续迁移
        return
    connection.rollback()  # 探针为只读，结束隐式事务
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    known = {rev.revision for rev in script.walk_revisions()}
    stale = sorted({v for (v,) in rows if v not in known})
    if not stale or not heads:
        return
    with connection.begin():
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            [{"v": h} for h in heads],
        )
    logger.warning(
        "alembic_version 指向仓库中不存在的迁移 %s（历史遗留脏指针），已重置为当前 head %s；"
        "库内表结构保持现状，如该迁移的结构变更仍需要请补回迁移文件后重跑",
        stale, heads,
    )


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _heal_stale_version(connection)
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
