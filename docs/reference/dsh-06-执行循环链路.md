# dsh 链路 ⑤：执行循环（agent-loop）——turn/step 驱动、拦截瀑布、工具调度（源码精读）

> 源码：`packages/core/agent-loop/src/`（agent.ts 496 行 / index.ts 713 行 / tool-calls.ts 289 行）。
> 这是 dsh 的默认 `ctx.agentLoop` 插件——**连循环本身都是可替换的插件**。

## 1. 层次结构：turn → step → LLM 请求 → 工具组

- **turn**（一轮）：打开于认领输入之前，"nothing is owed"时关闭。可能零 step
  （被拒绝/空输入的 turn 也留下 `turn/start`+`turn/end` 记录，"spent no step"）。
- **step**（一步）：一次模型请求 + 它请求的全部工具执行。
- 驱动循环：`kick()` 里 `while (await this.turn()) {}`——turn 返回 true（inbox 还有
  pending）就连开下一轮。源码：agent.ts:210-223。

## 2. turn() 的精确流程（源码：agent.ts:246-330）

```
session.append('turn/start', {turn})                    ← 持久事件
loop:
  decision = preStep(target, {turn, step})               ← agent/pre-step 瀑布
  reject            → turnEnds=blocked, 结束（不发模型请求）
  空消息 && phase.step==0 → turnEnds=completed, 结束      ← "空 turn 也拥有边界但不花模型调用"
  session.append('step/start')                           ← 持久事件
  for msg of decision.messages: session.append('user/message', surfaceOp:'append')
  try:  stepEnd = step(assembly)                         ← 见下节
  finally: session.append('step/end')                    ← finally 保证必有 step/end
  turnEnds = stepEnd（max-tokens 粘滞：后续正常完成不得降级结局，agent.ts:288-290）
  if turnEnds && inbox.nextStep 为空:
      dispatch.serial('agent/turn-stopping')             ← 串行事件，无 next()，可阻止停止
  if turnEnds && nextStep 为空: break
  target = 'next-step'                                   ← 同轮继续下一步
catch: abort → turnEnds=aborted；其他错误结构化（LlmError 保事实，其余 UNKNOWN code）
finally: session.append('turn/end', {turn, reason})      ← 任何路径都闭合
```

关键源码细节：
- **abort 后唤醒自动降级**（agent.ts:122-124）：`wakingAfterAbort` 的输入改投 `next-turn`。
- **turn-stopping 是 serial 而非 waterfall**（agent.ts:296）：可以拦但无 next 委托链。
- **错误全部结构化**：`LlmError` 保留 failure facts；其他错误 `errorChain()` 展平为
  `UNKNOWN` code（agent.ts:309-313）。

## 3. preStep()：模型看到什么在此决定（源码：agent.ts:225-243）

```
claimed = inbox.claim(target, turn)                     ← 认领本 step 输入
assembly = systemPrompt.assemble(...)                   ← 组装 prompt sections + 工具 schema
context = runtimeContext.project(joined sections)       ← 运行时上下文投影
decision = waterfall('agent/pre-step', {messages: claimed, ...}, 
                     默认: enter + context 追加在 claimed 之后)
```

- `agent/pre-step` 是**瀑布**：监听者可改写认领的消息或**直接 reject**；
- reject 或首次 enter 被改写为空 → turn 以 blocked/completed 关闭但**不花一次模型调用**，
  且日志记录了这次尝试（这就是"预算内试错也留痕"）。

## 4. step()：一次模型请求的完整生命周期（源码：agent.ts:332-401）

```
while(true):
  buildRequest()                                         ← agent/request 瀑布可改请求配置
  stream = preparedCall?.stream(request) ?? llm.stream(request)
  for chunk of stream: session.append('assistant/chunk') ← 每个 token 增量落日志
  finish = assembler.finish
  error|aborted → waterfall('agent/request-error', …, 默认 undefined)
                  action≠retry → throw LlmError；=retry → continue（重试同一 step）
  session.append('assistant/message', {message, usage}, 
                 {surfaceOp:'append', sourceEventSeqs: chunkSeqs})  ← 聚合消息引用 chunk seqs
  finish==max-tokens → return {max-tokens}
  无 tool-call 块 → return {completed}
  executeToolCalls(...)                                  ← 见下节
  concluded ? completed : null（null=还有 owed，继续下一步）
```

- **请求头恢复**：`buildRequest`（agent.ts:407-439）从 session 的持久 requestHeader
  恢复 provider/model/reasoningEffort——"A loop instance starts from its declared route,
  restoring only an explicit effort owned by that exact model"（模型换了就不恢复旧参数）。
- `agent/request` 瀑布能改路由/参数；解析不出 provider/model 直接抛错（index.ts:444 引用文案）。

## 5. executeToolCalls()：模型序提交 + 并行滚动池（源码：tool-calls.ts）

调度契约（文件头注释 + runGroup，tool-calls.ts:125-160）：

- **互斥调用是屏障（barrier），并行调用进有界滚动池**（`maxParallelToolCalls` 配置，
  默认值在 constants.ts）；
- **重分类在启动前进行**："Later calls are reclassified before start"——工具注册表变化
  能在流中制造新屏障（tool-calls.ts:200 重读后续 mode）；
- **dispatch 可重叠，但结果按模型序提交**：`committed` 只沿连续模型序 slot 前进
  （`commitReady`，tool-calls.ts:149-164）；`tool/result` 引用该调用的 `tool/call` seq
  （`callSeqs`）；
- **abort 语义**：停止新启动、drain 已启动、**给未启动的调用补合成 error 结果**
  （"Abort records synthetic error results for skipped calls so replay stays valid"），
  已启动调用的 additionalContext 仍进入 next-step inbox；
- **调度器内部失败**：保留已记录的 `tool/call` 事件但**不伪造结果**；
- **参数解析宽容**（tool-calls.ts:105-111）：非法 JSON 原样保留为文本，空输入映射 `{}`——
  交给工具自身的 schema 校验报错，不在调度层炸。
- `concludesTurn`：任何提交结果带此标记则 step 直接判 completed（提前结束能力）。

## 6. 事件与拦截点总表（全部源自本目录源码）

| 拦截点 | 模式 | 能做什么 | 源码 |
|---|---|---|---|
| `agent/pre-step` | waterfall | 改写/拒绝本轮输入，追加上下文 | agent.ts:234 |
| `agent/request` | waterfall | 改 provider/model/参数 | agent.ts:438 |
| `agent/request-error` | waterfall | 决定 retry 或上抛 | agent.ts:355 |
| `agent/turn-stopping` | serial | 阻止 turn 停止（继续干活） | agent.ts:296 |
| `agent/error` | emit | 在活动边界上报错误 | agent.ts:206 |
| `agent/status` | emit | idle/running 状态转换广播 | agent.ts:111 |
| `tools/pre-execute`/`execute`/`post-execute` | waterfall | 见链路⑥ | tools/src/index.ts:145-190 |

## 7. 对我们（agent_harness + LangGraph）的对照与借鉴

| dsh 设计 | LangGraph 现状 | 借鉴动作 |
|---|---|---|
| turn/step 边界全部持久事件 | LangGraph 有 checkpointer 但无 turn 语义 | 我们在 SSE 翻译层补 turn/step 事件落库（step_done 已有，补 start） |
| pre-step 可拒绝（拒绝也留痕） | 无对应 | 财务场景：预算/权限预检拒绝时也落 `turn/blocked` 事件 |
| 模型序提交 + callSeqs 引用 | LangGraph 内部处理 | 我们 SSE 下发 tool/result 时携带对应 tool/call 的 seq |
| abort 补合成结果保证回放有效 | interrupt 机制部分覆盖 | resume 不可达的调用启动恢复时补 synthetic result |
| 参数非法不在调度层炸 | pydantic 校验在工具内 | 工具 schema 校验失败返回结构化 error result 而非异常上抛 |
| requestHeader 持久恢复（换模型不恢复旧参数） | config 每次重传 | 会话级记录 provider/model 版本，恢复时校验一致性 |
| `concludesTurn` 提前完成 | 无 | 工具返回"填报已完成"可直接结束 step，省一次模型调用 |
