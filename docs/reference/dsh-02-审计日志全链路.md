# dsh 执行审计日志全链路：字段设计、回显、交付与派生信息

> 基于 deepseek-harness 源码（0.1.0-rc.5）。
> 核心结论：dsh 没有独立的"审计日志系统"——**会话事件日志（session event log）就是审计日志**。
> 运行时发生的每个事实（含每条 token 流、每次工具调用、每次审批、每次 compaction）
> 以 append-only 事件追加；审计、回显、模型上下文、遥测、统计全部是这份日志的下游投影。

## 1. 存储：一份日志，三层消费

### 1.1 物理格式（`packages/session/session-persistence-jsonl`）

```
<root>/--cwd编码--/<sessionId>/session.jsonl.zstd
```

- 首行 `SessionHeader`：`{type:'session', version, id, cwd?, createdAt,
  parentSession?, seedLength?, origin?, delegationDepth, agentPreset?}`（不可变）。
  `agentPreset` 也持久化——因为 resume 的会话必须用产生这段历史的同一套工具组合回放。
- 之后每行一个事件；`seq` 跨全日志连续（`events[i].seq === i` 硬校验，append 拒绝不连续批次）。
- 优化不丢信息：≥3 连续同块 `assistant/chunk` 合并为 packed row（`text-chunks` 等，
  记 `seq0/time0 + 每成员 dt`，实测省 ~60%），默认 zstd 压缩（独立 checksummed 帧）。
- 写路径：内存队列 + 200ms 固定合并窗口批量 append，每批 fsync；
  crash 时不完整尾帧截断并**合成收尾事件**（工具/step/turn 的 synthetic closers）。

### 1.2 事件信封（`packages/core/session/src/types.ts:404`）

```ts
type SessionEvent = {
  type: K            // 事件类型（见下表）
  seq: number        // 会话内单调递增序号
  time: number       // Unix epoch 毫秒
  data: SessionEventMap[K]   // 强类型 payload（discriminated union，switch(type) 可收窄）
  ignorable?: true   // 未知 type 且无此标记 → 读取方必须拒绝重建（fail-closed）
  // 仅 surface 事件（user/message, assistant/message, tool/result）额外携带：
  sourceEventSeqs?: number[]  // 引用的更早事件（如 assistant/message 引用构建它的 chunk seqs；
                              //   compaction 替换节点引用被遮蔽的节点）
  surfaceOp?: SurfaceOp       // 该事件如何进入模型可见面
}
```

设计要点：payload 是**判别联合**，编译期保证字段与类型匹配；`ignorable` 的缺省语义是
"必需"——忘了标记只会导致过度拒绝（不变），不会静默丢事件（会错）。

### 1.3 完整事件词表（`known-event-types.ts`，由脚本从源码生成）

**流程边界**：`turn/start {turn}`、`turn/end {turn, reason: completed|aborted|blocked|error…}`、
`step/start {turn, step}`、`step/end {turn, step}`（一个 step = 一次模型请求 + 它调的工具）。

**消息面**：`user/message`、`assistant/chunk`（逐 token 增量，永不丢）、
`assistant/message`（聚合结果，带 sourceEventSeqs 指回 chunks）、
`tool/call`、`tool/result`、`tool/code-dispatch(-start)`（run_code 内子调用，log-only 不进模型上下文）。

**交互与策略**：`approval/asked|decided|policy`、`agent-preset/selected`、
`agent/inbox/spliced`、`permissionPresets/preset`、`sandbox/mode`。

**会话元数据**：`session/title`、`session/title-llm-request`、`session/end-seed`、
`compaction/summary`、`feedback/record`、`user-questions/*`。

**业务事件族**：仓库外插件通过 declaration merging 扩展（如 cookbook 的
`review/start|progress|end`）。

**surface 三分类**（`session-query` 的 `foldSurface()`）：`current`（仍在模型上下文）、
`shadowed`（被 compaction 替换）、`log-only`（纯日志，模型不可见）。
`deriveMessages()` 只投影 current —— 这是"模型可见 = 可从日志重建"的运行时不变量。

## 2. 回显：日志 → 三种 UI 投影

同一份日志喂三个渲染目标（详见《UI 全链路》文档）：

1. **聊天流**：ConversationNodeAssembler 按事件族折叠 → 节点流（实时与回放同路径）；
2. **Trajectory 流程台账**（`ui-trajectory`）：turn/step 感知的事件 ledger——粗线分
   Turn、内联标 Step、每条 User/Assistant/Tool/子工具记录，选中开 inspector 看
   token 用量/耗时/input/output/timing；顶部时间轴 Overview 从记录的真实
   start/duration 投影，可拖选区间过滤、滚轮缩放；尾部定位 + 向上分页 + 虚拟滚动；
   进行中记录的时间留空不编造（"without fabricating duration"）；
3. **统计条**（`session-stats` 投影）：全日志口径的 turns/steps、llmMs、ttftMs/decodeMs/
   decodeTokens、toolMs（`tool/call→tool/result` 按 callId 配对求和；未配对的在
   turn/end 丢弃）。注意是**日志口径不是当前上下文口径**——被 compaction 掉的 step 仍计数。

## 3. 交付：日志如何离开进程

### 3.1 会话内交付（Host → 浏览器）

- WebSocket mux 流推 `host/remote-event` 帧 + `session/projection` 成品投影帧；
- 历史尾页（history tail page）携带投影初值；断线重连以 baseline 帧恢复；
- 断线期间 agent 不终止（StreamBridge 模式在宿主侧同样成立）。

### 3.2 遥测交付（`session-telemetry` + `session-telemetry-otel`）

**Seam 设计**：`SessionTelemetrySink` 三成员——`emit()`（必须非阻塞，同步跑在
事件流上）、可选 `flush()`（turn 结束后的 hint）、`shutdown()`（排空并等待 SDK 停止）。
批处理/重试/丢失策略全部归后端 SDK，seam 不包装。

**捕获点**：`session/created`（记录 header + 回读构造边界前日志）、`session/event`
（投影→深拷贝→脱敏→emit，零 IO）、`session/flush`、`session/disposed`（终止边缘的
shutdown 运行记录）、`agent/error`（唯一的活动错误中继——事件词表故意没有运行错误记录）。

**脱敏瀑布**（`sessionTelemetry/record` waterfall）：每条记录发出前过一遍监听器栈，
默认无规则=原样通过；抛错的监听器使**该条记录 fail-closed 扣留**；只影响外发副本，
**规范日志永不改写**。

**交付游标**：模块级 `WeakMap<Session, seq>` 记录已交接（非已送达）的最高 seq；
at-most-once 语义——resume 不回补上个进程没送达的记录（需要补投就要 outbox，是
刻意推迟的设计）。接收端按 `(session.id, event.seq)` 去重。

**共享披露（隐私设计）**：backend 必须声明 `sharing: full | feedback-only | disabled`，
`/feedback` 确认界面展示。"full" 也只承诺交接（enqueue），不承诺送达。

**三种模式**：`FULL`（每条即发）、`FEEDBACK_ONLY`（只在收到 `feedback/record` 事件时
才回放该事件之前的日志后缀——**以日志里的事件为同意凭据**，内存里的 bus 值不算）、
`DISABLED`（连 SDK 管道都不构造，fail-closed）。

**OTel 映射**：OTel JS SDK LoggerProvider → BatchLogRecordProcessor → OTLP/HTTP；
ledger 记录与 operational 记录两个 instrumentation scope；资源标识
`service.name/version` + 匿名 `user.id`（`$DSH_HOME/.anonymous-user-id`，删除文件即重置），
**每批次携带一次**而非每条携带。

### 3.3 会话查询与导出

- `session-query`：跨会话语料库查询（live 优先于持久化源），SQLite 全文索引后端
  （只索引 session id 和 cwd，绝不索引正文）；
- `session-log-export`：日志导出；
- fork：`ctx.sessions.fork(source, boundary?)` —— 子会话 header 记 `parentSession`
  和 `seedLength`，遥测接收端据此拼接（父流中已导出的前缀不重发）。

## 4. 派生与提升信息（日志之上的"增值层"）

日志是事实，以下是框架从日志**推导**并提升为会话级信息的机制：

| 派生物 | 机制 | 关键设计 |
|---|---|---|
| **会话标题** | `session-title-*`（first-prompt-llm / all-prompts-llm / llm 三种策略插件） | 标题生成请求本身也记 `session/title-llm-request` 事件——审计自身也被审计 |
| **统计** | `sessionStats` 投影 | 全日志口径；stateVersion bump 使旧缓存失效而非错误前推 |
| **上下文压缩** | `compaction-basic`：`compaction/summary` 事件 + 被 shadowed 的节点记 sourceEventSeqs | UI 折叠为一行 checkpoint（显示替换条数与估算 token，点击展开摘要）；模型上下文变小但日志不删 |
| **待交互状态** | `SessionSummary.pendingInteraction`（approval/plan-review/question 三分类） | 在 Session 对象实例化之前就按稳定请求 id 缓冲跟踪，会话列表即显示"等待审批" |
| **队列快照** | `agent/inbox/spliced` 持久事件 → Host 派生 `session/queue` 全量快照 | 客户端不做乐观变更，Host 快照是唯一可见提交 |
| **空白会话位** | `SessionSummary.blank`（空日志推导位） | 只会降不会升：首个 ACCEPTED prompt / running 帧翻转 false，拒绝的首条消息保持空白可复用 |
| **token/耗时** | `assistant/chunk` + usage + step 边界折叠 | TTFT = step/start→首个非空 delta；解码时长 = 首 token→聚合消息 |

## 5. 可靠性语义汇总（审计视角的硬保证）

1. **append-only**： flushed 事件永不改写；遥测脱敏只改外发副本。
2. **seq 连续性**：写入和读取双侧校验；遥测去重键就是 `(session.id, event.seq)`。
3. **fail-closed 词汇表**：未知事件类型无 `ignorable` → 拒绝重建整个会话。
4. **崩溃合成**：截断 + synthetic closers，让每个 turn/step/tool 在恢复后有终态。
5. **TOOL_OUTCOME_UNKNOWN**：有调用无结果时给模型的恢复语义（只重试幂等操作）。
6. **不可变 header + preset 锁定**：resume 必须用产生历史的同一组合，防止"回放一个
   模型无法继续的历史"。
7. **同意即日志**：feedback-only 遥测以日志内 `feedback/record` 事件为唯一同意凭据。

## 6. 对自研系统的移植清单

| dsh 设计 | 自研（Python/FastAPI + PG）等价 |
|---|---|
| JSONL + seq + 判别联合 | `session_events(seq PK, session_id, type, time, data JSONB)`；pydantic 判别联合校验 type↔payload |
| KNOWN_EVENT_TYPES 拒绝语义 | 事件类型注册表 + 未知类型(无 ignorable)加载报错 |
| packed chunk rows | PG 场景可简化：chunk 事件按批合并写，或只存聚合 message + 可选 chunk |
| 合成收尾事件 | 启动恢复扫描：RUNNING 状态 trace 的未闭合 step/turn 补终态 |
| session-telemetry seam | 独立 telemetry 插件订阅事件总线；emit 非阻塞 + WeakMap 游标 + (session,seq) 去重 |
| feedback-only 同意语义 | 用户反馈事件落库后才导出该前缀——合规友好的关键设计 |
| sessionStats 投影 | 服务端物化视图/投影表，stateVersion 兼容字段 |
| compaction + sourceEventSeqs | 压缩摘要事件引用被替换事件 seq；UI 折叠行 + 模型上下文用摘要 |
| audit 自身被审计（title-llm-request） | 元数据生成类 LLM 调用也记事件（token 计费/审计一致性） |

## 附：源码补证（v2 复核新增，packages/core/session/src/index.ts，1200+ 行）

- **deriveMessages 的缓存实现**（index.ts:701-734）：派生历史缓存三元组
  `derived / derivedNodes / derivedGeneration`——每个 surface 节点**只投影一次**，
  之后每次调用 O(新节点)；注释明确 surface 机制："every message-producing append records
  its `surfaceOp`, so a raw event with no marker (a chunk, a turn boundary) is correctly
  absent, and a compaction `replace` deletes the shadowed nodes from the derivation"；
  **replaceGeneration 前进即整体重建缓存**（:730-733）。返回"fresh array of SHARED,
  deep-frozen Messages"（:719-723）——复用已冻结的持久事件数据，零二次深拷贝。
- **requestHeader 增量折叠**（index.ts:655-680）：请求头折叠缓存 + `deepFreeze`，注释：
  "a consumer mutating it in place…would desync every later comparison against the log,
  **so mutation throws instead**"——共享快照靠冻结防篡改。
- **种子连续性校验**（index.ts:512-526）：fork/resume 种子事件要求
  `snapshot.seq === index`（"seed must be contiguous from 0"），否则抛错——
  种子注入也受 seq 连续性约束，不只是 append。
- **fork 边界校验**（index.ts:1124）：fork 的 boundary 参数必须命中一个真实的
  contiguous event seq，否则拒绝。
