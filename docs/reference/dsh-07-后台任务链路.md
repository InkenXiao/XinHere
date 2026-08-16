# dsh 链路 ⑨：后台任务（jobs）——派发任务功能的直接原型（源码精读）

> 源码：`packages/jobs/jobs`（契约）+ `jobs-local`（进程内实现）+ `tool-jobs`（模型面控制器）
> + `docs/subsystems/jobs.md`（类型目录）。
> 对应我们需求："填报后基于填报内容派发任务"——派发出去的任务就是这个 Job。

## 1. 三角色架构（又一个 seam）

| 角色 | 包 | 职责 |
|---|---|---|
| Service Definition | `dsh-jobs`（`ctx.jobs`） | 抽象 `JobRegistry` 契约：id、owner 隔离、读、取消、等待、通知、清理 |
| Provider | `dsh-jobs-local` | 进程内实现（`LocalJobRegistry extends JobRegistry`） |
| Consumer | `dsh-tool-jobs` | 三个模型面工具 + 完成通知 + prompt section |
| Producer | 业务插件（bash、subagent…） | 声明 `JobStart`，扩展 `JobKindMap`（declaration merging） |

契约明确"运行时拥有身份、访问与生命周期状态；生产者拥有执行资源"
（"the runtime owns identity, access, and lifecycle state"）。

## 2. 身份与状态（docs/subsystems/jobs.md）

- `JobId = <kind>-N`（如 `bash-1`），**branded id**；类型目录原话："Access control relies on
  owner authorization, not id secrecy"——访问控制靠 owner 校验，不靠 id 保密（id 可预测）。
- `JobStatus = 'running' | 'stopping' | 'completed' | 'killed' | 'failed'`，五个终态之外
  的事实放 `JobSnapshot.detail`（生产者自有）。
- `JobKindMap` 可扩展（`bash`、`subagent`…），registry 把 kind 当**不透明 id 命名空间**。

## 3. Service 契约要点（dsh-jobs/README.md，逐条源码背书）

- **start 原子性**："A preflight rejection or starter throw leaves no job id or registered
  work; successful return commits without another failable step"——预检（controller、spec、
  精确 owner、`outputLimitBytes`、准入策略）全部通过才调 `run()` 一次；成功后没有可失败的
  第二步。
- **owner 隔离**：`get/list/read/kill/wait` 全部 owner-relative；list 只见"自己拥有的 +
  无主的"。判定是 `SessionId` 精确比对——这就是访问边界（"this fence is the boundary"）。
- **kill 顺序**："invokes producer cancellation **before** changing status. A cancellation
  throw leaves the job running"——先请求生产者取消，成功才置 stopping；取消抛错任务照跑。
- **settlement first-wins**：一条终态记录、一轮受控通知、唤醒全部 waiter；重复结算被拒。
- **双监听**：`onJobDone`（终态+精确 owner，listener 异常被隔离、不阻塞）与
  `onJobsChanged`（可见集变化——注册/每次 stopping/结算/owner 消亡清理/服务排空，
  **owner 粒度**因为"removal is a change no per-job record can express"）。
- **进程内边界**（Known Limitations）："The contract is in-process"——跨进程后端要重塑
  身份/重启/所有权语义才能实现这个 seam。

## 4. 模型面：三个工具 + 通知经济学（tool-jobs/README.md）

- 工具：`job_output(job_id, wait?, timeout_ms?)`（默认非阻塞读增量）、`job_list()`、
  `job_kill(job_id, reason?)`。公共快照**刻意省略** `ownerSession` 和内部 `reported` 位。
- **完成通知的双通道选择**（最精华）：
  - owner 忙 → **注入 next-step inbox**，"the turn cannot close while that inbox holds it,
    so several jobs settling together cost one step rather than one turn each"——多个任务
    同时完成只花一个 step；
  - owner 闲 → **follow-up 唤醒**，因为"悬空通知 = 模型永远不知道的完成"；
  - `completionDelivery: quiet` 强制注入通道（确定性 transcript 需要）。
- **唤醒预算防自激**："Each owner may open `maxConsecutiveWakes` turns this way before
  further notices degrade to injection"——唤醒链是自激的（被唤醒的 turn 可能又启动后台任务），
  连续唤醒耗尽预算后退化为注入；认领任何用户消息恢复预算；自己排队通知不回填自己花掉的预算。
- **字节预算的通知裁剪**：有界通知中"stable id prefix 和 job_output 收集指令的优先级高于
  变长 label/detail"——保证 64 字节最小值下通知仍可行动。

## 5. 对我们的落地映射（派发任务功能）

| dsh 设计 | 落地（FastAPI + agent_harness） |
|---|---|
| seam 三角色 + kind 命名空间 | `dispatch_task` 工具 = Consumer；任务注册表 = `ctx.jobs` 等价物；业务任务类型 = kind |
| owner = SessionId 精确匹配 | 任务表记 `owner_session_id`，模型只能查/杀自己会话的任务（防跨会话干扰） |
| start 预检原子性 | 先校验（权限、参数、额度）再入队；入队本身一次事务 |
| kill 先请求后置位 | 取消先通知执行方，确认后才改状态；失败保持 running |
| 忙注入/闲唤醒双通道 | 任务完成通知：会话运行中→注入下轮上下文（多个完成合并一个 step）；空闲→触发新 turn |
| 唤醒预算防自激 | 连续自动唤醒 N 次后退化为仅注入，用户消息恢复预算 |
| 通知字节预算 | 完成通知固定头（task_id + 查看指令）优先，变长信息截断 |
| first-wins settlement | 结果表唯一终态约束；重复完成写被拒 |
| 进程内边界认知 | 我们如果任务跨进程（Celery/队列），owner 身份要重塑成 (session_id, worker epoch) |

## 附：源码补证（v2 复核新增，jobs-local/src/index.ts，534 行）

- **内部 Job 结构**（index.ts:55-61）：`settled: Promise<void>` + `markSettled` resolver
  ——"called by the **first** effective settlement"（first-wins 在数据结构层面实现：
  resolver 只能 resolve 一次）；`waiters` 计数器 + 可移除 resolvers。
- **每 owner 并发上限**（index.ts:33、146）：`maxConcurrentJobsPerOwner`（running +
  stopping 计数）超限的错误文案直接教模型自救："use job_kill to stop an unneeded job,
  wait for it to finish, then retry"——**限流错误自带恢复指引**。
- **kill 的精确顺序**（index.ts:211-225）：
  ```ts
  if (isTerminal(job.status)) { job.reported = true; return 'already-finished' }
  job.cancel(reason)      // 先取消；抛错则状态与通知位都不动
  job.status = 'stopping'
  job.reported = true     // kill 即视为已报告，抑制冗余完成通知
  ```
  注释："Cancel first so a throw leaves both lifecycle and notice state unchanged"。
- **read 的幂等语义**（index.ts:200-209）：流式 job 调 `readOutput()` 消费唯一游标；
  终态 job 读 terminal output 幂等并置 `reported = true`。
- **wait 的取消时序**（index.ts:236-249）："Abort removes the waiter **synchronously**
  so same-tick settlement cannot suppress a notice for a wait that will reject"——
  waiter 在 tick 内同步摘除，避免"结算和取消同 tick 竞态导致漏通知"；注释还区分
  "successful wait timeout vs caller cancellation"（scoped deadline 计时器每种退出都清）。
