# dsh UI 全链路：从注册到消费到回显

> 基于 deepseek-harness 源码（0.1.0-rc.5）。源码引用均为仓库内真实路径。
> 核心结论：**前端没有"页面"，只有注册表**。所有 UI（聊天节点、工具卡片、输入框接管、
> 视图 Tab、侧栏、设置页）都是插件向 slot/注册表贡献的条目；所有显示状态都是会话事件日志的投影。

## 0. 全链路总览

```
插件包(package.json 声明 dsh.client.inject)
  → 构建期：同包编译出 client 面 (tsconfig.client.json + DSH_BUILD_FACE=client)
  → 浏览器：client Cordis 运行时启动，插件挂载
  → 注册：三类注册（slot renderer / ConversationNodeDefinition / 投影消费）
  → 消费：Host WS 事件流 → SessionRuntime 分发 → ConversationNodeAssembler 折叠 → ChatFlow 渲染
  → 回显：重开会话 → 读 JSONL 日志 → 同一 Assembler 按 seq 回放 → 状态原样重建
```

## 1. 注册层：插件如何把 UI "放进"页面

### 1.1 一个插件包如何同时贡献后端和前端

插件包在 `package.json` 的 `dsh` 字段声明前端注入：

```json
"dsh": { "client": { "platform": "web", "inject": [...], "immediately": true } }
```

同一份源码用 `tsconfig.host.json` / `tsconfig.client.json` 编译两次（构建变量
`DSH_BUILD_FACE=host|client`），host 面跑在 Node 注册服务/工具，client 面打进
web bundle 注册 UI。两端共享同一份事件类型（declaration merging）。

### 1.2 Slot 体系（`packages/client/ui-slots`）

Slot 是 dsh 前端插件化的底座，**React-free**（`renderer.ts` 明确"React-free contracts"）：

- **声明与注册**：宿主包（如 ui-conversation）声明 slot（如 `conversation.chat.node`），
  业务插件 `ctx.slots.register({ name, key }, RendererComponent)` 注册 keyed 条目。
  向未声明的 slot 注册会抛错。
- **声明注入**：`ctx.slots.inject(name, callback)` 让贡献插件在 slot 声明存在时才激活，
  回调返回 disposer（或生成器 yield 多个注册，作为一个事务回滚）。
  声明销毁/重声明都会重跑回调（专用 epoch 机制）。
- **渲染分发**：宿主调用 `renderSlot(name, props, { entryKey, fallback })`，
  按 entryKey 查注册表渲染对应组件，查不到渲染 fallback。**没有中央 switch**。

Slot 的 key 语义各通道不同：工具卡片按**工具名**，聊天节点按**节点 kind**，
视图按 **tab id**。

### 1.3 四条 UI 注入通道（可在同一轮对话共存）

| 通道 | 注册方式 | key | fallback |
|---|---|---|---|
| 工具卡片 | `tool.call.toolview` slot | 工具 wire 名 | GenericToolCard（自动分类 search/read/shell/write/edit） |
| 聊天业务节点 | `ctx.conversationEvents` 注册 Definition + `conversation.chat.node` 注册 renderer | 节点 kind | 不渲染/占位 |
| Composer 接管 | `conversation.composer` slot（selector-routed） | 路由条件 | 原 InputBar |
| 视图 Tab / 侧栏 / 设置页 | `conversation.view`、`conversation.session.header.actions`、`settings.section` 等 slot | tab/条目 id | 无 |

工具卡片通道的关键源码 `ui-tool/src/client/tool/ToolCallTree.tsx`：

```ts
renderSlot('tool.call.toolview', owner, {
  entryKey: toolName,
  fallback: <GenericToolCard {...owner} t={t} />,
})
```

业务 UI 包**只注册自己的工具名和原子视图**——不管 Session 事件配对、不重建
transcript、不管 root/subcall 拓扑（"They do not pair Session events, rebuild the
transcript, or own root/subcall topology"，ui-tool README）。

### 1.4 工具自带的呈现钩子（后端声明 UI）

`ToolDefinition`（`packages/core/tools/src/index.ts`）内嵌两个纯函数 UI 钩子：

- `presentCall(args)` → 进行中状态的卡片视图（ToolCallView，card-tagged render intent）；
- `presentResult(args, result)` → 完成态视图（ToolResultView）。

约束：**纯函数、无副作用**，因为 UI 会在流式期间和日志回放两个场景调用它。
另外 `ToolOutputDefinition.presentationMeta()` 生成可回放的呈现元数据。
`schemas()` 白名单只把 name/description/parameters 给模型——presentCall/timeoutMs
等永远不进模型视野。

## 2. 消费层：事件流如何变成组件

### 2.1 连接与分发（`packages/client/connection` + `client/runtime`）

- WebSocket 连 Host；Host 的 mux 流推 `host/remote-event` 帧；
  runtime 把每帧交给 `ctx.remote.$dispatch`，领域包通过 `ctx.remote.$on` 订阅。
- `SessionRuntime` 持有 Session 对象、共享事件窗口（shared event window）和历史分页；
  `WorkspaceRuntime` 管工作区列表。客户端**不持有任何"会话前状态"**——
  会话永远是 Host 生成的（"Client sessions are always Host-born"）。

### 2.2 ConversationNodeAssembler（核心，`client/runtime` README "Conversation assembly"）

每个 Session 有一个 Assembler，职责：把连续事件窗口折叠成最终节点。

- **Definition 契约**（业务插件提供）：
  1. 事件 → 稳定 `{kind, id}` 的映射（一个事件属于哪个 Context）；
  2. 唯一 start 事件创建 State；
  3. 相关 update 按 `reviewId` 等业务 id 折叠进同一 Context；
  4. 为已注册视图目标产出最终节点数据。
- **实时 append**：每条新事件对每个 Definition 评估一次，只更新匹配的 Context。
- **分页加载更早历史**：保留现有 Context 与节点身份，只匹配新 prepend 的事件；
  前驱或 Location 事实变化的 Context 重放。完整重建只在打开/重同步/缺口修复时发生。
- **窗口外只有 update**：Assembler 保留 pending Context，等更早分页补齐 start 才构造
  State（所以 Definition 规则：每条事件必须携带稳定业务 id，禁止"猜最近的未完成 Context"）。
- **checkpoint 优先**：生产方能低成本发 whole-value checkpoint 就优先发
  （start 在窗口外也能直接用）；增量 delta 必须按 seq 升序回放确定性重放。

### 2.3 渲染层（React 皮）

- `client-web-react` 把 runtime 的 observable 包装成 hooks（`useSessions`、
  `useProjection` 等）；React 只在渲染边界出现。
- ChatView = 有序节点流。流式尾部隔离（streaming tail isolation）、turn 状态条、
  Think 行默认折叠+首行摘要跟随、compaction 折叠为一行 checkpoint、模型重试多节点
  投影为一条稳定状态行。
- 详情面板：`conversation.details.tool` slot 展示工具原始 input/output/timing。
- 本地化：`ui-slots/renderer.ts` 的 LocaleFace——locale revision 变化时重新派发
  `t` 函数引用，memo 组件自然重渲染。

### 2.4 投影（Projection，服务端算好的成品状态）

`session-projection` seam：领域 host 插件注册 `ProjectionDefinition`
（`init/apply/view` 三个纯同步函数 + schema 校验 + stateVersion），框架统一订阅
`session/event` 折叠，客户端**从不折叠领域事件**，直接收到成品全量值：

- 载体：历史尾页 + `session/projection` 推送帧，last-wins；
- 例子：`todos`（todo plan）、`sessionStats`（全轮/步数、LLM 耗时、TTFT、解码
  token、工具耗时——折叠语义见 session-stats README）、`permissions`；
- 全量值规则是承重结构：携带状态的日志事件**永远携带完整状态而非增量**；
- `stateVersion` 版本号：折叠语义变了就 bump，旧持久化缓存行直接丢弃不前推。

## 3. 回显层：重开会话如何原样重建

回显没有专用代码路径——**和实时消费走同一个 Assembler**：

1. 打开会话 → Host 读该会话的 JSONL 日志（`session-persistence-jsonl`）；
2. 校验：首行 SessionHeader、seq 连续（`events[i].seq === i`）、事件 type 必须在
   `KNOWN_SESSION_EVENT_TYPES` 内（未知且无 `ignorable` 标记 → 拒绝重建，宁可报错
   不静默丢事件）；
3. 事件从尾部窗口开始喂给前端 Assembler（长会话从尾部分页往回加载，Trajectory 视图
   同款虚拟滚动 + prepend 分页）；
4. 每个注册的 Definition 按同规则折叠 → 组件 State 重建 → renderer 渲染；
5. 崩溃恢复：不完整尾帧截断 + 合成收尾事件（`interruptedTurnClosers`）；
   有 call 无 result 的工具恢复为 `TOOL_OUTCOME_UNKNOWN`。

这就是"回放即重渲染"：`presentCall/presentResult` 是纯函数、事件族按 seq 确定性
折叠、组件不持有跨刷新状态——三个纪律合起来保证实时流和日志回放产出完全相同的 UI。

## 4. 对自研系统的移植清单

| dsh 机制 | 自研等价物 |
|---|---|
| slot + keyed renderer + fallback | 组件注册表（kind→lazy React 组件），未知 kind 降级通用卡片 |
| ConversationNodeDefinition（事件族→State） | 每个业务组件写一个纯 reducer：`(state, event) => state`，事件带业务 id |
| runtime 配对 tool/call↔result | 后端 SSE 下发前配好对，前端零配对 |
| whole-value checkpoint 优先 | 大表单事件带完整快照而非逐字段 delta |
| Projection（服务端成品状态） | 后端维护 todo/统计等投影表，前端 last-wins |
| KNOWN_EVENT_TYPES + ignorable 拒绝语义 | 事件 kind 注册表 + 启动校验 |
| presentCall/presentResult 纯函数 | 组件 props 必须可从 (args, result) 纯推导，禁止读前端环境 |

## 附：源码补证（v2 复核新增）

### 组装器真实实现（packages/client/runtime/src/client/sessions/conversation-assembler.ts，808 行）

- **分派循环**（:360-385 `dispatchInput`）：对每个注册 Definition 调 `definition.match(event)`；
  fallback Definition **只在没有任何普通 Definition 声明过该 target 时参与**（:376-383）
  ——fallback 按 target 粒度让位，不是全局让位。
- **acceptMatch 三条硬不变量**（:387-447）：
  - 重复 start → 抛 "more than one start Match"（:395-396）；
  - `previous.seq >= input.seq` → 抛 "non-appended Match"（:421-422）——匹配必须按 seq 递增；
  - start 前出现 update → 抛 "update before its start"（:424-425）。
- **状态推进**（:437-444）：start 到达整 Context **replay**；update 增量走
  `definition.update()`，返回值经 `requireState` 校验非空（:441）——update 不能折叠丢状态。
- **prepend 分页**（:223-254）：老事件分页进来时先 `locationIndex.rebuild`；未匹配的
  update 挂 `pending: Map<contextKey, PendingMatch[]>`（:342-357），start 补齐后
  `applyPendingMatches` 批量兑现——README 所述 pending Context 的真实数据结构。
- **发布时机插件可定制**（:356、:446）：`definition.publication?.(match) ?? 'immediate'`。

### Definition 注册表（conversation/event-registry.ts，67 行全文）

- `register()`（:19-27）：kind 重名抛 "already registered"（fail-loud）；
- `registerFallback()`（:34-50）：fallback 全局仅一个，重复抛错；注册是 Cordis effect；
- `assertDefinitionTarget`（:61-66）：`target` 与 `buildViewNode` 必须成对——
  没有渲染器的 Definition 注册时即拒绝。
