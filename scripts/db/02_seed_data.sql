-- ============================================================================
-- XinHere 数据库初始化脚本（二）：基础数据
-- 来源：apps/backend/app/seed.py + app/services/common.py
-- 用法：psql -U dbuser -d xinhere -f scripts/db/02_seed_data.sql
-- 说明：
--   * 幂等：ON CONFLICT DO NOTHING，可重复执行
--   * 密码哈希为 bcrypt（cost=12）：
--       普通用户  Xin@2026       → hq01、inv01~inv11
--       管理员    Xin@here#1234  → admin
--   * user_id 用 gen_random_uuid()::text 生成（应用侧为 uuid4 字符串），需 PG13+
-- ============================================================================

-- ---------------- 用户（1 总部 + 1 管理员 + 11 被投企业财务） ----------------
-- 总部财务（全量权限，company 为空）
INSERT INTO sys_users (user_id, username, password_hash, display_name, role, company)
VALUES (gen_random_uuid()::text, 'hq01',
        '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
        '李工', 'hq_finance', NULL)
ON CONFLICT (username) DO NOTHING;

-- 管理员（hq_finance 角色、独立口令）
INSERT INTO sys_users (user_id, username, password_hash, display_name, role, company)
VALUES (gen_random_uuid()::text, 'admin',
        '$2b$12$3xWYyesK32hxWhnbb1QpPO.h1cR7z8iccADSZ2EOZqtXeFBzP8LbW',
        '系统管理员', 'hq_finance', NULL)
ON CONFLICT (username) DO NOTHING;

-- 被投企业财务 inv01~inv11（角色 investee_finance，company 一一对应 11 家企业）
INSERT INTO sys_users (user_id, username, password_hash, display_name, role, company)
VALUES
    (gen_random_uuid()::text, 'inv01',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投数科财务', 'investee_finance', '信投数科'),
    (gen_random_uuid()::text, 'inv02',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投智造财务', 'investee_finance', '信投智造'),
    (gen_random_uuid()::text, 'inv03',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投新能财务', 'investee_finance', '信投新能'),
    (gen_random_uuid()::text, 'inv04',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投医疗财务', 'investee_finance', '信投医疗'),
    (gen_random_uuid()::text, 'inv05',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投物流财务', 'investee_finance', '信投物流'),
    (gen_random_uuid()::text, 'inv06',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投环保财务', 'investee_finance', '信投环保'),
    (gen_random_uuid()::text, 'inv07',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投半导财务', 'investee_finance', '信投半导'),
    (gen_random_uuid()::text, 'inv08',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投云联财务', 'investee_finance', '信投云联'),
    (gen_random_uuid()::text, 'inv09',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投金服财务', 'investee_finance', '信投金服'),
    (gen_random_uuid()::text, 'inv10',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投教育财务', 'investee_finance', '信投教育'),
    (gen_random_uuid()::text, 'inv11',
     '$2b$12$vd5e5JjkLOVOjMYJMett5OeagTsfGuGut1esvCp4S7K/7P.jRXhCq',
     '信投文旅财务', 'investee_finance', '信投文旅')
ON CONFLICT (username) DO NOTHING;

-- ---------------- 知识库树（7 节点） ----------------
INSERT INTO kb_sources (kb_id, name, parent_id, kb_type)
VALUES
    ('kb-enterprise',   '企业知识库', NULL,         'internal'),
    ('kb-department',   '部门知识库', NULL,         'internal'),
    ('kb-project',      '项目知识库', NULL,         'internal'),
    ('kb-project-xgf',  '信投股份',   'kb-project', 'internal'),
    ('kb-project-xxjs', '信息建设',   'kb-project', 'internal'),
    ('kb-external',     '外部知识库', NULL,         'external'),
    ('kb-personal',     '个人知识库', NULL,         'internal')
ON CONFLICT (kb_id) DO NOTHING;
