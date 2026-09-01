-- ============================================================================
-- XinHere 数据库初始化脚本（一）：建表
-- 数据库：xinhere（PostgreSQL >= 13）
-- 来源：apps/backend/app/persistence/models.py + alembic/versions/0001_initial.py
-- 用法：psql -U dbuser -d xinhere -f scripts/db/01_create_tables.sql
-- 说明：
--   * 本脚本与 alembic 迁移二选一；若走 alembic upgrade head 则无需执行本脚本
--   * 平台表（platform_*，append-only 无审计字段）：5 张
--   * 业务表（三红线：逻辑删除 is_delete + 审计四件套 created_by/created_at/
--     updated_by/updated_at + updated_at 由触发器维护）：15 张
--   * 共 20 张表
--   * UUID 主键由应用侧生成（uuid4）；gen_random_uuid() 需 PG13+（更低版本
--     需 CREATE EXTENSION pgcrypto）
-- ============================================================================

-- ---------------- 函数：updated_at 触发器 ----------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 平台表（append-only，无 update/delete）
-- ============================================================================

-- 1. 会话头表
CREATE TABLE IF NOT EXISTS platform_sessions (
    session_id     UUID           PRIMARY KEY,
    user_id        VARCHAR(64)    NOT NULL,
    title          VARCHAR(255),
    domain         VARCHAR(64)    NOT NULL,
    plugin_set     JSONB          NOT NULL,
    plugin_set_hash VARCHAR(64)   NOT NULL,
    status         VARCHAR(16)    NOT NULL DEFAULT 'active',
    created_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ    NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_psessions_user ON platform_sessions (user_id, updated_at DESC);

-- 2. 会话事件表（append-only 事件日志，(session_id, seq) 复合主键，seq 会话内 0 起连续）
CREATE TABLE IF NOT EXISTS platform_session_events (
    session_id  UUID          NOT NULL REFERENCES platform_sessions(session_id),
    seq         BIGINT        NOT NULL,
    type        VARCHAR(64)   NOT NULL,
    time        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    data        JSONB         NOT NULL,
    ignorable   BOOLEAN       NOT NULL DEFAULT false,
    turn        INTEGER,
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_pevents_type ON platform_session_events (session_id, type);

-- 3. 补偿任务表（副作用补偿队列）
CREATE TABLE IF NOT EXISTS platform_compensations (
    id          BIGSERIAL     PRIMARY KEY,
    session_id  UUID          NOT NULL,
    event_seq   BIGINT        NOT NULL,
    plugin_name VARCHAR(64)   NOT NULL,
    action      VARCHAR(128)  NOT NULL,
    payload     JSONB         NOT NULL,
    status      VARCHAR(16)   NOT NULL DEFAULT 'pending',
    retry_count INTEGER       NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- 4. 系统级操作日志（append-only，无 update/delete）
CREATE TABLE IF NOT EXISTS platform_operation_logs (
    id          BIGSERIAL     PRIMARY KEY,
    occurred_at TIMESTAMPTZ   NOT NULL DEFAULT now(),
    user_id     VARCHAR(64)   NOT NULL,
    session_id  UUID,
    channel     VARCHAR(16)   NOT NULL,
    actor       VARCHAR(128)  NOT NULL DEFAULT '',
    plugin_name VARCHAR(64),
    entity      VARCHAR(128)  NOT NULL,
    operation   VARCHAR(8)    NOT NULL,
    record_key  VARCHAR(128),
    detail      JSONB         NOT NULL DEFAULT '{}',
    client_ip   VARCHAR(45),
    entry_point VARCHAR(128),
    request_id  VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_oplogs_entity ON platform_operation_logs (entity, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_oplogs_user   ON platform_operation_logs (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_oplogs_sess   ON platform_operation_logs (session_id, occurred_at DESC);

-- 5. 会话投影状态表（事件派生状态的物化快照）
CREATE TABLE IF NOT EXISTS platform_projections (
    session_id    UUID         NOT NULL,
    key           VARCHAR(64)  NOT NULL,
    state_version INTEGER      NOT NULL DEFAULT 1,
    seq           BIGINT       NOT NULL DEFAULT 0,
    value         JSONB        NOT NULL,
    PRIMARY KEY (session_id, key)
);

-- ============================================================================
-- 业务表（三红线：is_delete 逻辑删除 + 审计四件套 + updated_at 触发器）
-- ============================================================================

-- 6. 用户表
CREATE TABLE IF NOT EXISTS sys_users (
    user_id       VARCHAR(36)  PRIMARY KEY,
    username      VARCHAR(64)  NOT NULL UNIQUE,
    password_hash VARCHAR(128) NOT NULL,
    display_name  VARCHAR(64)  NOT NULL,
    role          VARCHAR(32)  NOT NULL,
    company       VARCHAR(64),
    is_delete     BOOLEAN      NOT NULL DEFAULT false,
    created_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 7. 认证令牌表
CREATE TABLE IF NOT EXISTS sys_auth_tokens (
    token      VARCHAR(128) PRIMARY KEY,
    user_id    VARCHAR(36)  NOT NULL,
    expires_at TIMESTAMPTZ  NOT NULL,
    is_delete  BOOLEAN      NOT NULL DEFAULT false,
    created_by VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 8. 业务任务表（任务派发头表）
CREATE TABLE IF NOT EXISTS biz_tasks (
    task_id       VARCHAR(36)  PRIMARY KEY,
    scene         VARCHAR(32)  NOT NULL,
    title         VARCHAR(255) NOT NULL,
    dispatcher_id VARCHAR(36)  NOT NULL,
    payload       JSONB        NOT NULL,
    period        VARCHAR(32),
    status        VARCHAR(16)  NOT NULL DEFAULT 'open',
    is_delete     BOOLEAN      NOT NULL DEFAULT false,
    created_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 9. 待办表
CREATE TABLE IF NOT EXISTS biz_todos (
    todo_id       VARCHAR(36)  PRIMARY KEY,
    task_id       VARCHAR(36)  NOT NULL,
    assignee_id   VARCHAR(36)  NOT NULL,
    kind          VARCHAR(32)  NOT NULL DEFAULT 'action',
    scene         VARCHAR(32)  NOT NULL DEFAULT 'generic',
    title         VARCHAR(255) NOT NULL,
    sub           VARCHAR(255) NOT NULL DEFAULT '',
    status        VARCHAR(32)  NOT NULL DEFAULT 'pending',
    lamp          VARCHAR(1),
    ref           JSONB        NOT NULL,
    feedback_text TEXT,
    na_reason     TEXT,
    na_comment    TEXT,
    due           TIMESTAMPTZ,
    is_delete     BOOLEAN      NOT NULL DEFAULT false,
    created_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_todos_assignee ON biz_todos (assignee_id, status);

-- 10. 风险填报批次表
CREATE TABLE IF NOT EXISTS risk_fill_batches (
    batch_id      VARCHAR(36)  PRIMARY KEY,
    period        VARCHAR(32)  NOT NULL,
    dispatcher_id VARCHAR(36)  NOT NULL,
    status        VARCHAR(16)  NOT NULL DEFAULT 'collecting',
    is_delete     BOOLEAN      NOT NULL DEFAULT false,
    created_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 11. 风险填报表单表（每公司一份）
CREATE TABLE IF NOT EXISTS risk_fill_reports (
    report_id  VARCHAR(36)  PRIMARY KEY,
    batch_id   VARCHAR(36)  NOT NULL,
    company    VARCHAR(64)  NOT NULL,
    status     VARCHAR(16)  NOT NULL DEFAULT 'unfilled',
    lamp_r     INTEGER      NOT NULL DEFAULT 0,
    lamp_y     INTEGER      NOT NULL DEFAULT 0,
    lamp_g     INTEGER      NOT NULL DEFAULT 0,
    is_delete  BOOLEAN      NOT NULL DEFAULT false,
    created_by VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_reports_batch ON risk_fill_reports (batch_id, company);

-- 12. 风险填报明细项表（每报告 16 项）
CREATE TABLE IF NOT EXISTS risk_fill_items (
    id         BIGSERIAL    PRIMARY KEY,
    report_id  VARCHAR(36)  NOT NULL,
    idx        INTEGER      NOT NULL,
    name       VARCHAR(128) NOT NULL,
    lamp       VARCHAR(1)   NOT NULL DEFAULT 'g',
    fields     JSONB        NOT NULL,
    is_delete  BOOLEAN      NOT NULL DEFAULT false,
    created_by VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_items_report ON risk_fill_items (report_id, idx);

-- 13. 资金保障填报表
CREATE TABLE IF NOT EXISTS cash_guarantee_reports (
    form_id        VARCHAR(36)  PRIMARY KEY,
    company        VARCHAR(64)  NOT NULL,
    period         VARCHAR(32)  NOT NULL,
    avail_cash     DOUBLE PRECISION NOT NULL DEFAULT 0,
    pooled_fund    DOUBLE PRECISION NOT NULL DEFAULT 0,
    avail_credit   DOUBLE PRECISION NOT NULL DEFAULT 0,
    monthly_outflow DOUBLE PRECISION NOT NULL DEFAULT 0,
    ratio          DOUBLE PRECISION NOT NULL DEFAULT 0,
    lamp           VARCHAR(1)   NOT NULL DEFAULT 'g',
    status         VARCHAR(16)  NOT NULL DEFAULT 'draft',
    session_id     UUID,
    is_delete      BOOLEAN      NOT NULL DEFAULT false,
    created_by     VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by     VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 14. KPI 考核批次表
CREATE TABLE IF NOT EXISTS kpi_batches (
    batch_id      VARCHAR(36)  PRIMARY KEY,
    period        VARCHAR(32)  NOT NULL,
    dispatcher_id VARCHAR(36)  NOT NULL,
    status        VARCHAR(16)  NOT NULL DEFAULT 'collecting',
    is_delete     BOOLEAN      NOT NULL DEFAULT false,
    created_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 15. KPI 指标表
CREATE TABLE IF NOT EXISTS kpi_indicators (
    indicator_id VARCHAR(36)  PRIMARY KEY,
    batch_id     VARCHAR(36)  NOT NULL,
    company      VARCHAR(64)  NOT NULL,
    dim          VARCHAR(32)  NOT NULL DEFAULT '',
    name         VARCHAR(128) NOT NULL,
    kpi_type     VARCHAR(32)  NOT NULL DEFAULT '定量',
    content      TEXT         NOT NULL DEFAULT '',
    base_score   VARCHAR(16)  NOT NULL DEFAULT '',
    max_score    VARCHAR(16)  NOT NULL DEFAULT '',
    status       VARCHAR(16)  NOT NULL DEFAULT 'unfilled',
    is_delete    BOOLEAN      NOT NULL DEFAULT false,
    created_by   VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by   VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kpi_ind_batch ON kpi_indicators (batch_id, company);

-- 16. KPI 里程碑表
CREATE TABLE IF NOT EXISTS kpi_milestones (
    milestone_id VARCHAR(36)  PRIMARY KEY,
    indicator_id VARCHAR(36)  NOT NULL,
    content      TEXT         NOT NULL DEFAULT '',
    plan_date    VARCHAR(32)  NOT NULL DEFAULT '',
    material     VARCHAR(255) NOT NULL DEFAULT '',
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending',
    is_delete    BOOLEAN      NOT NULL DEFAULT false,
    created_by   VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by   VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kpi_ms_indicator ON kpi_milestones (indicator_id);

-- 17. KPI 里程碑反馈表
CREATE TABLE IF NOT EXISTS kpi_ms_feedbacks (
    feedback_id   VARCHAR(36)  PRIMARY KEY,
    milestone_id  VARCHAR(36)  NOT NULL,
    batch_id      VARCHAR(36)  NOT NULL DEFAULT '',
    company       VARCHAR(64)  NOT NULL,
    status        VARCHAR(32)  NOT NULL DEFAULT '进行中',
    progress      INTEGER      NOT NULL DEFAULT 0,
    actual_date   VARCHAR(32),
    lamp          VARCHAR(1)   NOT NULL DEFAULT 'g',
    status_note   TEXT,
    review_status VARCHAR(16)  NOT NULL DEFAULT 'draft',
    is_delete     BOOLEAN      NOT NULL DEFAULT false,
    created_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by    VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 18. KPI 灯调整记录表
CREATE TABLE IF NOT EXISTS kpi_lamp_adjustments (
    id             BIGSERIAL    PRIMARY KEY,
    company        VARCHAR(64)  NOT NULL,
    indicator_name VARCHAR(128) NOT NULL,
    old_lamp       VARCHAR(1)   NOT NULL,
    new_lamp       VARCHAR(1)   NOT NULL,
    reason         TEXT         NOT NULL DEFAULT '',
    operator       VARCHAR(64)  NOT NULL DEFAULT '',
    is_delete      BOOLEAN      NOT NULL DEFAULT false,
    created_by     VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by     VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 19. 投后报告表
CREATE TABLE IF NOT EXISTS pit_reports (
    report_id   VARCHAR(36)  PRIMARY KEY,
    company_ids JSONB        NOT NULL,
    period      VARCHAR(32)  NOT NULL,
    outline     JSONB        NOT NULL,
    content     TEXT         NOT NULL DEFAULT '',
    status      VARCHAR(16)  NOT NULL DEFAULT 'outlining',
    is_delete   BOOLEAN      NOT NULL DEFAULT false,
    created_by  VARCHAR(64)  NOT NULL DEFAULT 'system',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by  VARCHAR(64)  NOT NULL DEFAULT 'system',
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 20. 知识库节点表（树形）
CREATE TABLE IF NOT EXISTS kb_sources (
    kb_id    VARCHAR(36)  PRIMARY KEY,
    name     VARCHAR(128) NOT NULL,
    parent_id VARCHAR(36),
    kb_type  VARCHAR(16)  NOT NULL DEFAULT 'internal',
    mcp_ref  VARCHAR(128),
    is_delete BOOLEAN     NOT NULL DEFAULT false,
    created_by VARCHAR(64) NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by VARCHAR(64) NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 触发器：业务表 14 张 + platform_sessions（会话列表按 updated_at 排序）
-- ============================================================================
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'sys_users', 'sys_auth_tokens', 'biz_tasks', 'biz_todos',
        'risk_fill_batches', 'risk_fill_reports', 'risk_fill_items',
        'cash_guarantee_reports', 'kpi_batches', 'kpi_indicators',
        'kpi_milestones', 'kpi_ms_feedbacks', 'kpi_lamp_adjustments',
        'pit_reports', 'kb_sources', 'platform_sessions'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_updated_at ON %I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
    END LOOP;
END $$;
