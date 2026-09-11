-- 20260910 · Xin台卡片跳转地址绑定（幂等，可重复执行）
-- 变更内容：
--   1. 文档生成卡片 (docgen)  → 文档生成工作台（redirect 回运营管理系统登录页）
--   2. 运营管理系统卡片 (ops) → 运营管理系统年度考评页
-- 说明：cockpit_cards 为配置表（应用启动 seed 仅补缺失 key，不覆盖已有值），
--       存量记录须由本脚本更新；仅当链接仍为旧值 /pro/ 时才改，避免覆盖用户在
--       管理端的自定义配置。同步代码 seed 见 cockpit.py SEED_CARDS。

UPDATE cockpit_cards
SET link_url = 'http://192.168.1.161:8003/workbench?redirect=http://192.168.1.161:9096/login'
WHERE key = 'docgen' AND link_url = '/pro/';

UPDATE cockpit_cards
SET link_url = 'http://192.168.1.161:9096/dashboard/AnnualEvaluation/index'
WHERE key = 'ops' AND link_url = '/pro/';
