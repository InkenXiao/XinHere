# dsh 链路 ⑧+⑬：溢出（spill）与附件（attachment）——大数据与二进制外存（源码精读 v2）

> 本版全部结论直接引用 `src/*.ts` 源码行号；README 仅作对照。
> 源码：`packages/spill/{spill,spill-policy,spill-local}/src`、`packages/attachment/attachment/src`。

# Part A 溢出（spill）

## A1. 词汇与契约（spill/src/types.ts 全文 73 行）

- `SpillLocator = Branded<'SpillLocator'>`（types.ts:18）——注释明确："A local backend may
  use a filesystem path; a remote or database backend may use a URI or key. Consumers render
  it with retrievalHint, **but do not parse it**"——消费者禁止解析定位器。
- `SpillOwner { sessionId }`（types.ts:37-39）：保存时命名空间；注释："Forked sessions
  inherit locators already present in the seeded log; those artifacts are **not copied or
  re-owned**, and spills produced after the fork use the child session id"。
- `SpillSource { toolName, callId, label }`（types.ts:46-53）注释："**Not interpreted for
  access control**; purely descriptive"——来源字段不做权限。
- `suggestedName` 注释（types.ts:59-62）："The backend **sanitizes** it to a single safe path
  segment before use — **it is a hint, never a path**"。
- `SpillRef { locator, bytes, retrievalHint }`（types.ts:69-73）。

## A2. 本地后端的攻击面防御（spill-local/src/store.ts 全文 120 行）

- **私有根目录**（store.ts:27-30）：默认根 = OS tmpdir 下 `mkdtempSync('dsh-spill-')`，
  注释说明原因："Predictable world-readable paths would let other local users read spilled
  tool output **or pre-create symlinks**; mkdtemp gives an unpredictable suffix and 0700"。
- **单射路径编码**（store.ts:48-63 `encodeSegment`）：任意 UTF-16 字符串编码为安全路径段，
  `~XXXX` 十六进制转义可逆、不同输入永不碰撞；`.`/`..` 整体转义（store.ts:50-51）——
  **路径穿越在编码层被消灭**，不是靠黑名单过滤。
- **会话目录哈希**（store.ts:73-76）：`session-<sha256(sessionId)[:12]>`——目录名不含原始 id。
- **写入的防符号链接**（store.ts:107-120 `saveTextFile`）：文件名 = `randomBytes(6).hex +
  安全名`——"unpredictable (defeats symlink planting in a shared root) AND stays readable"；
  打开方式 `open(path, 'wx', 0o600)`——**排他 + owner-only**，注释："it fails on any existing
  path — symlink or not — so a pre-planted target cannot redirect the write"。目录 0700。

## A3. 策略插件：字节预算算法的精确实现（spill-policy/src/index.ts 全文 232 行）

- **加载期校验而非调用期**（index.ts:114-119）：负数/小数帽在插件加载时抛错——注释：
  "a negative/fractional cap would reach TextRetainer's assertBudget and throw, turning
  **every** oversized-result call into an isError. **A bad config must fail the deployment,
  not the tool**"。
- **跳过条件一行式**（index.ts:196-197）：`decision.kind !== 'accept' || hasOwn(decision,'value')
  || exec.parent !== undefined || exec.name === 'read'` ——非 accept、revalue、嵌套调用、
  read 工具四类直接过。
- **先 next() 再约束**（index.ts:190-194）：`const decision = await next()` 先让下游
  listener 定结果，"we bound whatever it accepted"；block 的纠正反馈原样通过——
  "spill only shapes accepted plain-text results, never corrective feedback"。
- **预留算法**（index.ts:163-175）：`reserve = byteLength(spillNotice({kind:'exact',
  count: totalBytes}, ref)) + 2`——注释解释为什么用 worst-case（全量字节数）定价：
  "its digit count bounds the real count's, so the reserved size is a safe upper bound"；
  `previewBudget = max(0, cap - reserve)`；预览头尾对半（index.ts:95-102：head =
  ceil(budget/2)、tail = floor(budget/2)）。
- **不变量与兜底**（index.ts:176-186）：`if (byteLength(replacedText) > cap) return undefined`
  ——注释："**the policy NEVER emits a replacement larger than the cap**…this one check
  subsumes 'not smaller than the original' too. The spill file already written is a
  harmless orphan; cleanup is deferred"。
- **best-effort 降级链**（index.ts:130-161）：无 session owner / 无 backend / saveText
  抛错 → warn + 返回 undefined（保留原文）。注释："**A spill failure must NEVER turn a
  successful tool call into an isError or hide the inline result**"。
- **第二臂：持久日志臂**（index.ts:217-231）：`tools/code-dispatch-log` 瀑布对
  `run_code` 子调用的**日志副本**套同一顶帽——注释："The program's returned value is
  untouched (it already crossed the worker boundary whole); only the session log's copy
  shrinks"；且 **read 子调用在这里不豁免**（index.ts:219-221）："the log copy is not
  model context, so the read → spill → read-again loop…cannot happen here, and read is
  precisely the tool that produces huge logs"。
- 两臂**共享同一个 `spillReplacement` 函数**（index.ts:127-129）："Shared verbatim by the
  model-facing post-execute arm and the durable dispatch-log arm so both produce
  **byte-identical projections**"。

# Part B 附件（attachment）

## B1. Service 抽象（attachment/src/index.ts 全文 63 行）

三个抽象方法的契约直接写在签名上（index.ts:34-59）：

- `validateImage`：注释 "Batch callers **validate every member before saving any member**"；
- `saveImage`："Validate and durably commit one image **before its owning session event is
  appended**"——先对象后事件；
- `readImage`："Read one image and **verify that bytes still match the recorded
  reference**"；`@throws the signal reason when aborted, or a storage error when
  verification fails"——取消保持为取消，不翻译成存储失败。

## B2. 引用结构（attachment/src/types.ts 全文 49 行）

- `ImageAttachmentRef`（types.ts:11-24）：`attachmentId` 注释 "Opaque storage identifier;
  **never a filesystem path or bearer URL**"；携带 mediaType（从存储字节验证而来）、
  bytes、width/height（内在像素尺寸）、name（"stripped of local path information"）。
- `ImageAttachmentLimits`（types.ts:27-33）：**四重限额**——单图字节、每消息图数、
  每消息图片总字节、最大像素数，外加 mediaTypes 白名单。上传准入与请求缓冲共用同一份。
- `SaveImageAttachment.mediaType` 注释（types.ts:39）："Caller-declared media type,
  **checked against fully decoded bytes**"——声明类型要过完整解码校验（防伪造扩展名）。
- `ImageMediaType`（types.ts:8）：v1 只收 png/jpeg/webp/gif。

# 落地映射（源码级）

| dsh 源码设计 | 落地（Python/PG/对象存储） |
|---|---|
| encodeSegment 单射编码（store.ts:48） | 存储键编码函数：任意字符串→安全段，转义可逆不碰撞 |
| `wx + 0o600` 排他写（store.ts:113） | 对象存储天然 create-only；本地文件用 `open('x')` + 0600 |
| 随机前缀防符号链接（store.ts:111） | 本地缓存路径加 secrets.token_hex 前缀 |
| 配置加载期 fail（policy index.ts:117） | 帽值校验放 FastAPI startup，不进调用路径 |
| worst-case 通知预留算法（policy index.ts:171） | `reserve = len(notice(total_bytes)) + 2`，逐字照抄 |
| 永不超帽 + 兜底保留原文（policy index.ts:183） | 同款 if：替换文本超帽→返回原文 |
| 溢出失败不降级成功调用（policy index.ts:34-35） | except 存储异常→log+原文，绝不转 isError |
| 日志臂与模型臂 byte-identical（policy index.ts:128） | 同一替换函数供 SSE 下发与事件落库共用 |
| 先 validate 全批再 save（attachment index.ts:39） | 多文件上传两阶段 |
| saveImage 先于事件 append（attachment index.ts:46） | 先 PUT 对象→拿 digest→再 INSERT 事件（同事务语义） |
| readImage digest 验证（attachment index.ts:53） | 读回时 sha256 校验 |
| 声明类型 vs 解码字节校验（types.ts:39） | Pillow 解码验证格式，不信 Content-Type |
| 四重限额（types.ts:27） | 上传 schema：单文件/单条数/总字节/像素 |
