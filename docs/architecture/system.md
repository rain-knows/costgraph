# 架构/系统边界

## 产品定位

CostGraph 是制造业成本分析应用。当前产品用真实 PostgreSQL 成本事实支持产品期间总览、成本明细、Agent 问答和结构化报表。LLM 只负责意图理解、槽位建议和解释文本；产品、期间、数据范围、权限、金额、舍入和报表结构由服务端确定性代码、Pydantic 契约和数据库约束控制。

当前 Agent 能力只有：

- `system_help`：说明系统能力，不读取成本事实。
- `cost_calculation`：在产品和期间或日期范围完整后读取已发布事实并确定性计算。

生产批次交互、BOM/半成品层级、异常工单、审批、PDF/Excel 导出和多 Agent 不在当前实现中。

## 运行链路

```text
React/Vite
  -> FastAPI conversation/runtime API
     -> trusted principal + policy gate
     -> PostgreSQL run queue -> independent worker
        -> one LangGraph workflow
           -> RuntimeServices -> Harness -> registered provider/tools
           -> published CostRepository facts -> Decimal calculation
        -> checkpoint + ordered events + atomic finalization
     -> owner-scoped conversations and artifacts
  -> FastAPI read-only cost-data API
     -> owner/data-scope policy -> published CostRepository facts
     -> deterministic aggregation and period comparison
```

样例 JSON 只能由导入脚本写入新的 CostGraph 数据库；应用请求不以 JSON、SQLite 或内存产出作为运行时后端，也不读取迁移源数据库。

## 模块职责

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| `frontend/src` | 导航、查询、会话、SSE 状态、成本与报表展示 | 授权、金额计算、数据库直连 |
| `backend/app/api` | HTTP/SSE、Pydantic 参数校验、错误映射 | 最终授权和金额事实 |
| `backend/app/domain` | 主体、数据范围、成本和报表领域模型 | SQL 与 UI 状态 |
| `backend/app/services` | 会话、导入、计算、比较、追溯和产出用例 | 接受客户端金额或权限为事实 |
| `backend/app/agent` | Runtime/Harness、注册表、路由、澄清和 LangGraph 编排 | 绕过策略、生成可信金额、启动第二套循环 |
| `backend/app/repositories` | PostgreSQL 查询、owner 过滤、Run 租约和幂等写入 | 修改业务公式 |
| `backend/app/db` | SQLAlchemy 模型、连接与 schema 边界 | 业务流程编排 |
| `backend/app/worker.py` | Run 抢占、续租、重试、checkpoint 恢复和终态提交 | HTTP 身份认证或金额计算 |

## Agent 主流程

```text
load conversation context -> policy gate -> select route
  -> understand question -> merge slots -> clarification gate
  -> resolve product -> load published cost facts -> calculate
  -> build report -> final answer -> atomic artifact finalization
```

缺少唯一产品或 `period/date_range` 时，图在读取成本输入前返回 `needs_clarification`，且不创建 Artifact。`system_help` 路由不会读取成本事实。

## 核心不变量

- `effective_capabilities = requested ∩ role_permissions ∩ server_allowlist`。
- 所有金额使用 Python `Decimal` 计算；模型文本和前端计算不能覆盖结构化金额。
- 成本读取匹配租户、当前 `published` 快照、产品、期间/日期范围和服务端数据范围。
- 会话、Run、事件、Trace 和 Artifact 的业务读取匹配 `tenant_id + principal_id`。
- 同一会话同一时刻只有一个活跃 Run；同一租户的相同 `message_id` 与相同规范化请求复用 Run，不同请求冲突。
- LangGraph 是唯一工作流引擎；不引入 Agents SDK、MCP、A2A 或多 Agent 编排。
- PostgreSQL、Alembic head 或 Worker 不健康时 `runtime_ready=false`，不能创建持久化 Run。

Runtime、Harness、工具/模型和事件控制面分别见 [Runtime](runtime.md)、[Harness](harness.md)、[Tool 与 Provider](tool-provider.md) 与 [Event、Trace 与 Replay](event-trace-replay.md)。

代码锚点：`backend/app/main.py`、`backend/app/api/`、`backend/app/agent/`、`backend/app/services/`、`backend/app/repositories/`、`backend/app/db/models.py`、`backend/app/worker.py`。
