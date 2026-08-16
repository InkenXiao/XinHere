# dsh 链路 ⑦：护栏（guard）与 链路 ⑫：凭证（credentials）（源码精读）

> 源码：`packages/guard/repeat-tool-reminder`、`packages/guard/timeout-policy`、
> `packages/credentials/credentials`。两条链路合一份文档（都不长但纪律极密）。

# Part A 护栏：repeat-tool-reminder（防死循环刷接口）

## A1. 定位：advisory，不是 veto（README 首段原话）

"An advisory loop-breaker, not a model-facing tool: it never appears in the tool list,
**never vetoes or rewrites a call**, and adds exactly one behavior"——不注册工具、不否决、
不改写，只注入升级提醒；决策权完全留给模型（合法的重复调用不被延迟也不被阻止）。
源码：packages/guard/repeat-tool-reminder/README.md 第 2-4 行。

## A2. 链路键与计数语义（README "Chain semantics"）

- 链路键 = `(tool name, canonical arguments)`；canonicalization = 深度键排序 + JSON.stringify
  ——**参数只有属性顺序不同也算相同调用**；
- 与上一个被跟踪调用相同 → 计数 +1；不同 → 重置为 1；
- **exclude 的调用对链路透明**："neither increments nor resets"——`grep X → todo_write →
  grep X` 在 todo_write 被 exclude 时仍算连续两次 grep X。"记账工具穿插进循环不能洗白它"
  ——这是 exclusion 有意义的全部原因；
- **被拒绝的调用也计数**：检测挂在 `tools/post-execute`（对被 pre-execute 拒绝的调用也
  运行），"a model hammering a denied call is exactly the loop worth breaking"；
- **per-agent 键控**：`WeakMap<Agent, Chain>`，子 agent 穿过同一瀑布也不串线；用户提交新
  prompt（`agent/pre-step`）重置该 agent 的链；
- **仅内存**：resume 后从新链开始——"the guard is a heuristic nudge, not a logged invariant"。

## A3. 配置防呆（README "Config"）

`thresholds`（默认 [3,5,8]）在插件加载时 fail-loud：空列表/非整数/<2/重复直接抛错，绝不
静默回退默认；首阈值发简短通用提醒，后续阈值发详细版（工具名+连续长度+canonical 参数，
参数头截断 `argumentsPreviewChars`——**上限约束提醒文本，永不约束检测**：链路键始终比对
完整 canonical 字符串，防止循环中的大 write/edit payload 无界进入下个请求）。

## A4. 提醒的投递通道（README "Reminder delivery"，最值得抄）

- 提醒**搭乘 post-execute 决策的 `additionalContexts`**（source 标记
  `{kind:'plugin', plugin:'repeat-tool-reminder'}`），**绝不用 content 替换**——
  `tool/result` 事件仍是工具自己的输出，审计不被污染；
- 循环把 context 缓冲后作为注入的 `user/message` 追加在本 step 工具结果之后——
  "model-visible, source-attributed, and reconstructable from the session log **with no
  new session event**"（复用现有事件类型，零新词表）；
- 始终 `next()` 委托并把提醒 prepend 到下游决策的 context 数组（被 block 的调用也拿到提醒）。

## A5. Model Experience 声明（README 后半）

首阈值提醒文案是固定模板；"Zero tokens before the threshold"；KV cache："Append-only;
newly visible content follows the reusable request prefix and does not invalidate existing
KV-cache entries"——护栏自身不烧缓存。

## A6. timeout-policy（同目录姊妹包）

函数插件而非服务、零配置：消费 `tools/execute` 瀑布，读每个工具**自己声明**的
`timeoutMs`（ToolDefinition 字段）并强制执行——"this plugin only enforces it, so a
mistyped tool name is not possible"（声明与执行分离，配置不可能拼错工具名）。

# Part B 凭证：credentials（"One doctrine, three consequences"）

## B1. 三条教义（README 首节原话）

1. **配置只携带秘密的引用，永远不携带秘密**："A settings section says
   `apiKeyEnv: DEEPSEEK_API_KEY`; the value lives with a credential provider"——
   配置文档因此可以安全同步、在配置 UI 里渲染；`describe()` 能回答"配了没/来自哪/可否写"
   而**永远不持有值**；轮换密钥不碰任何配置文件。
2. **消费者逐操作解析、永不缓存**："resolve(ref) is called at the start of each operation
   (the LLM adapters resolve once per model request)"——换密钥无需重启任何插件，
   下一个请求就生效。
3. **空存储值 = 未配置**："An empty stored value is absent. Everywhere"——空白永远不能
   冒充已配置的密钥。

## B2. 遮蔽规则 fail-loud（README "Surface" 节）

当只读源（如进程环境变量）当前供应某引用时，`set/unset` **直接拒绝**而不是假装成功——
"否则写会看似成功而解析仍返回遮蔽值"。`describe().writable` 让 UI 提前渲染只读态。

## B3. 事件与可观察性

`credentials/updated(ref)` 只在 provider 管理源的已提交变更后触发（set/unset/外部存储编辑）；
**进程环境变量的变化不可观察、永不发事件**。消费者不需要事件（它们逐操作重解析）；
事件只为配置 UI 刷新"已配置"徽标。observer 异常不能破坏持久变更（src/index.ts:111 注释）。

## B4. Provider 分层

`credentials-local`：进程环境变量 **覆盖** `$DSH_HOME/.credentials.yaml`，launcher 的
项目/用户 `.env` 兜底。seam 形状为 keyring/helper 命令/KMS provider 留了位置；
"远程 settings provider 永远不需要携带秘密"。

# 落地映射

| dsh 设计 | 落地（我们） |
|---|---|
| advisory 护栏不否决 | 财务接口重复调用检测：同参数连续 N 次注入提醒，不拦（拦截会造成模型困惑重试风暴） |
| 链路键 canonical 化 | 键 = (tool, json.dumps(args, sort_keys=True))，参数顺序不洗白 |
| exclude 透明语义 | 分页/记账类工具 exclude 后不重置循环计数 |
| 被拒调用也计数 | 检测挂在结果侧（含 permission denied 的调用） |
| 提醒走 additionalContexts 不改 content | 提醒作为注入 user message 落日志，tool/result 保持纯净 |
| 阈值 fail-loud | 配置校验在启动时抛错 |
| timeout 声明/执行分离 | 工具 schema 声明 timeout_ms，统一 middleware 强制 |
| 凭证三教义 | 配置只存 env var 引用；每请求解析；空白=未配置 |
| 遮蔽 fail-loud | 环境变量已定义时 UI 禁止写库内值 |
| 凭证永不进日志 | 事件/遥测 schema 校验拒绝 secret 字段（配合链路②脱敏瀑布） |

## 附：源码补证（v2 复核新增）

### repeat-tool-reminder 实现（guard/repeat-tool-reminder/src/index.ts，233 行）

- **两级提醒文案就是模块常量/纯函数**（index.ts:60-79）：`GENTLE_REMINDER` 常量
  （keyed to `thresholds[0]`，注释说明"not a literal count, so a custom first threshold
  keeps the gentle-then-detailed escalation"）；`detailedReminder(toolName, count,
  canonicalArguments)` 纯函数拼装（tool/连续次数/canonical 参数三行 + 行动指令）。
- **canonicalize 的实现**（index.ts:95-105）：`sortJsonValue` 递归深排序（数组保序、
  对象键排序）→ `JSON.stringify`；注释解释为什么不需要处理 bigint/环引用——
  "Arguments reach the guard as the loop's JSON.parse output…**JSON's value domain is
  the whole input domain**"——输入域被上游收敛，校验天然安全。
- **通配符编译**（index.ts:108-111）：`*` 转为 `.*`，**其余正则元字符全部字面转义**
  ——include/exclude 是 glob 语义不是正则注入面。
- **previewArguments**（index.ts:116-121）：头截断 + `… (+N more chars)` 标记，
  注释再强调"Bounds only the model-visible text — **the chain key always uses the
  full canonical string**"。
- **validateThresholds**（index.ts:126+）：空列表抛错（fail-loud 契约），返回前
  排序 ascending——"the escalation rule reads thresholds[0] as the gentle tier, so
  order is normalized here, **once**"（归一化只做一次）。

### credentials 抽象（credentials/credentials/src/index.ts，~120 行）

- **CredentialInfo 三字段**（index.ts:36-45）：`configured / source? / writable`——
  注释"safe for configuration UIs — **never the value**"。
- **ResolvedCredential**（index.ts:30-34）：`{ value, source }`，source 是 provider
  定义的层 id（本地 provider 用 `env` / `file` / `project-env` / `user-env` 四层）。
- **seam 级单一规则写在抽象类注释**（index.ts:52-56）："one seam-wide rule binds them
  all: **an empty stored value is absent everywhere** — resolve skips it, describe
  reports it unconfigured — so a blank never masquerades as a configured secret"。
- **resolve 的 JSDoc 契约**（index.ts:58-73）："Resolution is per call: consumers
  re-resolve at each operation **and must not cache across operations** — that
  per-operation read is what makes a changed credential reach the next operation
  without a restart"——不缓存是接口契约而非实现细节。
