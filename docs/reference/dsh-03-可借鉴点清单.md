# dsh 源码精读：对我们业务最有价值的可借鉴点（带源码定位）

> 目标系统：对话式业务组件平台（React 前端壳 + Python/FastAPI/LangChain 后端 + agent_harness）。
> 每条 = dsh 的真实实现（可点击跳转）+ 对我们系统的具体落地方式。行号基于 0.1.0-rc.5。

## A. 业务组件渲染链路（财务填报组件直接相关）

### A1. 工具卡片按名分发 + 通用降级——组件注册表的渲染原型
- dsh：`ToolCallTree.tsx` 用 `renderSlot('tool.call.toolview', owner, { entryKey: toolName, fallback: <GenericToolCard/> })`
  按工具名分发，未注册的渲染通用卡片（自动分类 search/read/shell/write/edit）。
  源码：packages/client/ui-tool/src/client/tool/ToolCallTree.tsx:38
- 我们落地：前端组件注册表 `{ [toolName]: lazy(ReactComponent) }`，渲染时
  `registry[name] ?? GenericCard`。**保证插件前端没加载/没安装时也不白屏**——这是 AI 批量
  生成插件的安全网。

### A2. 后端声明的呈现钩子是纯函数——"回放即重渲染"的前提
- dsh：`ToolDefinition.presentCall(args)` / `presentResult(args, result)` 定义进行中/完成态
  的 UI 视图，注释明确要求"Pure and side-effect-free: a UI may call it during live streaming
  AND a session-log replay"。
  源码：packages/core/tools/src/index.ts:279、packages/core/tools/src/index.ts:287
- 我们落地：财务组件的 props 必须可从 `(tool_args, tool_result)` 纯推导，禁止组件自己
  fetch 或读前端环境。写进插件模板的 lint 规则。

### A3. 模型只见 name/description/parameters——"模型只选不填"的源码级依据
- dsh：`schemas()` 白名单只下发工具三要素，`timeoutMs` 等内部字段"is NEVER sent to the model"。
  源码：packages/core/tools/src/index.ts:251
- 我们落地：工具定义中显式分两个区：`model_facing`（name/description/parameters schema）
  和 `internal`（超时、呈现、权限标记）。CI 校验 internal 字段不出现在下发 schema 里。

### A4. 事件族带稳定业务 id + checkpoint 优先——大表单状态回放的规则
- dsh：ConversationNode 规则——每条事件必须携带稳定业务 id（或可从 payload 独自推导），
  "Client 绝不能把 update 猜测为属于最近一个未完成 Context"；能发 whole-value checkpoint
  就优先发（start 在窗口外仍可用）。
  源码：docs/cookbook/adding-a-conversation-node.zh.md（第 1 节"设计可回放的事件族"）
- 我们落地：`report/form-start`（全量快照：公司列表+指标定义）、`report/form-field-update`
  （带 formId + 当前全量草稿）。分页加载老会话时无需扫描全窗口。

## B. 事件日志与审计（对应我们的 session_events 表）

### B1. 事件信封五字段 + 判别联合——表结构直接照抄
- dsh：`{ type, seq, time, data, ignorable? }`，surface 事件（user/message、assistant/message、
  tool/result）额外携带 `sourceEventSeqs`（引用构建它的更早事件）和 `surfaceOp`。
  源码：packages/core/session/src/types.ts:404（信封）、packages/core/session/src/types.ts:422（ignorable）
- 我们落地：`session_events(seq BIGINT PK, session_id, type, time, data JSONB, ignorable BOOL)`；
  pydantic 判别联合校验 type↔payload 匹配；`assistant/message` 类聚合事件带
  `source_event_seqs` 指回 chunk 批次。

### B2. seq 连续性双侧硬校验——防日志篡改/丢失的最便宜手段
- dsh：`events[i].seq === i` 跨全日志连续；append 拒绝不连续批次。
  源码：packages/session/session-persistence-jsonl/README.md:17
- 我们落地：PG 层 `(session_id, seq)` 唯一约束 + 写入前校验 seq = max+1。遥测去重键也用它。

### B3. 未知事件 fail-closed——"宁拒不猜"
- dsh：读者遇到词表外的 type 且无 `ignorable: true` 标记，必须拒绝重建整个会话而非静默跳过；
  注释说得很清楚：默认 required，忘标只会"过度拒绝"（不便）而非"静默吞掉关键事件"（错误）。
  源码：packages/core/session/src/types.ts:412-421
- 我们落地：事件 kind 注册表 + 加载时校验；新插件事件先注册再写；历史兼容靠
  `ignorable` 字段。

### B4. 崩溃恢复：合成收尾 + TOOL_OUTCOME_UNKNOWN——断线/崩溃语义的现成答案
- dsh：崩溃恢复保留完整帧、截断坏尾、**合成**工具/step/turn 关闭事件；有调用无结果的
  工具恢复为 `TOOL_OUTCOME_UNKNOWN`，"tells the model to retry only read-only or idempotent
  work"。
  源码：packages/session/session-persistence-jsonl/README.md:60
- 我们落地：启动扫描 RUNNING 状态的 trace/session，补终态事件；长填报组件中断后恢复时，
  工具结果标注 unknown，模型侧 prompt 提示幂等重试。

### B5. 批量写窗口——流式事件不逐条落库
- dsh：200ms 固定合并窗口（后来者不重置计时），flush/teardown 绕过窗口强制清空。
  源码：packages/session/session-persistence-jsonl/README.md:30
- 我们落地：SSE chunk 批量 buffer，`asyncio` 定时 flush + 会话结束强制 flush。

## C. 交付与遥测（审计数据出进程的规矩）

### C1. 遥测 emit 必须非阻塞 + 交付游标 + at-most-once
- dsh：`emit()` 必须非阻塞（同步跑在事件流上）；模块级 `WeakMap<Session, seq>` 只记
  "已交接"最高水位（非已送达）；resume 不回补丢件（要补投就上 outbox，刻意推迟）；
  接收端按 `(session.id, event.seq)` 去重。
  源码：packages/session/session-telemetry/README.md:27
- 我们落地：审计/分析外发走独立 queue（如 Redis/内存队列）+ water mark；不要在事件写入
  路径上做任何网络 IO。

### C2. "同意即日志"——feedback-only 遥测的合规设计
- dsh：`FEEDBACK_ONLY` 模式只在日志里存在 `feedback/record` 事件时才导出该前缀；
  "treats only the exact feedback/record object already stored at session.events[seq] as
  consent; an independently emitted bus value is ignored"——内存里的总线消息不算同意凭据。
  源码：packages/session/session-telemetry-otel/README.md（Mode 表 FEEDBACK_ONLY 行）
- 我们落地：财务数据敏感，遥测/分析导出默认 disabled，用户反馈时以落库的反馈事件为
  唯一同意凭据导出前缀。这套语义可直接抄给合规。

### C3. 脱敏瀑布只改外发副本
- dsh：`sessionTelemetry/record` 瀑布，抛错的监听器使该条记录 fail-closed 扣留；
  "Redaction applies to the outbound copy only; the canonical session log is never rewritten."
  源码：packages/session/session-telemetry/README.md（The redact waterfall 节）
- 我们落地：外发管道支持注册脱敏规则（公司名/金额脱敏），规则抛错扣留该条而非放行。

## D. 派生信息的提升（日志之上的增值层）

### D1. 投影单元：init/apply/view 三纯函数 + stateVersion——统计/TODO 的服务端实现模板
- dsh：`ProjectionDefinition { key, schema, init, apply, view, stateVersion }`，apply 是
  `(state, event) => state` 纯转移（不关心的事件返回同一引用→零下游开销）；stateVersion
  bump 让旧持久化缓存行丢弃而非错误前推。
  源码：docs/subsystems/session-projection.zh.md:22（定义）、:53（stateVersion）
- 我们落地：会话统计（轮数/耗时/token）、当前填报进度条，都做成这种服务端投影，
  前端 last-wins 直接渲染，不自己折叠事件。

### D2. 会话锁定插件组合（preset lock）——组件集变更的边界规则
- dsh：会话创建时锁定 preset，中途拒绝切换（`agent-preset-locked`），理由："这段历史是在
  第一套工具下产生的，换组合等于回放一个模型无法继续的历史"。
  源码：packages/client/ui-agent-preset/README.md:9、:17
- 我们落地：财务会话只装配财务域插件；会话中途部署了插件新版时，运行中会话继续用旧集，
  新会话才用新集。header 里记插件集版本。

### D3. 作用域注册表 + 重名 fail-loud——插件多了的卫生底线
- dsh：工具重名注册直接抛错（含 per-agent 变体的指引文案）；`tools/change` 事件通知 UI
  刷新；scope 机制让工具按会话/agent 粒度可见。
  源码：packages/core/tools/src/index.ts:727、packages/core/scope/README.md
- 我们落地：后端启动时收集全部插件工具，重名直接启动失败；每会话按业务域白名单装配。

### D4. 审计自身也被审计——元数据 LLM 调用也记事件
- dsh：会话标题由 LLM 生成，而这次 LLM 请求本身记为 `session/title-llm-request` 事件。
  源码：packages/core/session/src/known-event-types.ts:47
- 我们落地：所有后台 LLM 调用（标题、摘要、记忆压缩）都记事件含 token 消耗——
  成本核算和审计口径才闭环。

## E. 落地顺序建议（对应 plan 里程碑）

| 借鉴点 | 进 plan 的位置 | 优先级 |
|---|---|---|
| A1/A2/A3 组件注册表+纯函数 props+模型只选不填 | M2（渲染链路） | 必须 |
| B1/B2/B3 事件表五字段+seq 校验+fail-closed | M1（骨架） | 必须 |
| B4/B5 合成收尾+批量写 | M1 | 必须 |
| D2 preset 锁（会话锁插件集） | M1（表设计时预留 header 版本字段） | 必须 |
| A4 checkpoint 优先的事件族 | M3（大表单） | 必须 |
| D1 投影单元 | M3（统计/进度条） | 建议 |
| D3 重名 fail-loud + 域白名单 | M2（插件加载器） | 必须 |
| C1-C3 遥测交付/同意/脱敏 | M4（或按合规提前） | 按需 |
| D4 元调用记事件 | M3（记忆压缩上线时） | 建议 |
