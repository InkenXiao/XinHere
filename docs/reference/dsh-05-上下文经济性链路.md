# dsh 链路 ⑤：上下文经济性——token 计量、compaction、KV cache 意识（带源码定位）

> 场景对应：财务填报大表单后"注入摘要不注入明细"、长会话压缩（plan 8.5）。

## 1. 计量先行：tokenMeter 单例统一计价

`packages/compaction/compaction-basic/README.md`（What it owns - Measurement）：

- `ctx.tokenMeter` 在同一 consumed-log 修订号上给"最新规范信封 + 当前 surface"统一计价，
  压力判定**包含**：system prompt、工具 schema、路由、assistant 完成、工具结果、
  缓冲上下文（injected）、steering——即模型请求的真实全部成本，而非只算消息文本。
- 压力判定在 step 边界做；容量从"持有最新 provider/model 路由的 adapter"解析（路由策略），
  模型发现只是参考。

## 2. 压缩事务：bracket-first 生命周期

同 README（Lifecycle / Retention）：

- **区域事务**：校验范围和活动锁 → 同步 append `compaction/start`（这也是**持久锁**：
  活动中未匹配的 start = busy）→ 生成摘要 → 复验 → append `compaction/summary` + 替换消息
  → 恰好一次关闭尝试。
- **保留策略**：压缩"最老的完整 surface 单元"，保留近期尾部；工具调用/结果**成对**切割
  （不产生孤儿调用）；打开的不可分尾部等它关闭；turn 边界不保护 runaway turn 内的旧 step。
- **收敛纪律**："reject a summary that does not shrink its source"——摘要必须比原文小，
  否则重试（`compactionRetries` 次），再不行抛错。"a failed close deliberately leaves a
  blocking orphan"——失败关闭故意留下阻塞孤儿防止并发二次压缩。
- **先修剪后压缩**：可选 `toolResultPruner` 在范围选择前重写超大的工具结果（model-free），
  修剪后压力已安全则**跳过摘要**（省一次 LLM 调用）。

## 3. KV cache 复用的摘要调用（最值得抄的经济性细节）

同 README（Summarization）：

- 摘要是直接 `llm/stream` 一次性调用，**逐字重放**本会话自己的 system prompt、工具、
  被遮蔽区域消息（含图片引用），把压缩指令作为最后一条 user message 追加——
  "it reuses the provider's warm prefix cache instead of invalidating it"。
  压缩不烧缓存，这是把压缩成本降到最低的关键设计。
- `GenerateOptions.purpose = 'compaction'`，DeepSeek 适配器据此发
  `x-deepseek-harness-compact: 1` 归因头，不动模型可见正文。
- 摘要只取返回文本："excluding reasoning and tool calls that would leak private reasoning
  or create an orphaned call"——不泄漏推理、不产生孤儿调用。
- 唯一的子类钩子就是 `summarize()`——压力、保留、引用、缩减校验、遮蔽 token 记账
  全部留在框架；换远程摘要器只换这一个方法。

## 4. 替换语义：shadowed 而非删除

- 被压缩的节点不删除，成为 `shadowed` surface；替换 user message 用
  `<compacted-summary>` 标签框住摘要；`compaction/summary` 事件保留原始摘要 +
  `sourceEventSeqs` 指向被替换节点。
- 后续自动压缩周期**合并**前一个 checkpoint（摘要套摘要）。
- 溢出恢复：provider 确认的 overflow 不需要容量元数据——绕过正常压力路径，
  修剪后做一次最大平衡的头部缩减，保留最新不可分单元。

## 5. 模型体验纪律（每个包 README 的 "Model Experience" 节）

dsh 每个插件包的 README 强制三问：模型看到什么（What the model sees）、
token 影响（Token effect）、**KV cache 影响**——例如 user-questions：注册表本身零 token、
零缓存影响；permission-presets："间接影响，经由它选择的服务"。这个文档纪律本身就是
架构守门机制：任何插件作者必须声明自己对外部模型请求的成本。

## 6. 对我们的落地映射

| dsh 机制 | 落地（LangChain/LangGraph） |
|---|---|
| tokenMeter 全成本计价 | 压缩触发器算 system prompt + 工具 schema + 注入上下文，不是只算 messages 长度 |
| bracket-first 事务 + start 即锁 | 压缩事件对 `compaction/start`→`compaction/summary`，start 落库即互斥锁（DB 侧比文件侧更好做） |
| 摘要必须缩减 + 失败留锁 | 摘要 token 数 > 原文则重试/失败；拒绝生成"越压越大" |
| 重放前缀复用 KV cache | 摘要请求的 system prompt 与主对话保持一致前缀，压缩指令追加为 user message——直接降本 |
| purpose 归因头 | 摘要/标题类调用打标（计费与审计区分主对话成本） |
| shadowed 而非删除 | 财务明细被压缩后仍是 log-only 事件，UI 可展开"查看原始明细" |
| Model Experience 三问 | 插件模板 README 必填"模型可见性/token/缓存"三节——AI coding 的自检清单 |

## 附：源码补证（v2 复核新增）

### KV cache 复用的真实构造（compaction-basic/src/summarizer.ts + region.ts）

- **COMPACTION_INSTRUCTION 常量**（summarizer.ts:24-31 注释 + :31-59 全文）：压缩指令
  是**追加在被重放对话之后的最后一条 user message**，不是独立的 summarizer system
  prompt——注释原话："Keeping the conversation's own system prompt, tools, and message
  prefix in front of it makes the auxiliary call **a genuine prefix of the last routed
  request**, so the provider's KV cache is reused instead of invalidated"。
- **指令模板是固定 Markdown 结构**（summarizer.ts:34-58）：八个固定节
  （Primary Request and Intent / Key Technical Concepts / Files and Code / Errors and
  Fixes / Pending Jobs / Current Work / Next Step / Critical Context），"keep every
  section, in order…Write \"(none)\" for an empty section — never drop a section"——
  摘要输出本身就是可解析的结构化 checkpoint。
- **重放前缀的构造函数**（region.ts:488-514 `buildSummarizationInput`）：
  ```ts
  const header = session.requestHeader()
  const regionMessages = shadowedSeqs.map(seq => session.deriveEventMessage(events[seq]))
  return { system: header.system, tools: header.tools, messages: regionMessages }
  ```
  ——system prompt 与 tools 取自**持久化的上次请求头**，被压缩区域逐 seq 重新投影成
  messages；"The summarizer appends only the compaction instruction after this"。
- **压缩标记是普通常量**（summarizer.ts:21-22）：`<compacted-summary>` 开闭标签就是
  模块常量字符串——替换消息的框架没有任何魔法。
- **入口状态检查**（region.ts:516-544 `inspectCompactionEntryState`）：单次倒序扫描
  同时识别三件事——打开的 turn（turn/start 未闭合）、未匹配的 compaction/start、
  最新 session/end-seed 边界；`unmatchedCompactionStart` 在 end-seed 之前 = 陈旧锁
  （不阻塞），之后 = busy——README 所述"stale evidence from a prior lifecycle"的
  实现就是这三个游标的一次遍历。
