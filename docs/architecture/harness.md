# 架构/Harness

## 责任边界

`AgentHarness` 是无状态策略与工具控制器，负责规范化请求、构造最小上下文、计算能力交集、暴露允许的工具并在每次调用前重新校验。它不拥有 LangGraph State、Run 持久化、会话事务或金额公式。

有效能力固定为：

```text
requested capabilities
  intersect principal role permissions
  intersect server allowlist
```

客户端能力列表只是请求，不能授予权限。当前注册能力仅为 `system_help` 和 `cost_calculation`；能力元数据声明版本、角色、数据范围、风险和必需槽位。

## 上下文与策略门

Harness 从服务端注入的 `ExecutionPrincipal`、会话槽位和有界最近消息构造 `ExecutionContext` 与 `RuntimeContext`。主体和租户不是模型或客户端参数，工具输入不能覆盖 Runtime 保留字段。

工具 preflight 固定顺序：

1. 工具已注册且启用。
2. 主体拥有工具要求的全部有效能力。
3. 当前执行上下文满足工具声明的数据范围。
4. 参数不含 Runtime 保留字段。
5. 输入满足 Draft 2020-12 JSON Schema。

读取成本事实的工具声明 `published_cost_data`。Harness 提供调用前策略门，Repository 仍是租户、已发布快照和产成品零件/期间范围的最终数据边界，两者不能互相替代。

## 澄清与失败

- 成本计算缺少唯一产成品零件或 `period` 时，必须先返回澄清；不得读取成本输入、执行卷积或创建 Artifact。
- 系统帮助请求不访问成本 Repository。
- 未授权映射为稳定的 `tool_access_denied`；未知工具或输入/输出契约错误映射为 `tool_validation_error`。
- 公开错误摘要不包含完整参数、SQL、路径、模型正文、密钥或成本来源记录 ID。业务成本详情的 `CostTraceGraph.records` 只在授权的成本详情响应中返回，不能进入 Agent 公开事件或模型 Trace。

依据：`backend/app/agent/harness.py`、`backend/app/agent/context.py`、`backend/app/agent/capabilities.py`、`backend/app/domain/authorization.py`。
