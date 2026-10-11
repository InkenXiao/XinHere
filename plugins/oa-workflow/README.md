# oa-workflow 插件

企业级 OA 最小版：待办 + 审批流 + 个人任务派发处理，作为可插拔模块挂在 AI 问话主流程。

## 插件三问（SOP：docs/design/05-插件契约与加载器.md）

1. **做什么**：OA 审批单（请假 oa_leave / 报销 oa_expense / 通用 oa_generic，单审批人：
   申请人提交 → 模板 approver_role 解析的审批人 通过/退回）、OA 任务派发与个人待办、
   AI 对话内闭环（4 个 oa_* 工具 + 3 种确认卡）与独立管理页（前端 /oa）。
2. **拥有什么**：不拥有任何表——任务数据复用平台 BizTask/BizTodo（scene="oa"）与 todo_svc 内核；
   权限直连业务库 sii_eosts（BIZ_PG_URI）读芋道/若依标准表：system_users/system_dept/
   system_role/system_user_role/system_role_menu/system_menu.permission
   （perms 串 + super_admin 旁路 + data_scope 1-5），菜单授权经 scripts/seed_oa_menus.py 种子。
3. **怎么移除**：见下方移除清单；平台侧 4 处通用机制（loader 开关/提示词拼接/插件 router
   挂载/卡处理器注册点）不含 OA 语义，无插件时为空操作。

## 权限模型（若依/芋道全模式，直连业务库 sii_eosts）

- 数据源：`system_users / system_dept / system_role（code/data_scope/data_scope_dept_ids）/
  system_user_role / system_role_menu / system_menu.permission`——无任何内存 mock；
  菜单与授权由 `apps/backend/scripts/seed_oa_menus.py` 幂等种子（可 `--rollback`）。
- 角色（真实 code）→ OA 权限：
  - `XT_BBBMGL` 本部部门管理者 / `XTBM-JCB` / `XT_CWYJSH` / `BBCWRY` → 全量（含派发、审批）
  - `QYYYRE` 企业运营人员 → 任务查看 + 发起申请
  - `QYCWRE` 企业财务人员 → 审批（报销单审批人，模板 approver_role）+ 申请
- `super_admin`（admin）→ `*:*:*` 全权限旁路
- 权限串：`oa:task:list/dispatch`、`oa:approval:list/audit`、`oa:request:list/submit`；
  数据范围 data_scope 1-5（自定义范围读 system_role.data_scope_dept_ids）
- 模板审批人：`oa_leave/oa_generic` → `XT_BBBMGL`，`oa_expense` → `QYCWRE`
  （语义别名 dept_leader/finance 仍可用，见 perm.APPROVER_ROLE_ALIASES）

## 移除清单

1. 删除 `plugins/oa-workflow/` 目录（含模板 JSON：task-templates 自动发现，删目录即失效）
2. 删除前端 `src/pages/oa/` 与 `src/plugins/Oa{DispatchConfirm,Request,Audit}Card.tsx`
3. 回退标记 `# --- oa-workflow ---` 的注册行：
   - `apps/frontend/src/registry/manifest.ts`（3 行 import + 3 行数组项）
   - `apps/frontend/src/router/index.tsx`（1 行路由）+ `TopBar.tsx`（1 行导航）
   - `apps/frontend/src/pages/work/components/HistoryRail.tsx`（oa 会话分组映射）
   - `apps/backend/app/services/skills.py`（SESSION_TYPE_TOOLS 的 oa 条目）
4. 平台通用机制保持不动（loader.py 的 agent_plugins/plugin_agent_prompt/plugin_routers、
   executor.py 的 plugin_tools 装配、main.py 的 plugin_routers 挂载循环、
   component_handlers.register）——无 agent_tools 插件时均为空操作，主流程零感知。

历史数据说明：已产生的 BizTask/BizTodo（scene="oa"）留在库中不阻塞移除；
`GET /api/v1/tasks/templates` 在插件存在期间会列出 OA 模板（平台模板目录不过滤 scene）。
