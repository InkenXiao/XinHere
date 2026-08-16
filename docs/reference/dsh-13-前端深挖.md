# dsh 前端深挖：连接信任边界 / HMR / 表单引擎 / 主题防闪 / 原子纪律（dsh-01 之外的补充）

> dsh-01 覆盖了"注册→消费→回显"链路；本文挖前端剩余的高价值取舍。
> 依据：packages/client/{connection, hmr, schema-form, web-react, locale, ui-theme,
> ui-primitives} 的 README 与源码结构。

## A. Connection：浏览器↔Host 的信任边界（安全设计，最容易被自研忽略）

### A1. /api 浏览器信任围栏（connection/README "browser-trust fence"）

- **Host 头是唯一不可伪造的防线**："over plain HTTP a browser attaches neither `Origin`
  nor Fetch-Metadata to image and navigation reads, so an unmarked request may still be a
  rebound browser read…**Host is the one header rebinding cannot forge**"——所有请求
  （含无标记的）必须 `Host` 是 loopback 或命中 `trustedHosts`，两边都过 WHATWG
  规范化比较——**DNS rebinding 防御**；
- **trustedHosts 条目在插件加载时 fail-loud**：不是裸 `host[:port]` 权威（WHATWG
  解析能原样读回）就抛错——否则"会悄悄授权 `harness.internal/path` 里的主机名，或把
  悬空冒号/前导零端口放大成任意端口授权"；
- **`Origin` 存在时必须等于 Host 权威；显式 `sec-fetch-site: cross-site` 直接拒**；
- **"围栏是可达性策略，不是认证"**——"The fence is a reachability policy, not
  authentication"，`dsh web --host 0.0.0.0` 刻意不支持直到有认证层。

### A2. 特权方法只允许 loopback（最小授权面）

配置面板全套（`settings.*`、`credentials.*`）、`host.pickDirectory/openPath`、
**agent-preset 读写**都钉死 loopback。理由写得很细："a composition names the plugins a
session runs, so reading one **is reconnaissance**"（读 preset = 侦察）；而
`agentPreset.list/select` 不在内——"the roster carries only ids and trust, and choosing
a preset grants nothing `session.create`'s own `agentPreset` did not"（**授权语义按
"它能拿到什么"划分，不是按"它像不像管理功能"划分**）。

### A3. 双下行 WebSocket

`/api/events.mux` 和 `/api/events.host` 各一条**只下行的 WS**："the client sends no
application data over these sockets"——浏览器不在这两条流上发任何应用数据（上行全走
HTTP POST unary）。任一 socket 断 → 当前连接 generation 失败、**两条流整体重建**；
readiness 要求双 socket + `host.describe` HTTP 都成功。普通 GET 返回 426 无 SSE 回退。

**对我们的价值**：WS 推流 + HTTP 上行的分离（比双向 WS 简单得多且好调试）；
信任围栏的 Host 头策略直接抄——本地部署的 FastAPI 同样面临 DNS rebinding。

## B. HMR：插件级热替换的精确序列（hmr/README）

- **每帧（rebuilt）一个插件经串行队列重载**，序列固定：`invalidate` → `prefetch`
  （**装载并注册新 bundle 时旧 fiber 仍在服务**）→ `registry.delete`（注释给出次序
  陷阱："a bare fiber dispose trips the vendored Loader's self-dispose branch, which
  would mark the entry disabled"）→ drain 旧 fiber → 删 `<style data-plugin>` →
  `entry.refresh()` 重导入重挂载 → `fiber.await()` **把启动失败重新抛出**；
- **依赖级联零客户端图分析**："a fiber's **activation epoch strings its service
  providers' uids**, so replacing a provider's fiber cascades every dependent"——
  依赖失效由 Cordis 的 epoch 机制自动完成，客户端不维护依赖图；
- **构建检测靠 stat 轮询 + 哈希**："retains missing rows as dirty, and broadcasts only
  real rev changes"——任何 tsdown watch 产出都能触发 HMR，**无需 builder→host 通道**。

**对我们的价值**：前端插件若做运行时热插拔（plan 里的远期项），这套"prefetch 先行 +
epoch 级联 + 无构建器通道"序列就是蓝图；即使只做 Vite HMR，"启动失败重抛"和
"样式按插件标签清理"也适用。

## C. schema-form：配置表单从 schema 生成（插件配置 UI 的地基）

- 编辑单元是**不可变 draft user section**：`setPath` 物化中间层、`deletePath`
  是字段级重置（"dropping the key falls the resolved value back to the composition
  base and schema defaults"）；**presence 语义与 settings seam 完全镜像**
  （"presence semantics, not value comparison"）；
- `nodeAtPath` 让编辑器**先探测** provider profile 有哪些字段（含 `meta.role`——
  secret 字段）再决定渲染；"an unresolvable path returns undefined so the caller
  **degrades loudly** instead of rendering a wrong subtree"；
- `validateDraft` 在写之前本地拒绝无效草稿。

**对我们的价值**：业务插件的配置面（数据源连接、参数）用同一份 JSON Schema 驱动
pydantic 校验 + 前端表单生成——**契约三用**（模型工具参数/后端校验/配置表单）。

## D. web-react：React 粘合层的克制与诚实

- 全部 React 粘合只有四个构件：`createSlotRenderer`、`SessionProvider`、
  `bindSnapshotSelector`（**唯一的 hook 构造器**，"hosts and engines traffic in bare
  observable sources; every hook binds here, cached per source"）、`useInvoke`；
- **业务插件永不依赖本包**："business plugins depend on ui-slots types only, never on
  this package"——框架升级换掉 React 粘合层不动业务代码；
- **Known Limitations 暴露真实缺陷**（罕见的诚实）：zustand persist 中间件会
  "object-spreads state on save, so a `SnapshotStore<string>` round-trips as a
  character map"——引擎绕开它手写持久化；"`UseSession` is deliberately wide
  (`object` snapshot)"——依赖方向决定了类型只能宽，消费者在边界收窄。

**对我们的价值**：React 壳同样应把 hooks 构造收敛到一处、业务插件只依赖类型包；
README 的"已知缺陷+绕开方案"格式值得照抄（AI coding 时的边界说明书）。

## E. 主题与 FOUC 防闪（ui-theme）

- **运行时不碰 DOM**："it never touches the DOM — ui-layout's presenter applies the
  resolved snapshot"——状态与呈现分离，主题服务可单测；
- **FOUC 防御的关键一手**：host 在每个 index 响应的 `<body>` 开标签**之后同步注入
  bootstrap 脚本**，内嵌持久的主题偏好；"the browser resolves `system` from the OS
  scheme, then sets `color-scheme` and `body[data-ds-dark-theme]` **before the shell
  loading page renders**"——首帧之前完成主题解析，加载页也不闪白；
- **被拒的快速切换回滚**："rapid selections are serialized in gesture order with
  namespace revisions, and **a rejected latest write reloads the durable value**"——
  乐观 UI + revision 串行 + 失败回读三层；
- **滚动条 token 重绑定契约**：elevated surface（menu/popover/dialog）在自己容器上
  重绑定 thumb token，"one rebind retints whichever path the engine took"——
  主题化覆盖到滚动条这种最难的细节。

**对我们的价值**：暗色模式 + 会话偏好持久化时，`<body>` 后同步注入这段 5 行脚本是
零依赖的 FOUC 解；呈现与状态分离让主题可测试。

## F. locale：临时值→权威值两段式

- "**a fresh browser starts provisionally** in the language `navigator` asks for"，
  Host 的显式偏好读回来后**活替换**临时值；"The Host read runs after plugin
  activation **so an unavailable settings service cannot block the page**"——
  永不为一个可能缺席的服务阻塞首屏；
- 字典查找链 `ns → common → zh → key`——兜底到 key 本身而不是抛错；
- 类型化命名空间注册（`LocaleNamespaceMap` merge-extensible）。

**对我们的价值**：任何"先本地猜、后端确认再替换"的偏好（时区、语言、单位）都是
这个模式；查找链兜底 key 是插件缺字典时不白屏的保证。

## G. ui-primitives：零 Cordis 原子纪律

- 原子组件**零 Cordis**："this zero-cordis atom **cannot read the application
  locale**"——本地化文案全靠调用方传 props（`copyLabel`/`copiedLabel`）；
- 每个原子自带无障碍细节：Toast `role="alert"`、`prefers-reduced-motion: reduce`
  下降级动画、HoverCard 键盘可达 + 选中区不触发点击、"success feedback retains the
  original card height"（成功反馈不跳动布局）；
- Toast 重复显示需 remount："owners key the element by a per-show sequence so an
  identical repeated message **restarts** the hold-and-fade cycle instead of silently
  reusing the faded banner"。

**对我们的价值**：业务组件库的最底层（按钮/卡片/Toast/Tooltip）同样应零依赖框架，
文案外置——这样 AI 生成的业务插件可以直接引用原子而不拖入状态管理。

## 落地优先级

| 宝藏 | 价值 | 时机 |
|---|---|---|
| A 信任围栏 + 双下行 WS | ★★★ 本地服务安全 + 通信架构 | M1 |
| C schema-form 契约三用 | ★★★ 插件配置 UI 零手工 | M2 |
| E 主题 FOUC 同步注入 | ★★ 建站第一天就要（后补很痛） | M1 |
| D React 粘合收敛 + 诚实 limitation | ★★ 壳架构纪律 | M1 |
| F locale 两段式 | ★★ 偏好类功能通用 | M2 |
| B HMR 序列 | ★ 观察期（先构建时打包） | 远期 |
| G 零 Cordis 原子 | ★★★ 随手带入 | M1 |
