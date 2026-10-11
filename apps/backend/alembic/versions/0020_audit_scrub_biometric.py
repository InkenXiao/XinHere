"""审计存量脱敏：清除生物特征向量（PIPL）

Revision ID: 0016_audit_scrub_biometric
Revises: 0015_alias_unique_label
Create Date: 2026-10-10

- scrub 白名单加入 embedding/samples/vec 之前写入的 platform_operation_logs.detail
  可能留存声纹向量（insert 的 new.* / update 的 changes.*），按 PIPL 必须清除
- 按键存在性分支处理：jsonb_set 的 new_value 为 SQL NULL 且 path 不存在时
  会把整列置 NULL（PG 16 实测），故仅对键存在的行做对应减法
- 幂等：清理后行不再含敏感键，重跑为无操作
"""
from alembic import op

revision = "0020_audit_scrub_biometric"
down_revision = "0019_alias_unique_label"
branch_labels = None
depends_on = None

_BIO_KEYS = "'embedding', 'samples', 'vec'"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE platform_operation_logs
        SET detail = CASE
            WHEN detail ? 'new' AND detail ? 'changes' THEN
                jsonb_set(
                    jsonb_set(
                        detail,
                        '{{new}}',
                        (detail -> 'new') - ARRAY[{_BIO_KEYS}]::text[]
                    ),
                    '{{changes}}',
                    (detail -> 'changes') - ARRAY[{_BIO_KEYS}]::text[]
                )
            WHEN detail ? 'new' THEN
                jsonb_set(detail, '{{new}}', (detail -> 'new') - ARRAY[{_BIO_KEYS}]::text[])
            ELSE
                jsonb_set(detail, '{{changes}}', (detail -> 'changes') - ARRAY[{_BIO_KEYS}]::text[])
        END
        WHERE detail ?| ARRAY['new', 'changes']
        """
    )


def downgrade() -> None:
    # 不可逆：被清除的生物特征数据无法恢复（也不应恢复）
    pass
