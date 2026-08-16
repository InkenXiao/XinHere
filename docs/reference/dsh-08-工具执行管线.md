# dsh 链路 ⑥：工具执行管线——三瀑布 + 审批 + 取消融合（源码精读）

> 源码：`packages/core/tools/src/index.ts`（执行管线集中在 1459-1790 行）。
> 这是模型调用任何工具（含我们的财务组件工具）必经的完整管线。

## 1. 管线全貌（prepareExecution → dispatch → finalize，源码级顺序）

```
prepareExecution (index.ts:1463)
  ① createExecution(input)                     → ready 或预检失败
  ② callerCancelled? → ABORTED_BEFORE_DISPATCH
  ③ waterfall 'tools/pre-execute'              → allow | deny | ask（默认 allow）
       ask → serviceAsk → 审批 seam（index.ts:1689）
  ④ guardReason(exec)                          → 静态 guard 拒绝（deny 之外的第二道闸）
  ⑤ 每一步之间重复检查 callerCancelled
  → 进入 dispatch
dispatchScheduledExecution (index.ts:1569)
  ⑥ waterfall 'tools/execute'                  → around 包装（超时/重试/指标），最内层:
       dispatchToolBody (index.ts:1532)
         fuseToolSignals(callerSignal, wrapperSignal)   ← 信号融合
         resolveExecution(name, agent, parent)          → 作用域解析工具
         state.bodyInvoked = true
         returned = tool.execute(args, exec)
         isAborted && 已启动 → toolAbortedResult(result)（保留 prior 结果）
  ⑦ normalizeDispatchResult + 合并 deferredContexts
finalizeScheduledExecution (index.ts:1609)
  ⑧ postExecute → waterfall 'tools/post-execute'（accept/revalue/block，index.ts:1742）
  ⑨ callerCancelled && 非 error → cancellationResult（按 bodyInvoked 选结果形态）
  ⑩ finishScheduledExecution: materialize → applyFinalContent(工具自带的
     finalizeContent 快照回调) → 再次 materialize → notifyResult
  ⑪ notifyResult (index.ts:1664): Object.freeze(exec) → emit 'tools/result'
     （observer 异常只 warn，绝不影响结果通道）
```

## 2. 关键源码细节（每条都是可抄的工程决策）

### 2.1 pre-execute 的 ask 分流与"三态拒绝可区分"（index.ts:1689 serviceAsk 注释）

"the three non-grants deny with **distinct reasons** so the model can tell a human 'no'
from an absent approval channel"——拒绝、用户拒绝、无审批通道是三种不同 reason，
模型能区分"人说不行"和"系统没接审批"。审批 seam 是机会性消费（`ctx.get('approval')`）：
没组装审批插件就历史性降级为 deny。

### 2.2 取消语义：信号融合 + 永不弃单（index.ts:1527-1560）

- around 包装者可能替换 `exec.signal`，registry 在 body 前**把原始 caller 信号熔回去**
  （`fuseToolSignals`），"replacement cannot detach caller cancellation"；
- "Cancellation never abandons the body: a started promise reaches quiescence before its
  outcome becomes ABORTED"——启动了的 body 一定跑到静默，结果再标 ABORTED；
- 结果形态按 `bodyInvoked` 区分：未启动 = `ABORTED_BEFORE_DISPATCH`（干净），已启动 =
  `ABORTED`（可能带 partial prior 结果）。

### 2.3 post-execute 的三种改写与防呆（index.ts:1742-1790）

- `accept`（可替换 content 或追加 additionalContexts）、`revalue`（替换 value，**会重新
  走工具的 output schema 校验** `createSuccessResult`）、`block`（整体转 isError）；
- 两个编译期防呆：不能同时替换 value 和 content（TypeError）；"cannot replace the value
  of a failed result"——失败结果不允许被改成成功值。

### 2.4 finalizeContent：工具自带的最后一英里（index.ts:1646-1654）

执行开始时**快照**工具的 `finalizeContent` 回调，对每个规范化结果（包括绕过 post-execute
的管线失败）恰好调用一次；回调必须全函数不抛错（抛错被 materialize 兜底转 error result）。
用途：工具想把失败/管线错误统一改写成自己的文案。

### 2.5 canonicalResults 弱表（index.ts:1791-1796）

只有 registry 规范化的结果带 `token` 标记——下游（如日志层）能区分"registry 认可的结果"
和"裸 dict"，防止外部伪造结果进入持久层。

### 2.6 观察者隔离（index.ts:1664 notifyResult）

`tools/result` 是 emit 型：freeze(exec) 后分发，observer 同步抛错和异步 rejection 都只
warn（"without exposing a mutation or error channel into the outcome"）——观察永远不能
影响执行结果。

## 3. 与执行循环的衔接（链路⑤已述，此处补充）

- 管线被 `tool-calls.ts` 的调度器按模型序调用（prepare/dispatch/finalize 分阶段对应
  调度器的启动/完成槽位）；`needsPost` 标志决定走完整 finalize 还是快速 finish；
- `ToolExecutionResult.concludesTurn` / `additionalContexts` 从这里产生，回到 agent-loop
  决定 step 结束与 next-step 注入。

## 4. 对我们的落地映射（agent_harness 工具层）

| dsh 设计 | 落地（Python） |
|---|---|
| pre-execute 瀑布 + 审批分流 | LangChain 工具外包装一层 middleware：权限/预算预检 → ask 走审批（复用现有 approval 思路），三种拒绝 reason 可区分 |
| guard 静态第二道闸 | 域级 guard（如"财务写操作需二级确认"）独立于工具定义声明 |
| 信号融合 + bodyInvoked 二态取消 | asyncio.Task 取消：未启动的调用直接 ABORTED，已启动的等静默再标 ABORTED（不裸 cancel） |
| post-execute revalue 重新校验 | 结果替换值必须过 pydantic output schema 再下发 |
| finalizeContent 快照 + 全函数 | 工具可注册结果改写器；失败也要有结构化文案而非裸异常 |
| canonical token 弱表等价 | SSE 下发的 tool/result 带 registry 签发的事件 seq，前端不认无 seq 的结果 |
| 观察者隔离 | 事件订阅回调异常一律 log 不影响主流程（我们 event_hooks 已是此语义，保持） |
