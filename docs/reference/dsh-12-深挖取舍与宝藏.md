# dsh 深挖：主链路之外的设计取舍与埋藏宝藏（源码+包README 精读）

> 15 条主链路（dsh-01~11）之外，本轮挖掘 8 个未覆盖区域 + 贯穿全仓的工程纪律模式。
> 每条含：dsh 的取舍（为什么这么选/放弃了什么）+ 对我们的价值。

## A. Code Mode：模型写代码代替逐个调工具（packages/code-runtime）

**机制**：模型可以调用 `run_code` 工具，在程序里**以普通函数调用**的方式使用其他工具
（binding = 工具的桥接命名空间），dsh 把子调用桥接回真实工具管线。

**关键取舍（code-runtime/README + src/types.ts）**：
- `run()` 对**所有程序性失败都 resolve 带错误字段**（解析失败/抛异常/输出超限/预算到期/
  abort/宿主死亡），"rejects only for caller misuse of the Service Definition contract"
  ——程序失败是数据不是异常，模型能读到并自纠；
- 错误分类是**正交的 kind 分类学**（`CodeRunFailure: kind + model-feedable message`）；
- **程序被当作敌对同侪**（"treated as a hostile peer"）：任意 binding 名、畸形流量
  永远打不崩宿主；run 之间零状态存活；
- `isolation` 标签（worker-thread/process/container）"is a label, **not a security
  claim**"——诚实声明隔离描述符不是安全承诺；
- **语言可移植的绑定命名**：标识符子集排除 `$`（JS-only）、导出
  `PORTABLE_RESERVED_WORDS`（ECMAScript ∪ Python 保留字并集）——一份 bindings 对任何
  后端语言合法；
- 桥接调用"no seam-level byte cap"——字节预算归策略层（spill），不归运行时。
- 配套：`tool/code-dispatch(-start)` 事件（链路②已述）记录子调用但不进模型上下文。

**对我们的价值**：中期方向。当"查 A 公司再查 B 公司再合并"这类多步操作频繁时，
与其让模型发 5 次工具调用，不如给它一个可编程操作面——子调用照样走我们的工具管线
（权限/审计/超时一个不少），但模型侧从"对话式调用"升级为"编写调用"。

## B. LLM seam：适配器注册的"无缺口替换"（packages/llm/llm）

**取舍**（llm/README Public API）：
- `registerAdapter` 的 disposer 带 `replace(providers)`："the candidate route set is
  validated in full **before anything moves**, so a conflict with another adapter leaves
  the current routes registered and serving, and **the swap itself is one synchronous
  section with no observable gap**"——热换适配器无服务缺口；`replace([])` 合法
  （清空路由），空初始注册非法（对称性破例有明确理由）；
- **模型发现是配置时工作、面向 draft**："keyed by settings namespace rather than by
  provider route — the provider a surface is adding does not exist yet"；探询用的凭证
  "the harness uses for that one interrogation and **never stores**"；
- `LlmDiscoveredModel` 只有 `id` 必填——"most provider listings disclose an id and
  nothing else"——对现实世界的目录质量的诚实建模；
- **流协议单一终态归一**："normalizes failures from final adapter selection, sync
  dispatch, iterator construction, and iteration into the stream protocol's single
  terminal form `finish {kind:'error'|'aborted'}`…A failure after partial deltas may
  leave content blocks open; consumers discard that incomplete output"——半截输出
  的处理责任划给消费者，协议只有一种失败形状。

**对我们的价值**：多模型接入的注册表设计（我们迟早接非 DeepSeek 模型）；
"探询凭证永不存储"配合链路⑫。

## C. BlockAssembler 与 replayState：流式块的回放保真（packages/llm/llm/src/assembler.ts）

- `BlockAssembler`（assembler.ts:36）把 token 级 chunk 聚合成内容块；
- `replayState`（assembler.ts:41、89、152）：**适配器在流中携带的不透明回放状态**，
  聚合后存进 `assistant/message` 的 source（链路⑤ agent.ts:378 的
  `assembler.replayState`）——provider 特定的解码状态（如思考块的内部状态）随消息
  持久化，重放时不丢失保真度。**不透明**：session 层不知道里面是什么，只负责存取。

**对我们的价值**：如果模型输出含推理内容/特殊块，SSE 翻译层应保留 provider 特有
状态随事件持久化，前端回放才无损。

## D. Settings：双层配置的完整契约（packages/settings/settings）

**取舍**（settings/README，每条都是决策）：
- **base/user 双层 + presence 语义**："A field is user-overridden when it is PRESENT in
  `user` — an override equal to the composition default is still an override —
  comparing values could not see"——值相等≠未覆盖，UI 能区分"故意设成默认值"；
- **redactSecrets 是 wire 强制**："every wire surface MUST pass it"——`role('secret')`
  字段在描述符层剥除并给 `{path, set}` 槽位；
- **mutate(op) 存在的理由**（对抗脱敏视角丢失）："a configuration UI reads the
  redacted descriptor, so rebuilding a section from it and **replacing wholesale deletes
  every secret the wire never returned**, while an op names the one field it means"
  ——持有不完整视图的调用方只能用 set/unset op，不能整体替换；
- **乐观锁**：`expectedRevision` + `SettingsConflictError`（"both revisions attached"）
  ——"The write queue orders writes but cannot by itself tell a fresh writer from one
  holding a stale snapshot"；
- **patch 只进 user 层**："deep-merges into the user section only (never the base)"；
- **非 JSON 数据在入口拒绝**："a Date, Map, BigInt, non-finite number, or circular
  reference rejects with its `$`-rooted path **before anything persists** (YAML/JSON
  storage would silently change such values on reload)"——存储会静默变形的值干脆不让进；
- **热重载降级**："an invalid section keeps that namespace's last good value and warns
  — a live reload never takes the process down — while boot-time validation fail loud"
  ——**运行时宽容、启动时严格**的双标准。

**对我们的价值**：业务插件的配置体系直接照此设计（尤其 presence 语义、mutate op、
redact 强制、乐观锁四条，都是踩过坑才会有的规则）。

## E. Gateway / Typert：类型化 RPC 的严格/回退双模（packages/api/gateway）

**取舍**（gateway/README）：
- **严格模式读生成描述符**；**SRC 模式是"从未有过严格定义的端点"的开发回退**，
  "Withdrawing an observed strict definition **fails instead of weakening validation**"
  ——撤描述符宁可失败也不降级校验；
- **signal 是描述符元数据不是 wire 参数**："declares `signal: AbortSignal` as its final
  Host parameter. The signal is descriptor metadata rather than a wire argument"——
  取消信号在网关注入，不跨线；
- 错误分层：业务错误原样透传，`TypertGatewayError` 只区分"调度/绑定/provider/查找/
  Context/参数/编解码"等**基础设施失败**——业务语义与传输故障永不混淆；
- `TypertLookupFailure` 可携带已有 RPC 错误"preserving its original error code for
  policy rejections such as cold-resume failures or ownership fences"——策略性拒绝
  （所有权围栏）跨层保码。

**对我们的价值**：FastAPI 与前端的 RPC 层（如果做类型化调用而非裸 REST）；
"撤严格定义=失败"和"取消不跨线"两条纪律直接适用。

## F. TextRetainer：保留/省略的机械学独立成库（packages/util/output-retention）

**取舍**（output-retention/README）：
- 库只回答一个问题："**what did we keep, and what did we omit?**"——业务语义
  （文件分组、行号、退出码、逐行预览、溢出文件、模型面文案）全部归工具包；
- **是库不是服务**："no `ctx`, registers nothing, emits no events. The only state is
  per-retainer, never cross-call"——可单测、零插件耦合；
- 两个原语：`TextRetainer`（headTail/预算切分）+ `ItemRetainer`（条目级），
  `describeOmitted` 生成统一的省略描述文案。

**对我们的价值**：所有"截断/预览/省略"逻辑收敛到一个纯函数库——财务组件里表格
分页预览、工具结果截断、通知裁剪（链路⑨的 64 字节预算）共用同一套机械学。

## G. Feedback 命令：不解析即尊重（packages/feedback/command-feedback）

**取舍**（command-feedback/README）：
- "feedback is otherwise **unparsed**: no truncation, case folding, or control words.
  Text that looks like another command, such as `/feedback /plan felt slow`, **is
  feedback content**"——绝不猜测用户意图，看起来像命令的也是反馈内容；
- "Repeated commands each produce **their own event; nothing is replaced or merged**"
  ——append-only 语义贯彻到反馈；
- 确认语包含会话共享披露（链路②的 sharing disclosure 在此消费）。

**对我们的价值**：用户反馈/点评功能的语义纪律——存储原始、不合并、不复写。

## H. Boot：fail-loud 启动与 HMR 补丁层（packages/boot/app-boot）

**取舍**（app-boot/README 表格）：
- `installFailLoud`：启动或 Loader 拒绝 → 单行标记 stderr + exit(1)；**release 钩子
  有超时**："a wedged disposer **delays** the fatal exit, never cancels it"——
  卡死的清理器最多拖时间，不能取消致命退出；
- `assertEntriesActivated`：await 每个启用插件，失败时报告"原始栈或未解析服务"——
  启动期把插件依赖问题全部暴露，不带病运行；
- **环境分层冻结**：inherited > project `.env` > user `.env`，"materialize accepted
  file values **without replacing inherited ones**"——文件值不覆盖继承值；
- **快照模式**：`snapshotMode==='replay'` 时 `cordis.yml` 换成兄弟文件
  `cordis.snapshot.yml`——测试回放用另一份配置，产品配置零污染；
- `watchUserPatches`：用户补丁文件每次增删改**事务性地全量重组**补丁列表
  （"transactionally recomposes the full patch list"）——HMR 的单位是整层而非单条。

**对我们的价值**：插件加载器（M1）的启动纪律：fail-loud + 全量激活检查 + 退出超时。

## I. 贯穿全仓的工程纪律（跨包模式，最值得整体移植）

1. **invariant.ts 伴随包模式**：几乎每个包都有 `src/invariant.ts` + 独立发布的
   `/invariant` 入口（spill、attachment、goal、schedule、tools…）——**运行时不变量
   与业务逻辑分开发布**，测试/开发组装可单独挂载校验（如 goal 的 invariant 在事件
   **进入持久日志前**独立 fold 校验）。
2. **Agent Notes 决策日志**（`.agents/notes/{implemented,proposed,archived}/`）：每个
   非平凡决策一篇带日期的 note（如 zstandard-jsonl-session-logs、job-registry-seam），
   README 链接引用。**决策有出处、有状态（proposed/implemented/archived）、有理由**。
3. **Model Experience 三问**：每个插件 README 强制"What the model sees / Token
   effect / KV Cache effect"三节（链路⑤已述，此处补全其普遍性——连 storage 这种
   纯 host 包也要写"Zero direct tokens"）。
4. **fail-loud vs fail-closed vs best-effort 的显式分级**：注册冲突 fail-loud（抛错）、
   未知事件 fail-closed（拒绝重建）、溢出存储失败 best-effort（保原文）——三种策略
   在各包里都**写明选了哪种和为什么**，没有默认隐式。
5. **WeakMap 生命周期管理**：per-agent/per-session 的进程内状态几乎全用 WeakMap
   （repeat-tool-reminder 的链、goal 的缓存、jobs 的游标）——"object lifetime bounds
   the weak entry **without a disposal listener**"——用对象图管理生命周期而不是手工
   注册清理器。
6. **生成式文档校验**：known-event-types 由脚本从源码生成且 CI 校验 fresh
   （"GENERATED…do not edit; verified fresh by pnpm run verify-persistence-catalog"）；
   cordis-surface 文档段同样生成并字节级校验——**文档漂移在 CI 拦截**。
7. **错误码是封闭词表**：每个域的错误 code 列表封闭且稳定（schedule 十个、
   user-questions 八个、settings 的 SETTINGS_CONFLICT…），诊断"do not expose backend
   exceptions"——内部异常永不裸奔到模型/前端。

## 落地优先级

| 宝藏 | 价值 | 建议时机 |
|---|---|---|
| D settings 双层契约 | ★★★ 插件配置体系的地基 | M1 |
| I-1 invariant 伴随包 | ★★★ 契约测试的载体 | M1 |
| I-2 Agent Notes | ★★★ AI coding 协作纪律 | M1 起持续 |
| F TextRetainer 纯函数库 | ★★★ 截断/预览统一 | M2 |
| B 适配器无缺口替换 | ★★ 多模型接入时 | M2 |
| C replayState 回放保真 | ★★ 有推理内容模型时 | M2 |
| E gateway 双模+错误分层 | ★★ | M2 |
| H boot fail-loud | ★★ 插件加载器 | M1 |
| G feedback 不解析 | ★★ 用户反馈功能 | M3 |
| A Code Mode | ★ 方向性 | 观察期 |
| I-4/5/6/7 纪律模式 | ★★★ 零成本随代码带入 | 全程 |
