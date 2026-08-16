# dsh 链路 ⑩+⑪+⑮：定时（schedule）、目标（goal）、子智能体（subagent）（源码精读 v2）

> 本版全部结论直接引用 `src/*.ts` 源码行号；README 仅作对照。
> 源码：`packages/schedule/schedule/src`（2003 行）、`packages/goal/goal/src`（1288 行）、
> `packages/subagent/subagent/src`（4531 行）。共同骨架：**会话事件日志是唯一持久权威，
> 定时器/激活/运行对象都只是日志的进程内投影**。

# Part A 定时（schedule）

## A1. 运行时：定时器是投影（src/runtime.ts，324 行）

- **文件头注释**（runtime.ts:2）："Disposable **live timer projection** for one exact root
  agent"——运行时对象自我定位为"投影"。
- **分段定时器 + 重读墙钟**（runtime.ts:21、177-184）：
  ```ts
  arm(target, now) {
    const delay = Math.min(target - now, MAX_TIMER_DELAY_MS)   // 拆分到 Node 定时器上限内
    this.timer = setTimeout(() => { this.timer = undefined; this.requestDrive() }, delay)
  }
  ```
  注释："**every wake rechecks the wall clock**"——唤醒后 `driveOnce` 里重新
  `const wakeNow = Date.now()`（runtime.ts:246）再决策，不信任 setTimeout 的到期时刻
  （时钟回拨不早发、前跳变 overdue）。
- **每次驱动先 flush**（runtime.ts:231-241）：`driveOnce` 第一步
  `await flushSchedulePersistence(...)`，失败仅 warn 并 return——持久化不确定时
  **不派发也不定 arm**，等下次驱动重试。
- **读侧防污染**（runtime.ts:205-218 `readFolded`）：fold 抛错 → `this.faulted = true`
  + warn + undefined——**损坏的日志只熔断本运行时**，不传播异常；
  `decide()`（runtime.ts:220-228）对墙钟决策错误同样"contain without permanently
  faulting"。
- **派发走 maintenance 相位**（runtime.ts:254-259）：到期派发在
  `agent.runMaintenance(...)` 内再次 `readFolded` 重新认领——认领时刻的最新日志才是
  派发依据，不是 arm 时的快照。

## A2. 工具侧：persistence_uncertain 三态（src/tools.ts，467 行）

- **专用错误形状**（tools.ts:105、203-210）：
  ```ts
  code: { type: 'string', required: true, const: 'persistence_uncertain' }
  // message: 'Schedule persistence is uncertain; retry with schedule_list before
  //           relying on this result.'
  ```
  ——不确定 ≠ 失败 ≠ 成功，给模型的指引是"先 schedule_list 再依赖结果"。
- **读操作也 flush**（tools.ts:245）：列表/删除预检前 `await flushSchedulePersistence(...)`；
- **barrier 不可逆**（tools.ts:307）：注释 "A projection observer cannot reverse a
  completed durability barrier"——完成的持久屏障之后的观察者回滚无法翻案；
- **成功预检触发 owner 重算**（tools.ts:296）："Called after every successful preflight
  **and again after** a create or actual delete barrier succeeds"——用重算代替
  Schedule 专属重试定时器。

## A3. 领域层（src/domain.ts，807 行）

- fold 从 `session.events.slice(header.seedLength ?? 0)` 开始（runtime.ts:208-211 传入）
  ——**fork 不继承父会话提醒**由 seedLength 切片实现；
- 回放拒绝清单（domain.ts 内 ScheduleLogError 路径）：未知版本、多余字段、复用 id、
  非法转移（README 对照，invariant.ts 同策略应用于**存量日志与候选事件**）。

# Part B 目标（goal）

## B1. 进程内缓存 + 激活分离（src/index.ts:125-133）

```ts
interface GoalCache {
  readonly state: GoalFoldState        // 折叠出的持久状态
  activation: GoalActivation           // 进程本地激活
  observedSeq: number
  pendingActivation: { seq, activation } | undefined   // 跨同步 append 边界的激活意图
}
```

- 缓存按 `WeakMap<Session, GoalCache>` 键控（index.ts:191）——会话对象生命周期即缓存
  生命周期，无泄漏；
- **session-start 即解除激活**（index.ts:198-200）：
  ```ts
  ctx.on('agent/session-start', ({ agent }) => {
    this.cache(agent.session).activation = 'disarmed'
  })
  ```
  ——代码层面落实"激活永不持久"：任何会话启动边缘都先 disarmed，重放发现 active 持久
  相位也不自动续跑；
- `disarm()`（index.ts:236-242）：只改 `cache.activation = 'disarmed'`，**不写 revision、
  不发变更事件**——注释："Remove process-local continuation authority **without changing
  durable goal phase or revision**; a later **human-authorized resume** records the new
  activation edge"。

## B2. 域校验的严格性（src/index.ts:141-180）

- `resolveMaxGoalRounds`（141-147）：非安全正整数抛 `GOAL_INVALID_MAX_ROUNDS`；
- `resolveObjective`（150-155）：trim 后非空；
- `resolveBlockReason`（166-180）：code 必须 `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`
  （lower-kebab-case），message 非空——**block 理由是强类型契约**，policy 拥有的
  机器可读 code + 人读 message。

## B3. 投影单元复用（src/index.ts:204-213）

goal 同时注册为 `sessionProjections` 的 `goal` 投影（init: null / apply: fold /
stateVersion: 4）——前端经标准投影载体拿 goal 状态，注释强调 "headless assemblies
stay unaffected"（没组装投影注册表就不注册）。

# Part C 子智能体（subagent）

## C1. 注册即生成器事务（src/index.ts:370-385）

```ts
this.providers.set(name, provider)
yield () => { this.providers.delete(name); this.emitLifecycle('subagent/provider-removed', name) }
this.ctx.emit('subagent/provider-added', provider)
```
注释（index.ts:381-382）："A throwing added-listener **unwinds the yielded rollback**"——
added 事件抛错会回滚注册本身（fail-loud 语义的一致性）。

## C2. start()：能力检查在委派之前（src/index.ts:414-426）

```ts
const provider = this.expectProvider(name)
this.assertCapabilities(provider, request)
assertSubagentMaxDepth(request.maxDepth)                 // 深度护栏
if (request.outputSchema !== undefined) assertObjectJsonSchema(...)  // 结构化输出 schema 校验
const descriptor = snapshotSubagentDescriptor({ mode: 'one-shot', provider: name, ... })
return observeRun(this.emitLifecycle, name, request.parent, await provider.start(resolved))
```
- one-shot 描述符在**委派前快照**（descriptor 持久化，供列表/审计）；
- 注释（405-410）："Provider ownership lasts until its promise fulfills; **a rejection
  therefore has no run for the caller to dispose and emits no run lifecycle events**.
  Post-publication failures settle **through the returned run**"——发布前失败=无副作用，
  发布后失败=经 run 对象结算（两阶段失败语义）。

## C3. prepareContinuable：方法存在即能力（src/index.ts:433-446）

```ts
if (provider.prepareContinuable === undefined) {
  throw new SubagentError(`...does not support continuable children
   (no prepareContinuable capability)`, 'UNSUPPORTED_CAPABILITY')
}
return provider.prepareContinuable(request)
```
注释（428-431）："Method presence on the provider **IS the capability**, so a provider
without it is rejected **before the manager reserves any child resources**"——拒绝发生在
资源预留之前。spec 只携带数据（seed），身份/组装/投递/冷恢复全归 continuation manager
（continuation.ts 1483 行拥有这套编排，README 对照）。

# 落地映射（源码级）

| dsh 源码设计 | 落地（我们） |
|---|---|
| `Math.min(delay, MAX)` 分段定时（runtime.ts:179） | asyncio 定时循环按上限分段 sleep |
| 唤醒后 `Date.now()` 重读（runtime.ts:246） | 每轮 `SELECT now()` 或 time.time() 重取，不信 sleep 精度 |
| driveOnce 先 flush（runtime.ts:235） | 定时 worker 每轮先确认事件已落库再决策 |
| fold 损坏仅熔断本运行时（runtime.ts:213） | fold 异常→标记 faulted+log，不崩 worker |
| 派发时二次 readFolded（runtime.ts:258） | 到期派发前重读数据库最新态 |
| persistence_uncertain 常量 code + 指引（tools.ts:105,210） | 持久不确定时返回专用 code+"先 list 再依赖" |
| barrier 完成不可逆（tools.ts:307） | 事务提交后观察者不能翻案 |
| seedLength 切片不继承（runtime.ts:210） | fork 会话的任务/提醒从 seed 之后折叠 |
| activation 与持久状态分离（goal index.ts:128-133） | 任务"当前目标"表 + 进程内 can_continue 标志两栏 |
| session-start 先 disarmed（goal index.ts:198） | resume 后需显式操作才续跑 |
| block reason kebab-case 正则（goal index.ts:172） | blocked_reason(code, message) 契约校验 |
| 注册生成器 + added 抛错回滚（subagent index.ts:381） | 插件注册用 try/finally+事件，事件失败回滚注册 |
| 能力检查先于资源预留（subagent index.ts:438） | 任务类型不支持时在入队前拒绝 |
| 发布前/后两阶段失败（subagent index.ts:405-410） | 一次性任务：入队失败无痕迹；入队后失败经任务对象结算 |
