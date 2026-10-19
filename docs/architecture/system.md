# 架构/系统边界

## 产品定位

CostGraph 是制造业成本分析应用。当前产品用 PostgreSQL 中已发布的成本事实支持产成品批次总览、三套成本视图、批次成本追溯、Agent 问答和结构化报表。LLM 只负责意图理解、槽位建议和解释文本；零件、期间、批次关系、数据范围、权限、金额、舍入和报表结构由服务端确定性代码、Pydantic 契约和数据库约束控制。

当前 Agent 能力只有：

- `system_help`：说明系统能力，不读取成本事实。
- `cost_calculation`：在产成品零件和期间完整后读取已发布事实并确定性卷积最终批次。
- `report_generation`：识别明确报表意图，在 `cost_calculation` 数据能力之上生成展示型或周期对比型结构化产出。

当前批次图表达真实发生的购置、工艺和领用关系，不是计划 BOM 或库存台账。库存结存、返工分支、副产品、异常工单、审批、PDF/Excel 导出和多 Agent 不在当前实现中。

## 运行链路

```text
React/Vite
  -> FastAPI conversation/runtime API
     -> trusted principal + policy gate
     -> PostgreSQL run queue -> independent worker
        -> one LangGraph workflow
           -> RuntimeServices -> Harness -> registered provider/tools
           -> published CostRepository cost projection -> deterministic aggregation
        -> checkpoint + ordered events + atomic finalization
     -> owner-scoped conversations and artifacts
  -> FastAPI read-only cost-data API
     -> owner/data-scope policy -> published CostRepository cost projection
     -> deterministic DAG validation, convolution and aggregation
  -> FastAPI read-only data-admin API (admin/data_management.read)
     -> tenant-scoped batch metadata, error summaries and published quality snapshot
  -> FastAPI data-admin import API (data_import.submit / data_import.approve)
     -> PostgreSQL import job queue -> independent worker
        -> validate payload -> approval gate -> publish immutable snapshot
        -> facts + projections + published status + append-only audit in one transaction
```

样例 JSON 只能由导入脚本写入新的 CostGraph 数据库；应用请求不以 JSON、SQLite 或内存产出作为运行时后端，也不读取迁移源数据库。当前规范样例使用汽车装饰件工厂数据，黄金批次通过原料购置、注塑成型、表面包覆和总成装配事件形成多层实际投入图。

## 模块职责

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| `frontend/src` | 导航、查询、会话、SSE 状态、成本与报表展示 | 授权、金额计算、数据库直连 |
| `backend/app/api` | HTTP/SSE、Pydantic 参数校验、错误映射 | 最终授权和金额事实 |
| `backend/app/domain` | 主体、数据范围、48 项费用代码、成本卷积和报表领域模型 | SQL 与 UI 状态 |
| `backend/app/services` | 会话、导入、DAG 校验、卷积、追溯、产出用例和导入任务状态机 | 接受客户端金额、图关系或权限为事实 |
| `backend/app/agent` | Runtime/Harness、注册表、路由、澄清和 LangGraph 编排 | 绕过策略、生成可信金额、启动第二套循环 |
| `backend/app/repositories` | PostgreSQL 查询、owner 过滤、Run/ImportJob 租约和幂等写入 | 修改业务公式 |
| `backend/app/db` | SQLAlchemy 模型、连接与 schema 边界 | 业务流程编排 |
| `backend/app/worker.py` | Run 与 ImportJob 抢占、续租、重试、checkpoint 恢复、校验和发布终态提交 | HTTP 身份认证或金额计算 |

## Agent 主流程

```text
load conversation context -> policy gate -> select route
  -> understand question -> merge slots -> clarification gate
  -> resolve finished part -> load published part-period projections -> aggregate batches
  -> build report -> final answer -> atomic artifact finalization
```

缺少唯一产成品零件或 `period` 时，图在读取成本投影前返回 `needs_clarification`，且不创建 Artifact。期间按最终批次的 `completion_time` 归属；`system_help` 路由不会读取成本事实。明确报表词进入 `report_generation`，普通成本请求进入 `cost_calculation`；报表意图进一步解析为 `presentation` 或 `period_comparison`，对比型在同一权限和数据范围内分别读取两期投影。报表分析 Provider 只接收零件、期间、确定性计算结果和状态栏，Decimal 事实按原精度序列化为字符串；报告模型接收成本批次的 `datetime` 并在公开 JSON 中输出时间字符串。

## 核心不变量

- `effective_capabilities = requested ∩ role_permissions ∩ server_allowlist`。
- 主体身份在运行边界绑定：Run 创建时固化 `principal_id`、`tenant_id`、`roles` 与 `data_scope`，主体来自 OIDC 访问令牌并与本地成员关系求交集；Worker 只用该快照，不重新解析客户端身份。`RuntimeRunRequest.principal` 是必填字段，不存在匿名主体。从状态恢复上下文时 `execution_context_from_state()` 重新推导授权，只有传入 `authoritative_principal` 才做身份一致性校验（包括 `data_scope`）；API 传当前主体，Worker 传 Run 中固化的主体。
- 每个 `batch_id` 是不可变快照：成本事实与投影表把批次纳入主键，同一逻辑 ID 不再跨批次覆盖；旧 `published` 快照只在新快照事务提交后变为 `superseded`。
- 导入任务状态机固定为 `submitted -> validating -> validation_failed | approval_pending -> approved | rejected -> publishing -> published | publish_failed`；`submitted`、`validating`、`approval_pending` 可取消，发布失败保留原已发布快照并允许同一已批准任务重试，不提供回滚接口。
- HTTP 导入接口只创建任务或改变状态；校验、投影生成和发布全部由 Worker 在单事务中完成，未完成审批的任务不可发布。
- 所有金额使用 Python `Decimal` 计算；模型文本和前端计算不能覆盖结构化金额。
- 成本读取匹配租户、当前 `published` 快照、产成品零件、期间和服务端数据范围。
- 一个购置或工艺事件只生成一个输出批次；事件投入边组成无环图，累计金额只由服务端按拓扑序计算，不作为第二份事实落库。
- 会话、Run、事件、Trace 和 Artifact 的业务读取匹配 `tenant_id + principal_id`。
- 同一会话同一时刻只有一个活跃 Run；同一租户的相同 `message_id` 与相同规范化请求复用 Run，不同请求冲突。
- LangGraph 是唯一工作流引擎；不引入 Agents SDK、MCP、A2A 或多 Agent 编排。
- PostgreSQL、Alembic head 或 Worker 不健康时 `runtime_ready=false`，不能创建持久化 Run。

Runtime、Harness、工具/模型和事件控制面分别见 [Runtime](runtime.md)、[Harness](harness.md)、[Tool 与 Provider](tool-provider.md) 与 [Event、Trace 与 Replay](event-trace-replay.md)。

代码锚点：`backend/app/main.py`、`backend/app/api/`、`backend/app/agent/`、`backend/app/services/`、`backend/app/repositories/`、`backend/app/db/models.py`、`backend/app/worker.py`。

## 前端认证与权限呈现

- 登录使用 OIDC Authorization Code + PKCE；访问令牌只保存在内存，不写 localStorage、
  sessionStorage、cookie 或 URL，刷新页面即回到未登录态。
- \GET /api/me\ 返回的 \permissions\ 决定导航可见性与路由可访问性；\/api/me\ 不返回 \	enant_id\。
- 401 清空内存令牌并回到登录页；403 保留会话并渲染无权限面板；404 渲染不存在面板。
  三者不得退化成\"空数据\"。
- 配置项为 \VITE_OIDC_ISSUER\、\VITE_OIDC_CLIENT_ID\、\VITE_OIDC_REDIRECT_URI\、\VITE_OIDC_SCOPE\，
  见 \rontend/.env.example\。
