# 业务型 Agent 对话平台 — 技术方案与实施 Plan

> **文档状态（2026-08-15 起）**：本文是早期方案，开发的直接依据以 `docs/design/01~07` 为准。
> 以下条目已被设计文档取代：React 18 → **React 19**；WebSocket → **SSE 下行 + REST 上行**；
> `apps/web` + `apps/api` → `apps/frontend` + `apps/backend`；§4.3 的 user-question promise →
> **LangGraph interrupt + Command(resume)**；另新增业务表三条红线（逻辑删除 / 审计字段 /
> 系统级操作日志，见 design 02 §2.5/§2.6）。

> 版本 v1.1（2026-08-15）
> v1.1 变更：新增第 8 节"七项关键约束"（事件版本化、interrupt 生命周期、双写一致性、
> 模型自由度、上下文经济性、工具层数据权限、契约测试），并更新现有后端复用结论。
> 架构蓝本：借鉴 DeepSeek Harness（github.com/deepseek-ai/deepseek-harness，MIT）的
> "一切皆插件 + append-only 会话事件日志 + 事件族驱动组件渲染" 设计，按我们业务裁剪自研。

## 1. 目标与设计原则

**产品形态**：一个对话式工作台。用户在聊天窗口用自然语言提出业务需求（如"我要填报财务指标"），
模型输出文字说明 + 结构化 JSON，前端用预设业务组件渲染该 JSON（如复杂财务填报表单）；
用户在组件上的交互成为对话上下文的记忆，可基于填报内容继续派发后续任务。

**核心设计原则（一切为了 AI Coding 的效率、风险、经济性）**：

1. **壳与业务彻底分离**：前端主页面只是一个"可动态加载/渲染插件的容器"，只提供框架能力
   （对话流、组件注册表、通信层、鉴权、主题）；后端只提供框架能力（模型访问、记忆、
   数据库连接、日志、插件注册）。**每次新增/修改业务 = 新增一对独立的前端组件 + 后端接口，
   不触碰框架代码**。
2. **组件与接口成对、独立、可插拔**：一个业务能力 = `component-plugin`（前端）+ `service-plugin`
   （后端），二者只通过公开契约（JSON Schema + 事件类型）耦合，互不依赖其他业务插件。
3. **append-only 会话事件日志是唯一事实源**：模型可见的内容、组件渲染状态、用户交互，
   全部以事件形式追加到会话日志；组件状态由事件回放得到，刷新/重开/导出均可恢复。
4. **AI Coding 友好的代码组织**：每个插件是一个小而自包含的目录（契约、实现、测试、README
   放在一起），AI 一次只改一个插件目录，上下文小、边界清晰、可独立验证、可独立回滚。

## 2. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 前端框架 | React 18 + TypeScript + Vite | 与 dsh 蓝本一致；生态成熟，AI 生成质量高 |
| 前端状态 | Zustand | 轻量、样板少，AI coding 出错率低 |
| 前端样式 | CSS Modules 或 UnoCSS | 不引重型组件库；复杂表单组件自建 |
| 通信 | WebSocket（对话流）+ REST（CRUD） | 流式输出必须 WS；业务接口走 REST 更简单 |
| 后端框架 | Python 3.12 + FastAPI | 已定 |
| Agent 编排 | LangChain（含 LangGraph 做有状态流转） | 已定 |
| 数据库 | PostgreSQL（业务数据）+ JSONB（事件日志） | 事件日志天然适合 JSONB；一库两用降低运维 |
| 记忆 | 短期：会话事件日志回放；长期：PG 向量（pgvector）+ 摘要 | 先不上独立向量库 |
| 部署 | 前端静态托管 + 后端 Docker Compose | 单体后端、模块内插件化，不搞微服务 |

### 2.1 现有后端复用（重要结论）

经对现有 `D:\Project\ai-backend\packages\harness\agent_harness` 的代码评估：
**保留其作为后端壳核心，改造而非重写**。它已实现本方案最难的三块——

- 组件中断闭环：LangGraph `interrupt` → SSE `component_request`（interrupt_id + payload）
  → 前端渲染 → `Command(resume=...)` 回传继续（`executor.py`）；
- 会话记忆：`thread_id` = session_id + checkpointer；
- 流式与审计：StreamBridge（SSE 断线 agent 不终止）、append-only 审计事件表。

需要补齐：UI 回放用的会话事件日志表（现有 `agent_audit_events` 是 trace 审计视角，
非 UI 回放事件流）、组件交互业务事件族落库、插件化工具发现注册、submit 后的结构化摘要注入。
**不引入 DeepSeek Harness 的代码或其 SDK**——其 SDK 是 headless JSON-RPC，拿不到 Web
聊天页与组件渲染，引入等于换运行时重写，净损失。dsh 仅作架构参考文档。

**明确不做的**（从 dsh 裁剪掉）：沙箱、审批策略、subagent、MCP、profile/bundle 多层组装、
多 UI face。我们是单一 Web 产品 + 业务表单场景，用"单壳 + 插件目录约定"替代 Cordis 的完整 IoC。

## 3. 总体架构

```
┌──────────────────────────────────────────────┐
│  前端壳 (apps/web)                             │
│  ├─ 对话流视图（消息列表 = 节点流渲染）          │
│  ├─ 组件注册表 ComponentRegistry（动态加载）     │
│  ├─ 事件回放引擎（事件流 → 各组件 State）        │
│  └─ 框架能力：鉴权/主题/WS连接/表单基础件        │
└───────────────┬──────────────────────────────┘
                │ WebSocket(事件流) + REST(业务)
┌───────────────┴──────────────────────────────┐
│  后端壳 (apps/api, FastAPI)                    │
│  ├─ 会话服务：append-only 事件日志、投影        │
│  ├─ Agent 服务：LangChain/LangGraph 编排、      │
│  │   工具注册表（业务插件注册为 tool）           │
│  ├─ 记忆服务：会话内回放 + 跨会话摘要/pgvector   │
│  └─ 框架能力：DB/日志/鉴权/配置/插件发现加载     │
└──────────────────────────────────────────────┘
         │ 依赖注入（仅依赖契约）
┌────────┴─────────────────────────────────────┐
│  业务插件（plugins/*，每个独立成对）             │
│  plugins/financial-report/                    │
│    ├─ contract  (JSON Schema + 事件类型，前后共享)│
│    ├─ frontend/ (React 组件 + 注册入口)         │
│    └─ backend/  (FastAPI router + LangChain tool)│
└──────────────────────────────────────────────┘
```

## 4. 核心机制设计（借鉴 dsh 的四个关键机制）

### 4.1 会话事件日志（Session Event Log）

- 每个会话一个 append-only 事件流（PG 表 `session_events`：`seq, session_id, kind, payload, created_at`）。
- 事件类型举例：`user/message`、`assistant/message`、`assistant/chunk`、
  `tool/call`、`tool/result`、以及**业务事件族**如 `report/form-start` / `report/form-field-update` / `report/form-submit`。
- **模型可见即可重建**：任何要进入模型上下文的信息必须以事件存在；
  模型的消息历史 `derive_messages()` 从事件日志投影得到（dsh 同款不变量）。

### 4.2 业务组件渲染（= dsh ConversationNode，简化版）

- 模型不直接"输出组件"。模型调用业务工具（如 `financial_report_form`），后端工具执行后
  追加 `report/form-start` 事件（payload 即组件所需的 JSON：公司列表、期间、指标定义等）。
- 前端组件注册表按事件的 `kind` 找到对应 renderer，`financial-report` 组件订阅
  该事件族（start/update/submit），增量组装出表单 State 并渲染。
- 组件状态由事件回放得出 → 刷新/重开会话，填到一半的表单原样恢复。

### 4.3 用户交互回灌（记忆）

- **组件内交互落日志**：用户在组件上的关键操作（改筛选、提交）由前端追加对应 update/submit 事件（经后端 REST 确认写入）。
- **注入模型上下文**：submit 事件后，后端将结构化摘要通过 `inject` 通道加入模型输入队列，下一次模型请求即可看到"用户已填报 X/Y/Z"。
- **回合中间暂停**：需要模型等待用户完成操作时，工具内部 `await` 一个 user-question
  promise（前端弹交互、答案回传后 resolve），模仿 dsh 的 `ctx.userQuestions` Provider 机制。
- **长期记忆**：会话摘要 + 关键业务事实（如"用户负责 A/B 公司"）写入 pgvector，新会话检索注入。

### 4.4 插件契约（前后端唯一耦合点）

每个业务插件目录内含 `contract/`：

- `schema.json`：组件 props 的 JSON Schema（也是模型工具的参数约束）；
- `events.ts / events.py`：事件 kind 与 payload 类型（单一来源，双向生成）；
- `plugin.json`：名称、版本、权限声明（要访问哪些表、注册哪些 tool/router）。

**新增一个业务的固定动作**：复制插件模板 → 填 contract → 写 backend（router + tool）→
写 frontend（renderer）→ 注册即生效。框架代码零改动。

## 5. 仓库结构（monorepo）

```
biz-agent-platform/
├─ apps/
│  ├─ web/                    # 前端壳（React）
│  │  └─ src/{shell,registry,replay,ws,primitives}
│  └─ api/                    # 后端壳（FastAPI）
│     └─ src/{core,session,agent,memory,tools,plugins_loader}
├─ plugins/
│  └─ financial-report/       # 示例业务插件（首个）
│     ├─ contract/
│     ├─ frontend/            # 独立 vite lib 构建，壳动态 import
│     └─ backend/             # 独立 Python 包，入口声明 router + tools
├─ packages/
│  └─ shared-contracts/       # 事件/消息公共类型（前后端各生成一份）
├─ templates/
│  └─ plugin-template/        # AI coding 用的插件脚手架
├─ docker-compose.yml
└─ README.md
```

前端插件加载策略：**首期构建时打包**（Vite 动态 import + 注册表清单，够用且零运行时风险）；
远期如需运行时热插拔再评估 Module Federation / importmap，不作为首期目标。

## 6. 里程碑

**M1 — 框架骨架（约 2-3 周，基于现有 agent_harness 改造）**
- 补齐后端壳：`session_events` 表与投影、插件发现加载器、基于现有 executor/StreamBridge 的复用。
- apps/web：对话流视图、WS 连接、组件注册表（静态清单 + 动态 import）、事件回放引擎雏形。
- 落实约束：8.1 事件版本化、8.3 双写顺序。
- 交付标准：纯文字对话可用；事件日志可回放重建完整会话。

**M2 — 组件渲染链路打通（约 2 周）**
- 业务工具调用 → 业务事件 → 前端 renderer 的完整链路（复用现有 interrupt/component_request 机制）；
- inject 摘要通道；以一个"简单表格展示组件"作为管线验证件。
- 落实约束：8.2 interrupt 生命周期、8.5 摘要注入、8.7 契约测试进 CI。
- 交付标准：模型一句话触发组件渲染；组件交互事件落日志并影响下一次模型回复。

**M3 — 首个真实业务：财务指标填报（约 3-4 周）**
- financial-report 插件全链路：筛选（公司/期间）→ 大表单组件 → 草稿/校验/提交 →
  填报结果注入上下文 → 基于填报内容派发任务的第二个工具。
- 落实约束：8.4 模型只选不填、8.6 工具层数据权限、8.5 长会话压缩 v1、长期记忆 v1（pgvector）。
- 交付标准：完成"填报 → 基于填报内容派发任务"的完整用户旅程。

**M4 — 工程化与 AI Coding 闭环（持续）**
- plugin-template + CONTRIBUTING 中的《新增插件 SOP》；
- 每插件独立测试（后端 pytest、前端 vitest）+ 契约校验（schema 对齐 CI）；
- 评估 LangSmith / 自建 trace 的调用观测。

## 7. 主要风险与对策

| 风险 | 对策 |
|---|---|
| 模型输出的 JSON 不符合组件 schema | 工具参数走 function calling 结构化约束；后端再做一次 pydantic 校验，失败返回修正提示 |
| 插件间隐性依赖滋生 | CI 强制：插件只能 import `shared-contracts` 与框架公开 API；违规即失败 |
| 大表单事件流过大 | 事件携带 whole-value checkpoint（dsh 建议），字段级 update 只保留最近态 |
| 老会话回放遇组件改版 | 事件 payload 带 version，renderer 按版本兼容（约束 8.1） |
| 组件提交双写不一致 | 先日志后业务库 + 补偿 worker 重放（约束 8.3） |
| 对话入口越权 | 数据权限落在工具层（约束 8.6） |
| LangChain 版本变动 | 编排层薄封装，业务插件只见 tool 接口不见 LangChain |
| 团队 React 经验 | 壳与基础件由 1-2 人维护；业务插件是"组件 + schema"的小单元，AI 生成 + 模板约束可降低门槛 |

## 8. 七项关键约束（动手前定死 / 各里程碑落实）

这七条是评审中识别的盲区，违反任何一条都会在后期付出数倍代价。

### 8.1 事件 schema 版本演进（M1 落地）

append-only 日志 + 会演进的组件 = 必须有版本策略，否则老会话回放会炸。

- 每个事件 payload 必带 `version` 字段，renderer 按 `组件kind + version` 分发兼容渲染；
- **事件格式只加不改**：已发布的事件 payload 字段永不重命名/改语义，新需求加新字段或新事件 kind；
- contract 变更视为破坏性变更，走插件版本升级流程（新 version 并行存在，不覆盖旧版）。

### 8.2 interrupt 完整生命周期（M2 落地）

中断不只是"等 resume"，必须覆盖：

- **放弃路径**：用户关闭/取消组件时发送 cancel（resume 一个显式 `{"action":"cancelled"}`），
  图据此走收尾分支，不允许 interrupt 永久挂起；
- **超时策略**：组件停留超时的处理要显式定义（提醒 / 自动 cancel / 永不超时，选一个）；
- **多 interrupt 并存**：同一会话多个未完成组件时，resume 必须携带 interrupt_id 对应回具体节点，
  禁止"回传给最近一个"的隐式匹配。

### 8.3 双写一致性（M1 落地）

组件提交 = 事件落日志 + 业务落库，两个写必须定死顺序：

- **先写事件日志（事实源），后执行业务落库**；业务落库失败可由事件重放补偿（补偿 worker 扫描
  已 submit 未落库的事件）；
- 前端组件状态永远以事件日志回放为准，不承认前端本地草稿为事实（本地草稿可以缓存，提交才算数）。

### 8.4 模型自由度压到最低（贯穿工具设计）

- 模型只输出**选择**（哪个组件、什么筛选条件），**不生成组件内容数据**——
  公司列表、指标数值等一律由后端工具查库填充进事件 payload；
- 工具参数用 pydantic 严格校验，模型给不出合法参数时返回修正提示而非硬闯；
- 这是财务场景的幻觉防线：模型永远不碰"数据长什么样"，只决定"看什么"。

### 8.5 上下文经济性（M2 落地）

- **事件日志 ≠ 模型上下文**：注入模型的永远是后端生成的结构化摘要
  （"已填报 3 家公司 12 项指标，合计 X"），原始明细留在日志按需取；
- M3 配套长会话压缩（compaction）：超过阈值时将早期事件折叠为摘要事件（dsh 有对应子系统可参考）。

### 8.6 数据权限在工具层（M3 落地）

- "用户只能填分管公司"这类规则必须落在**业务工具查数据的环节**：模型调工具时携带用户身份，
  工具内部过数据权限过滤，而不是前端组件隐藏选项；
- 对话式入口是越权的新通道，工具层是唯一可靠的闸门。

### 8.7 契约测试是 AI Coding 闭环的前提（M2 起进 CI）

AI 批量生成插件的错误必须被 CI 拦住而不是运行时炸：

- contract schema 前后端一致性校验（TS 类型与 pydantic 模型从同一 JSON Schema 生成）；
- 事件 payload 符合事件定义校验（回放测试：构造事件序列 → 断言组件 State）；
- 工具参数校验测试（合法/非法参数各一组）。此项目从 M2 就进 CI，不等 M4。

## 9. 对外表述（口径）

"本平台借鉴 DeepSeek 开源的 DeepSeek Harness 框架的'一切皆插件'架构思想
（Cordis 驱动、append-only 会话事件日志、事件族驱动 UI 渲染），采用 React 前端容器 +
Python/FastAPI/LangChain 后端的轻量化自研实现，业务能力以
'前端业务组件 + 后端业务接口'成对插件的方式持续扩展，并结合 AI Coding 工作流高效迭代。"
