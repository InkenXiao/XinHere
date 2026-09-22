# XinHere（信在此，新在此）

对话式业务组件平台：用户在聊天窗口用自然语言提出业务需求，模型输出文字 + 结构化事件，
前端用预设业务组件渲染（风险填报、资金保障、KPI 考核、投后报告等）；组件交互成为对话
上下文记忆，可基于填报内容派发后续任务。面向投资集团总部（hq）↔ 被投企业（investee）
的投后管理协作场景。

**架构思想借鉴 DeepSeek Harness**（一切皆插件、append-only 会话事件日志、事件族驱动
组件渲染），采用 React 19 前端壳 + Python/FastAPI/LangGraph 后端的轻量自研实现。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 19 + TypeScript + Vite 6 + Zustand 5（无 UI 库，自研 primitives） |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.0（psycopg）+ LangGraph 1.2 + deepagents + alembic + sse-starlette |
| 数据库 | PostgreSQL（库名 `xinhere`，20 张表），LangGraph checkpoint 同库 |
| LLM | OpenAI 兼容协议（`MAIN_API_URL`/`MAIN_MODEL` 可配）；知识库经 MCP 对接外部服务（`KB_MCP_URL`） |

## 目录

| 路径 | 内容 |
|---|---|
| `apps/backend/` | FastAPI 后端：平台层（agent/events/plugins/api/audit）+ 业务 services + 3 个业务插件表模型单一迁移源 |
| `apps/frontend/` | React 前端壳：shell（对话/工作台/驾驶舱）+ plugins 业务组件 + registry 装配器 |
| `packages/contracts/` | JSON Schema 单一事实源（core/risk/cash/kpi/pit）+ py/ts 双端生成 |
| `plugins/` | 业务插件三件套：`risk-warning`（风险填报/资金保障）、`kpi-assessment`（KPI 考核/里程碑/灯调整）、`post-investment`（投后报告） |
| `scripts/db/` | 数据库初始化 SQL（建表 / 基础数据 / COMMENT，与线上库逐列校验一致） |
| `scripts/lint_redlines.py` | 三红线静态检查 |
| `tests/` | 后端单测（`apps/backend/tests`）+ 全链路 e2e 13 个场景（`tests/e2e`） |
| `deploy/` | nginx.conf、前端运行时配置渲染（config.js.template）、发布脚本 publish.sh |
| `docs/` | `design/` 7 份开发设计文档；`reference/` dsh-01~13 源码考古 + PLAN v1.1 |
| `ui/` | 静态设计稿（index1/2.html） |

## 后端架构（apps/backend/app）

- **platform/agent**：LangGraph 执行器 `executor.py`（deepagents 图 + Postgres checkpointer +
  启动崩溃恢复）、SSE 桥 `stream_bridge.py`、事件翻译 `event_translator.py`、组件处理器
  `component_handlers.py`、工具层 `tools.py`/`tool_base.py`
- **platform/events**：事件存储 `store.py`（append-only，(session_id, seq) 连续）、
  状态派生 `derive.py`（投影到 platform_projections）、事件注册表 `registry.py`
- **platform/plugins**：插件装配 `loader.py`（fail-loud，启动时 discover）
- **platform/api**：路由前缀 `/api/v1`——auth / sessions(SSE) / todos / dashboard /
  risk_fills / cash / kpi / reports / kb / plugins
- **platform/audit + core/context**：审计上下文（ContextVar）+ 操作日志脱敏
- **services/**：risk / cash / kpi / report / todo / dashboard / kb 业务逻辑
- **persistence/**：SQLAlchemy 模型（20 张表）+ session 工厂（红线 1/3 的 ORM 事件钩子）

## 数据模型（20 张表）

- **平台表 5 张**（append-only，无审计字段）：`platform_sessions`、`platform_session_events`
  （(session_id, seq) 复合主键事件日志）、`platform_compensations`、`platform_operation_logs`
  （select/insert/update 全量留痕）、`platform_projections`
- **业务表 15 张**（三红线）：`sys_users`、`sys_auth_tokens`、`biz_tasks`、`biz_todos`、
  `risk_fill_batches/reports/items`、`cash_guarantee_reports`、`kpi_batches/indicators/
  milestones/ms_feedbacks/lamp_adjustments`、`pit_reports`、`kb_sources`

三条红线（`docs/design/07`）：
1. **逻辑删除**：业务表带 `is_delete`，session 级 `with_loader_criteria` 自动过滤已删行；
2. **审计四件套**：`created_by/created_at/updated_by/updated_at`（before_flush 自动填充，
   `updated_at` 由触发器 `set_updated_at()` 维护）；
3. **操作留痕**：insert/update/select 全部落 `platform_operation_logs`（含变更 diff 与
   查询摘要，敏感字段脱敏）。

## 数据库初始化

方式一（推荐）：发布脚本自动执行 alembic 迁移。

```bash
bash deploy/publish.sh          # 内含 alembic upgrade head（幂等）
```

方式二：手工执行 SQL 脚本（与 alembic 二选一；已对线上库逐列校验一致）。

```bash
psql -U dbuser -d xinhere -f scripts/db/01_create_tables.sql   # 20 张表 + 索引 + 触发器
psql -U dbuser -d xinhere -f scripts/db/02_seed_data.sql       # 种子数据（幂等）
psql -U dbuser -d xinhere -f scripts/db/03_comments.sql        # 表/字段 COMMENT
```

种子数据（见 `apps/backend/app/seed.py`）：
- 用户 13 个：`hq01`（李工，总部财务）、`admin`（管理员）、`inv01~inv11`（11 家被投企业
  财务，信投数科/智造/新能/医疗/物流/环保/半导/云联/金服/教育/文旅）；
- 普通口令 `Xin@2026`，管理员口令 `Xin@here#1234`（bcrypt 存储，见 .env 实际配置）；
- 知识库树 7 节点（企业/部门/项目（信投股份、信息建设）/外部/个人）。

## 部署（本机 docker compose）

```bash
vim .docker.env         # 首次配置：POSTGRES_* / MAIN_* / SSO / VITE_* 等（唯一配置文件）
bash deploy/publish.sh  # 前端构建 → 依赖变化才重建镜像 → alembic 迁移 → up -d
```

- 前端容器（xinhere-frontend）：内部 nginx 8096（`/api/` 反代 backend:8196），在
  `ai_network` 中静态 IP 172.28.200.20，不发布宿主端口；对外 HTTPS 由宿主外部 nginx
  容器（listen 8096 ssl，及 www.xinhere.cn:8099）反代该 IP 提供
- 后端容器（xinhere-backend）：内部 8196，仅 `ai_network` 内互通（无宿主端口），
  健康检查 `/healthz`；容器入口由 Dockerfile ENTRYPOINT 指定
- 改代码/改配置不重建镜像：后端源码、插件、契约、alembic 均卷挂载；前端重新 build dist
  后重启；前端运行时配置经 nginx entrypoint 渲染 `window.__ENV__` 注入（`deploy/config.js.template`）
- 外部依赖：`ai_network` 内 `pg_db`（PG）、`model-api`（LLM 网关）、知识库 MCP 服务

## 前端结构（apps/frontend/src）

- `shell/`：LoginPage、HeroHome、ChatPanel（对话）、ExecutionView（执行轨迹）、
  TodoPanel（待办工作台）、ScreenDashboard（驾驶舱）、SceneModal、TopBar、HistoryRail
- `plugins/`：8 个业务组件——RiskDispatchConfirm、RiskFillForm、KanbanCard、
  CashGuaranteeForm、KpiFillForm、MsFeedbackForm、LampAdjustPanel、PitReportView
- `registry/`：组件 manifest + ConversationAssembler（事件族 → 组件流装配）
- `state/`：zustand stores（auth / session / todo / ui）；`transport/`：api + SSE

## 测试

```bash
cd apps/backend && .venv/bin/pytest tests/          # 单测：事件并发/红线/种子幂等/todo 状态机等
pytest tests/e2e/                                   # e2e：13 个场景（auth→chat→风险→资金→KPI→报告→todo→驾驶舱→SSE 重连→崩溃恢复→红线→KB）
python scripts/lint_redlines.py                     # 三红线静态检查
```

## 文档阅读顺序

1. `docs/design/02-数据模型与事件词表.md`（地基）
2. `docs/design/01-系统架构与目录设计.md`（总纲）
3. `docs/design/03-接口协议设计.md` → `05-插件契约与加载器.md` → `04-前端设计.md`
4. `docs/design/07-七项关键约束实现规范.md`（红线规则）
5. `docs/design/06-实施计划与任务分解.md`（任务卡）

设计决策的源码级依据见 `docs/reference/`（dsh-01~13 为 DeepSeek Harness 源码精读）。

## 状态

已实现并可部署（M1 平台壳 + M2 业务插件完成，2026-08）。当前入口为三个业务场景
（风险填报 / 资金保障 / KPI 考核）+ 投后报告 + 待办工作台 + 驾驶舱。
