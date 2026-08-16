# dsh 链路 ④：交互闭环——审批 / 提问 / 中断接管 / 注入（带源码定位）

> 场景对应：财务组件"暂停模型等用户操作 → 答案回传 → 同一上下文继续"。

## 1. 提问 seam：Service Definition / Provider / Consumer 三分离

- **Service Definition**（`packages/interaction/user-questions`）：`ctx.userQuestions` 只有两个
  API——`registerProvider(provider)`（一个 context 只允许一个 UI 端 provider，重复注册抛错）
  和 `ask(request): Promise<Answer>`。`AskUserQuestionRequest` 结构：
  `{ questions: [{ id, question, detail?, header?, options?, multiSelect?, intent? }], agent?, signal? }`。
- **Consumer**（`packages/interaction/tool-ask-user`）：模型面的 `ask_user_question` 工具，
  把模型参数翻译成 request、`await ctx.userQuestions.ask()`、把人的答案以
  `{ answers: [{id, selected, custom?}] }` 规范形返回 agent loop。**它自己不渲染任何 UI**。
- **UI Provider**（`packages/client/ui-user-questions`）：浏览器半边注册 composer 接管。

对我们：工具（Consumer）与 UI（Provider）的分离 = 财务组件的"后端工具"与"前端表单"
可以独立替换——同一个工具换个 Provider 就能变成 TUI/移动端。

## 2. Composer 接管与"意图卡片"

`ui-user-questions/README.md:5` 记录了完整的接管规则：

- UI 插件在 `conversation.composer` keyed slot 注册 `question` 条目——有 pending 提问时
  **整个输入框被替换**，不留占位卡片；
- **presentation intent**（`README.md:9`）：请求携带 `intent` 时渲染为专用卡片而非通用表单。
  `plan-review` intent = "Plan review" 条 + 滚动 markdown 正文 + 三按钮（Chat about it /
  Refuse / Approve）；关键规则："An intent changes the layout, never which answers are
  reachable"——intent 只换布局不换可达答案；卡片**只在能完整表达该请求全部答案时才接管**
  （一题、声明 intent、plan 在场、approve label 命中选项、二元单选），否则退回通用流程。
- **答案与选项顺序解耦**："the intent names which label approves, so the verdict never rides
  option order"——approve 语义由 intent 显式命名，不依赖选项排列。
- **Host 权威**（`README.md:11`）："successful HTTP delivery does not remove pending state
  locally"——本地选中状态只在收到 host 的 `question/resolved` 帧后移除；草稿按 rpcId 键控，
  同 id 回放保留未提交草稿。
- 取消路径：`Chat about it` 以 `ASK_CANCELLED` 拒绝等待——**用户拒绝也是一等答案**。

## 3. 审批 seam：一次性授权 + fail-closed + 审计配对

`packages/interaction/user-approval/README.md`：

- `ctx.approval.request(req)` 返回 `allowed-once | rejected | cancelled | unavailable`；
  "a grant applies only to the requested action"（授权只对这一次动作有效）；
  "missing or failing answerers fail closed"（无应答者 = 拒绝，不是放行）。
- 每次请求属于一个打开的 turn；服务**先落一对 `approval/asked`/`approval/decided` 审计事件**，
  模型只看最终工具结果（"the model sees only the resulting logged tool outcome"）；
  "an audit append that fails before commit rejects rather than returning an unlogged decision"
  ——审计写失败宁可拒绝决策，不留未记账的授权。
- 策略快照：首次请求和每次策略变化后，把当前策略的完整语义（ask/never 各自的含义）
  追加进模型上下文——模型知道"审批可能失败"，不会假设一定通过。

## 4. 输入通道三分：followup / steer / inject

`packages/core/agent-loop/src/agent.ts:120-134` + `packages/core/agent/src/types.ts:10`：

```ts
send(message, target: 'next-turn' | 'next-step', wakeup: boolean)
followup(input) → send(input, 'next-turn', true)   // 排队下一轮，唤醒
steer(input)    → send(input, 'next-step', true)   // 本轮下一step就插入，唤醒（转向）
inject(input)   → send(input, 'next-step', false)  // 下一step插入，不唤醒（静默注入）
```

- **inject 不唤醒**：注入的上下文静静躺在 inbox 里，直到下一条会唤醒的消息到来一起被
  认领——这就是"组件 submit 摘要进上下文但不触发新轮次"的通道。
- abort 后的唤醒输入自动降级为 `next-turn`（`agent.ts:122-124`：waking input 不能加入已
  abort 的活动）。
- inbox 变更是持久事件 `agent/inbox/spliced`（`packages/core/agent/src/types.ts:19`），
  Host 从中派生全量 `session/queue` 快照——客户端不做乐观变更。

## 5. 对我们的落地映射

| dsh 机制 | 落地（FastAPI + agent_harness） |
|---|---|
| userQuestions seam | 工具内 `await` 一个 ask promise；Provider 由前端组件实现的 REST/WS 回调兑现；已有 interrupt/component_request 机制即 Consumer |
| intent 卡片 + "只换布局不换答案" + 接管条件 | 财务组件声明 `intent`；复杂表单渲染器校验"能表达全部答案"才接管 composer |
| 审计配对先行 | 审批/授权类交互先落 `approval/asked|decided` 事件再返回结果；落库失败=拒绝 |
| 三通道 followup/steer/inject | 组件 submit → inject（不唤醒）；用户追问 → followup；运行中改条件 → steer |
| 取消是一等答案 | 组件关闭/放弃发送显式 cancelled resume（对应 plan 8.2） |

## 附：源码补证（v2 复核新增）

### user-questions 服务（packages/interaction/user-questions/src/index.ts，144 行）

- **单 provider 约束的实现**（index.ts:64-75）：generator effect 内
  `if (this.provider !== undefined) throw DUPLICATE_PROVIDER`——注册时即抛，不是运行时。
- **ask() 的完整校验链**（index.ts:92-140），顺序固定：
  1. 信号已 abort → `ASK_ABORTED`（:93-95）；
  2. 空问题数组 → `EMPTY_QUESTIONS`（:96-98）；
  3. **agent 活性双重校验**（:100-113）：`agents.get(agent.id) !== agent` →
     `CALLER_NOT_LIVE`（必须精确是注册表里的活实例）；`!agents.roots().includes(agent)` →
     `DELEGATED_CALLER`，错误文案自带出路："include the unresolved question or decision
     **in the child agent's final result**"——被委派的子 agent 不能问人，要把问题带给最终结果；
  4. **intent 在 asker 侧校验**（:114-135）：`approve` label 必须命中本问题的 options
     之一、`plan-review` 必须携带 detail。注释（:114-120）说明为什么在这里验而不是各 UI 验：
     "Caught at the asker, where the mistake is, rather than in each UI"——
     一次性拦截，不让每个 UI 各自实现；
  5. 无 provider → `NO_PROVIDER`（:136-138）。

### tool-ask-user 工具（packages/interaction/tool-ask-user/src/index.ts，~100 行）

- 文件头注释（:1-6）即定位："The tool **pauses until a UI provider returns a human
  answer**, then feeds that answer back into the agent loop **as an ordinary tool result**"
  ——答案回流走普通工具结果通道，agent loop 无需感知。
- 工具 schema（:20-57）options 描述里写明推荐位约定："If you recommend one, put it first
  and append \"(Recommended)\""——协议约定写进 schema 描述，模型自然遵循。
- output schema（:58-78）`additionalProperties: false` 的严格 answers 形状 +
  `render` 把答案序列化为 JSON 文本——回放时 UI 按同形状解析。
