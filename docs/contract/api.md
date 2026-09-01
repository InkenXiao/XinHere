# XinHere 前后端契约 v1（冻结）

> 唯一事实源。后端 W1 与前端 W2 均以此为准实现，偏差即 bug。
> 依据：`docs/design/02 §2/§3`、`03 §1-§3`、`07 §1-§6`；计划 `.trae/documents/xinhere-homepage-fullstack-plan.md` §4-§6。

## 0. 总则

- 下行 SSE（`text/event-stream`）+ 上行 REST POST。SSE 帧：`event: <type>`、`data: <json含seq/time>`、`id: <session_id:seq>`。
- 所有 REST 响应带 `X-Request-Id`；错误体统一 `{code, message, details?}`。
- 错误码封闭词表：`UNAUTHORIZED` / `FORBIDDEN` / `NOT_FOUND` / `VALIDATION_ERROR` / `RUN_BUSY`(409) / `INTERRUPT_MISMATCH`(409) / `SESSION_ARCHIVED`(409) / `UPSTREAM_ERROR`(502) / `INTERNAL`(500)。
- 认证：`Authorization: Bearer <token>`（POST /auth/login 获得）。除 `/auth/login` 与 `/healthz` 外全部需认证。
- 用户角色：`hq_finance`（本部财务：派发/审批/NA确认）、`investee_finance`（被投财务：填报/反馈）。
- 事件 payload 必带 `version: 1`；未知 type 且 `ignorable=false` → 加载端拒绝（fail-closed）。
- 所有金额单位：万元（number，非字符串）。

## 1. REST 端点

BASE = `/api/v1`

### 1.1 认证

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/auth/login` | `{username, password}` | `{token, user: UserInfo}` |
| POST | `/auth/logout` | - | `{ok: true}` |
| GET | `/auth/me` | - | `UserInfo` |

```ts
interface UserInfo { user_id: string; username: string; display_name: string;
  role: 'hq_finance' | 'investee_finance'; company: string | null }
```

### 1.2 会话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions` | `{title?}` → `SessionHeader`（domain 固定 `general`） |
| GET | `/sessions?limit=&offset=` | `{items: SessionListItem[], total}` 按 updated_at DESC |
| GET | `/sessions/{id}` | `SessionHeader & {stats}` |
| POST | `/sessions/{id}/chat` | `{message: string, kb_ids?: string[]}` → SSE 流（§2）。run 进行中 → 409 `RUN_BUSY` |
| GET | `/sessions/{id}/events?after_seq=&limit=` | Accept 双形态：`text/event-stream` → 先发 `baseline` 帧再续流；默认 JSON `{items: PlatformEvent[], has_more}` |
| POST | `/sessions/{id}/cancel` | 取消当前 run → `{ok}` |
| POST | `/sessions/{id}/feedback` | `{text}` → `{ok}` |

```ts
interface SessionHeader { session_id: string; user_id: string; title: string | null;
  domain: string; status: 'active' | 'archived'; created_at: string; updated_at: string }
interface SessionListItem extends SessionHeader { last_message: string | null; pending_interaction: boolean }
```

### 1.3 组件交互

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| POST | `/sessions/{id}/components/{component_id}/update` | `{draft}` | 落 `*-field-update` 事件，不唤醒模型 → `{ok, event_seq}` |
| POST | `/sessions/{id}/components/{component_id}/submit` | `{action: 'submit'\|'cancel', values?, interrupt_id}` | interrupt_id 不匹配 → 409 `INTERRUPT_MISMATCH` → `{ok, event_seq}` |

### 1.4 待办

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/todos?box=assignee\|dispatcher` | `{items: TodoItem[]}`。assignee=我的待办；dispatcher=我派发的（含 `na_confirm`/`feedback_review` 型） |
| POST | `/todos/{id}/feedback` | `{text}` → status=`feedback_submitted`，并向派发者产生 `feedback_review` 待办 |
| POST | `/todos/{id}/na` | `{reason}` → status=`na_pending`，向派发者产生 `na_confirm` 待办 |
| POST | `/todos/{id}/na-confirm` | 确认 → 原待办 `na_closed`（列表移除） |
| POST | `/todos/{id}/na-reject` | `{comment?}` → 原待办回 `pending` |
| POST | `/todos/{id}/complete` | → `completed` |

```ts
type TodoStatus = 'pending' | 'feedback_submitted' | 'na_pending' | 'submitted'
  | 'completed' | 'na_closed'
interface TodoItem {
  todo_id: string; task_id: string; kind: 'action' | 'na_confirm' | 'feedback_review' | 'review';
  scene: 'risk_fill' | 'cash_guarantee' | 'kpi_fill' | 'ms_feedback' | 'lamp_adjust' | 'report' | 'generic';
  title: string; sub: string;              // sub = 派送人/归属期等一行描述
  status: TodoStatus; lamp: 'r' | 'y' | 'g' | null;
  ref: Record<string, unknown>;            // 场景引用 {batch_id?, company?, report_id?, form_id?...}
  dispatcher_name: string; due: string | null; created_at: string; updated_at: string }
```

### 1.5 Dashboard

GET `/dashboard/summary` →

```ts
interface DashboardSummary {
  overview: { open_tasks: number; completed_7d: number; completion_rate: number; overdue: number };
  by_scene: { scene: string; total: number; done: number }[];
  todo_funnel: { status: TodoStatus; count: number }[];
  risk_board: { batch_id: string; period: string;
    companies: { company: string; status: 'unfilled' | 'filled' | 'reviewed' }[];
    lamps: { r: number; y: number; g: number } } | null;
  trend_14d: { date: string; created: number; completed: number }[] }
```

### 1.6 风险填报（页面与工具共用 service 层）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/risk-fills` | `{period}` (hq) → 创建批次+11 公司 report+待办 → `RiskBatch` |
| GET | `/risk-fills` | 批次列表 |
| GET | `/risk-fills/{batch_id}` | `RiskBatch & {reports: RiskReport[]}` |
| GET | `/risk-fills/{batch_id}/reports/{company}` | report+16 项 items |
| PUT | `/risk-fills/{batch_id}/reports/{company}/items` | `{items: RiskItemInput[]}` 存草稿（assignee） |
| POST | `/risk-fills/{batch_id}/reports/{company}/submit` | → filled；向 hq 产生 `review` 待办 |
| POST | `/risk-fills/{batch_id}/reports/{company}/review` | `{approve: boolean, comment?}` (hq) → reviewed/回退 unfilled |

```ts
interface RiskBatch { batch_id: string; period: string; dispatcher_id: string;
  status: 'collecting' | 'done'; created_at: string }
interface RiskField { k: string; v: string; pf?: boolean }   // pf=true 系统预填只读
interface RiskItem { idx: number; name: string; lamp: 'r'|'y'|'g'; fields: RiskField[] }
interface RiskReport { report_id: string; batch_id: string; company: string;
  status: 'unfilled'|'filled'|'reviewed'; lamp_r: number; lamp_y: number; lamp_g: number;
  items: RiskItem[] }
```

### 1.7 现金保障倍数

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/cash-guarantees` | `{company, period}` → 建单（带上月预填值）→ `CashReport` |
| GET | `/cash-guarantees?company=&period=` | 列表 |
| GET | `/cash-guarantees/{id}` | 详情 |
| PUT | `/cash-guarantees/{id}` | `{avail_cash, pooled_fund, avail_credit, monthly_outflow}` 存草稿（服务端重算 ratio/lamp） |
| POST | `/cash-guarantees/{id}/submit` | → submitted + hq `review` 待办 |
| POST | `/cash-guarantees/{id}/review` | `{approve, comment?}` → reviewed |

```ts
interface CashReport { form_id: string; company: string; period: string;
  avail_cash: number; pooled_fund: number; avail_credit: number; monthly_outflow: number;
  ratio: number;              // =(avail_cash+pooled_fund+avail_credit)/monthly_outflow
  lamp: 'r'|'y'|'g';          // ratio<=3 → r; <=6 → y; else g
  status: 'draft'|'submitted'|'reviewed' }
```

### 1.8 经营者考核（KPI 族）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/kpi/batches` | `{period}` (hq) → 批次+各公司指标行+待办 |
| GET | `/kpi/batches` · `/kpi/batches/{id}` | |
| GET | `/kpi/batches/{id}/companies/{company}` | `{indicators: KpiIndicator[], milestones: KpiMilestone[]}` |
| PUT | `/kpi/batches/{id}/companies/{company}/indicators` | `{indicators:[{indicator_id, content, base_score, max_score}]}` |
| PUT | `/kpi/batches/{id}/companies/{company}/milestones` | `{milestones:[{indicator_id, content, plan_date, material}]}` 拆分 |
| POST | `/kpi/batches/{id}/companies/{company}/submit` | → hq review 待办 |
| POST | `/kpi/batches/{id}/companies/{company}/review` | `{approve, comment?}` |
| POST | `/kpi/ms-feedbacks/dispatch` | `{period}` (hq) 发起里程碑反馈 |
| GET | `/kpi/ms-feedbacks?company=` | 我的里程碑反馈列表 |
| PUT | `/kpi/ms-feedbacks/{id}` | `{status, progress, actual_date?, lamp, status_note?}` 被投反馈 |
| POST | `/kpi/ms-feedbacks/{id}/submit` · `/review` | 提交/审批亮灯 |
| POST | `/kpi/lamp-adjust` | `{company, indicator_name, new_lamp, reason}` (hq) → 留痕 |

### 1.9 投后报告

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/reports` | `{company_ids: string[], period}` → `{report_id}`（异步生成，经事件族推送进度） |
| GET | `/reports` · `/reports/{id}` | 列表/详情（含 outline、content、status） |

### 1.10 知识库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/kb/sources` | `{items: KbSource[]}` 树 |
| POST | `/kb/search` | `{query, kb_id?}` → `{hits: {title, snippet, source}[]}`（MCP 代理；不可达→502 `UPSTREAM_ERROR`） |

```ts
interface KbSource { kb_id: string; name: string; parent_id: string | null; kb_type: 'internal'|'external' }
```

### 1.11 其他

GET `/plugins` → 插件清单 `[{name, version, domain, tools[], events[]}]`；GET `/healthz` → `{ok:true}`（免认证）。

## 2. SSE 事件帧

帧格式：`id: {session_id}:{seq}` + `event: {type}` + `data: {seq, time, ...payload}`。

### 2.1 框架事件

| event | data 要点 | 前端行为 |
|---|---|---|
| `turn/start` `turn/end` | `{turn, reason?}` | 流程边界 |
| `step/start` `step/end` | `{turn, step}` | 步骤计数 |
| `user/message` | `{content, source:'human'\|'inject', version:1}` | 用户气泡/inject 折叠行 |
| `assistant/chunk` | `{turn, step, delta, version:1}` | 流式尾部追加 |
| `assistant/message` | `{turn, step, content, usage?, source_event_seqs, version:1}` | 固化气泡 |
| `tool/call` | `{call_id, name, arguments, version:1}` | 工具卡 running |
| `tool/result` | `{call_id, name, content, is_error, outcome?, refs:[call_seq], version:1}` | 工具卡完成（refs 后端配对，前端零配对） |
| `component/request` | `{component_id, kind, version, props, interrupt_id}` | 渲染业务组件 |
| `component/submit-ack` | `{component_id, event_seq}` | 提交确认（不落日志） |
| `baseline` | `{projections, pending: {interrupts: [{component_id, kind, props, interrupt_id}]}}` | 重连状态恢复（不落日志） |
| `projection` | `{key, value, seq}` | 投影 last-wins |
| `error` | `{code, message}` | 终止帧 |

组件 kind 清单：`risk-dispatch-confirm@1`（发起确认卡）、`risk-fill-form@1`（16 项填报）、`cash-guarantee-form@1`（试算器）、`kpi-fill-form@1`、`ms-feedback-form@1`、`lamp-adjust-panel@1`、`pit-report-view@1`、`kanban-card@1`。

### 2.2 业务事件族（payload 均带 `version:1`，`extra=forbid`；JSON Schema 见 `packages/contracts/schemas/`）

| type | 要点 |
|---|---|
| `risk/fill-start` | `{batch_id, period, companies: string[], version}` |
| `risk/report-update` | `{batch_id, company, status, lamp_r, lamp_y, lamp_g, version}` 快照 |
| `risk/submit` | `{batch_id, company, summary, version}` |
| `risk/review` | `{batch_id, company, approve, comment?, version}` |
| `cash/form-start` | `{form_id, company, period, fields: CashReport 数值四件套, prev?: 同四件套, version}` |
| `cash/form-field-update` | `{form_id, draft: {avail_cash, pooled_fund, avail_credit, monthly_outflow}, version}` 全量快照 |
| `cash/form-submit` | `{form_id, values, ratio, lamp, summary, version}` |
| `kpi/batch-start` | `{batch_id, period, companies, version}` |
| `kpi/indicator-update` | `{batch_id, company, indicators 快照, version}` |
| `kpi/ms-split` | `{batch_id, company, milestones 快照, version}` |
| `kpi/ms-feedback` | `{feedback_id, company, milestone 快照+反馈, version}` |
| `kpi/lamp-adjust` | `{company, indicator_name, old_lamp, new_lamp, reason, version}` |
| `kpi/review` | `{batch_id, company, approve, comment?, version}` |
| `pit/report-start` | `{report_id, company_ids, period, outline: string[], version}` |
| `pit/report-update` | `{report_id, section_idx, content, version}` |
| `pit/report-done` | `{report_id, summary, version}` |
| `todo/changed` | `{box, todo_id, status, version}` ignorable=true |

## 3. Agent 工具（模型只选不填）

schema 只含选择字段；数据后端查库填充；返回模型摘要文本。

| 工具 | 参数 | 返回摘要 |
|---|---|---|
| `search_knowledge` | `{query, kb_id?}` | 命中条数+前 3 条摘要 |
| `list_companies` | `{}` | 11 家被投企业清单 |
| `dispatch_risk_fill` | `{period, company_ids?}` | interrupt→`risk-dispatch-confirm` 组件；确认后"已下发 N 家" |
| `get_risk_fill_status` | `{batch_id?}` | 各状态计数 |
| `start_cash_guarantee_fill` | `{company, period}` | interrupt→`cash-guarantee-form`；提交后"倍数 X，亮 Y 灯" |
| `dispatch_kpi_fill` | `{period}` | "已下发经营者考核填报 N 家" |
| `dispatch_ms_feedback` | `{period}` | "已发起里程碑反馈 N 项" |
| `adjust_lamp` | `{company, indicator_name, new_lamp, reason}` | "已调整并留痕" |
| `generate_post_report` | `{company_ids, period}` | "报告生成中（report_id）" |
| `dispatch_generic_task` | `{assignee_username, title, content, due?}` | "已派发" |
| `query_task_stats` | `{}` | 任务执行统计摘要 |

## 4. 种子数据（seed，幂等）

- 用户：`hq01`（李工/hq_finance/口令 `Xin@2026`）；`inv01`..`inv11`（被投财务，company=各公司，口令 `Xin@2026`）。
- 公司（11）：信投智造/信投新能/信投医疗/信投数科/信投物流/信投环保/信投半导/信投云联/信投金服/信投教育/信投文旅（inv01↔信投数科 演示主账号，其余按序）。
- `kb_sources`：企业知识库/部门知识库/项目知识库(子:信投股份、信息建设)/外部知识库/个人知识库。
- 16 项风险指标模板（字段与原型 RISK_ITEMS 对齐，预算值 pf=true）。
- KPI 指标模板 4 行（营业收入/净利润/数字化转型进度/合规经营）。
