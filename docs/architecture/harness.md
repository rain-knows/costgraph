# 架构/Harness

## 责任边界

`AgentHarness` 是无状态策略与工具控制器，负责规范化请求、构造最小上下文、计算能力交集、暴露允许的工具并在每次调用前重新校验。它不拥有 LangGraph State、Run 持久化、会话事务或金额公式。

## 角色与权限分类法（当前）

服务端主体角色为四个：`viewer`、`data_operator`、`data_approver`、`tenant_admin`。
`user`/`admin` 别名已删除，无兼容回退。角色 -> 权限是代码确定性映射（`ROLE_PERMISSIONS`）：

```text
viewer        -> cost_data.read
data_operator -> cost_data.read, data_management.read, data_import.submit
data_approver -> cost_data.read, data_management.read, data_import.approve, audit.read
tenant_admin  -> 全部 7 个权限
```

`PermissionId` 共 7 个：`cost_data.read`、`data_management.read`、`data_import.submit`、
`data_import.approve`、`iam.manage`、`data_scope.manage`、`audit.read`。
`ALL_PERMISSION_IDS` 覆盖全部 7 个；`_authorized_permissions()` 按角色并集计算。
`iam.manage` 与 `data_scope.manage` 已在分类法中冻结，但本批次没有对应的 IAM 接口，
见 [IAM 契约](../api/iam-contract.md)。

`ROLE_CAPABILITIES` 让四个角色都拥有 `system_help`、`cost_calculation`、`report_generation`；
`backend/app/agent/capabilities.py` 中 `CapabilitySpec.required_roles` 必须与之一致，
否则模块导入时 `_validate_capability_role_metadata()` 会抛 `RuntimeError`。运行时授权只用
一个权威角色映射，能力元数据不能单独授予权限。

`AGENT_SERVER_ALLOWED_PERMISSIONS` 不存在，`server_allowed_permissions` 保持全部 7 个权限。

## 身份来源

staging/production 主体只能来自 OIDC 访问令牌；本地 APP_ENV=demo 且显式开启 LOCAL_DEMO_AUTH_ENABLED 时，固定开发令牌映射到本地演示主体：

```text
Authorization: Bearer <token>
  -> OIDC 签名 / iss / aud / exp 校验（JWKS）
  -> sub 作为 principal_id，租户与角色取自受信 claim
  -> 与 cost_data.iam_principal_roles 的本地成员关系求交集
  -> cost_data.iam_data_scopes 读取数据范围
  -> ExecutionPrincipal(principal_id, tenant_id, roles, data_scope)
```

- 令牌中的角色本身不构成授权：只有同时存在于本地成员关系中的角色才生效。
- 数据范围随主体固化，Run 与导入任务只使用固化快照，Worker 不重新解析客户端身份。
- `AGENT_PRINCIPAL_ID`、`AGENT_TENANT_ID`、`AGENT_PRINCIPAL_ROLES`、`AGENT_ALLOWED_*`
  与 `get_server_principal()` 已删除，不留回退。
- 未登录或令牌非法返回 401（带 `WWW-Authenticate: Bearer`），已登录无权限返回 403，
  跨租户资源返回 404。三种语义不得互相冒充。
- OIDC 参数 (`OIDC_ISSUER`、`OIDC_AUDIENCE`、`OIDC_JWKS_URI`) 在 `staging`/`production`
  未配置时启动即失败；测试通过 FastAPI `dependency_overrides` 注入主体。

依据：`backend/app/auth/oidc.py`、`backend/app/auth/context.py`、`backend/app/auth/errors.py`、
`backend/app/repositories/postgres_iam_repository.py`。

## 授权门

数据治理 Router 使用独立 permission gate，不注册为 Agent tool；`/api/me` 只返回有效能力、
角色授权权限与数据范围摘要，不暴露 `tenant_id` 或配置秘密。

服务入口各自构造只请求所需权限的 `ExecutionContext` 并调用 `require_permission()`：

```text
/batches, /batches/{id}, /batches/{id}/errors, /published, /quality  -> data_management.read
GET  /imports, GET /imports/{job_id}, GET /imports/{job_id}/errors   -> data_management.read
POST /imports                                                       -> data_import.submit
POST /imports/{job_id}/decision                                     -> data_import.approve
POST /imports/{job_id}/cancel                                       -> data_import.submit
GET  /api/iam/principals                                            -> iam.manage
PUT  /api/iam/principals/{id}/roles                                 -> iam.manage
PUT  /api/iam/principals/{id}/scope                                 -> data_scope.manage
GET  /api/audit/events                                              -> audit.read
```

权限不足映射为稳定的 403；错误码按权限区分：`data_management_access_denied`、
`data_import_submit_access_denied`、`data_import_approve_access_denied`；
IAM 与审计接口统一使用 `permission_denied`。
客户端不能提交 principal、tenant、role、permission 或 data scope。

有效能力固定为：

```text
requested capabilities
  intersect principal role permissions
  intersect server allowlist
```

客户端能力列表只是请求，不能授予权限。当前注册能力为 `system_help`、`cost_calculation` 和 `report_generation`；能力元数据声明版本、角色、数据范围、风险和必需槽位。`report_generation.report_styles` 公开 `presentation/period_comparison` 两种报表形式，并依赖 `cost_calculation` 提供底层成本事实访问；缺少依赖时该能力不会进入有效集合。

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
- 周期对比使用两个不同自然月；只给目标月时，环比或普通对比确定性采用上一自然月，同比采用上年同月，两个期间相同时先澄清。两期数据分别通过相同工具 preflight 和 Repository 范围校验。
- 系统帮助请求不访问成本 Repository。
- 未授权映射为稳定的 `tool_access_denied`；未知工具或输入/输出契约错误映射为 `tool_validation_error`。
- 公开错误摘要不包含完整参数、SQL、路径、模型正文、密钥或成本来源记录 ID。业务成本详情的 `CostTraceGraph.records` 只在授权的成本详情响应中返回，不能进入 Agent 公开事件或模型 Trace。

数据治理查询与导入操作都在服务入口重新计算授权：查询固定请求 `data_management.read`，提交与取消固定请求 `data_import.submit`，审批固定请求 `data_import.approve`。从 checkpoint 恢复执行上下文时只接受与当前服务端主体一致的主体、角色、数据范围和 requested 集合，重新计算角色授权、server allowlist 和 DataScope；主体、租户、角色或数据范围不一致时 fail-closed，不能通过伪造 effective 字段扩大范围。`RuntimeRunRequest.principal` 是必填字段，运行边界不存在匿名主体。

依据：`backend/app/agent/harness.py`、`backend/app/agent/context.py`、`backend/app/agent/capabilities.py`、`backend/app/domain/authorization.py`、`backend/app/services/data_admin_service.py`、`backend/app/services/import_job_service.py`、`backend/app/services/iam_service.py`。

## 前端认证与权限呈现

- staging/production 登录使用 OIDC Authorization Code + PKCE；本地 APP_ENV=demo 可在前后端同时显式开启固定开发主体。两种路径的访问令牌只保存在内存，不写 localStorage、
  sessionStorage、cookie 或 URL。
- `GET /api/me` 返回的 `permissions` 决定导航可见性与路由可访问性；`/api/me` 不返回 `tenant_id`。
- 401 清空内存令牌并回到登录页；403 保留会话并渲染无权限面板；404 渲染不存在面板。
  三者不得退化成"空数据"。
- 配置项为 `VITE_OIDC_ISSUER`、`VITE_OIDC_CLIENT_ID`、`VITE_OIDC_REDIRECT_URI`、`VITE_OIDC_SCOPE`，
  见 `frontend/.env.example`。
