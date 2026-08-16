# biz-agent-platform

对话式业务组件平台 monorepo。用户在聊天窗口用自然语言提出业务需求，模型输出文字 +
结构化事件，前端用预设业务组件渲染（如财务填报表单）；组件交互成为对话上下文记忆，
可基于填报内容派发后续任务。

**架构思想借鉴 DeepSeek Harness**（一切皆插件、append-only 会话事件日志、事件族驱动
组件渲染），采用 React 19 前端壳 + Python/FastAPI/LangGraph 后端的轻量自研实现。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/design/` | **开发设计文档（7 份，开发的直接依据）** |
| `docs/reference/` | 设计依据：dsh 源码考古文档（13 份）+ 技术方案 PLAN v1.1 |
| `apps/backend/` | Python 3.12 + FastAPI + LangChain/LangGraph + PG（M1 建） |
| `apps/frontend/` | React 19 + TS + Vite + Zustand（M1 建） |
| `packages/contracts/` | JSON Schema 单一事实源 + pydantic/TS 双端生成（M1 建） |
| `plugins/` | 业务插件目录（contract/frontend/backend 三件套，M2 起） |

## 阅读顺序

1. `docs/design/02-数据模型与事件词表.md`（地基）
2. `docs/design/01-系统架构与目录设计.md`（总纲 + agent_harness 复用清单）
3. `docs/design/03-接口协议设计.md` → `05-插件契约与加载器.md` → `04-前端设计.md`
4. `docs/design/07-七项关键约束实现规范.md`（红线规则）
5. `docs/design/06-实施计划与任务分解.md`（任务卡，从这里开工）

设计决策的源码级依据见 `docs/reference/`（dsh-01~13 为 DeepSeek Harness 源码精读）。

## 状态

设计阶段（2026-08）。代码脚手架从 06 文档的 M1 W1-1 任务卡开始。
