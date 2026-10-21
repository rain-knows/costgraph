# API/IAM 契约

身份、角色绑定、数据范围与审计查询的当前接口事实。权威代码：
`backend/app/auth/`、`backend/app/api/iam.py`、`backend/app/api/audit.py`、
`backend/app/services/iam_service.py`、`backend/app/repositories/postgres_iam_repository.py`、
`backend/app/schemas/iam.py`。

## 身份边界

- 所有 `/api/*` 受保护接口要求 `Authorization: Bearer <access_token>`。
- 本地 `APP_ENV=demo` 且显式开启 `LOCAL_DEMO_AUTH_ENABLED=true` 时，固定本地演示令牌
  `costgraph-local-demo` 映射到 `local-costgraph-user`、`local-costgraph-tenant`、
  `tenant_admin` 与空数据范围；这是开发启动路径，不适用于 staging/production。
- 令牌由 `backend/app/auth/oidc.py` 校验：签名（RS256/RS384/RS512/ES256/ES384，经 JWKS）、
  `iss`、`aud`、`exp`/`iat`、以及必需 claim `exp, iat, iss, aud, sub`。
- `sub` 映射 `principal_id`；租户与角色来自受信 claim（`OIDC_TENANT_CLAIM`、`OIDC_ROLES_CLAIM`），
  再与本地 `cost_data.iam_principal_roles` 的成员关系求交集。令牌里的角色本身不构成授权。
- 主体数据范围来自 `cost_data.iam_data_scopes`，随 `ExecutionPrincipal` 一起固化；
  Run 与导入任务只使用固化的主体快照，Worker 不重新解析客户端身份。
- 配置主体已删除：`AGENT_PRINCIPAL_ID`、`AGENT_TENANT_ID`、`AGENT_PRINCIPAL_ROLES`、
  `AGENT_ALLOWED_PART_IDS`、`AGENT_ALLOWED_PERIOD_START`、`AGENT_ALLOWED_PERIOD_END`
  与 `get_server_principal()` 均不存在，没有配置回退路径。

### 状态码语义

| 场景 | 状态码 | `code` |
| --- | --- | --- |
| 缺少或非法 Bearer 头 | 401（带 `WWW-Authenticate: Bearer`） | `missing_bearer_token` |
| 令牌签名 / issuer / audience / 过期 / claim 不合法 | 401 | `invalid_token` |
| 已登录但不属于该租户 | 403 | `principal_not_in_tenant` |
| 已登录但未被分配任何角色 | 403 | `principal_has_no_role` |
| 已登录但缺少所需权限 | 403 | `permission_denied` |
| 目标主体不属于当前租户 | 404 | `iam_principal_not_found` |

## 当前接口

错误外壳与其他接口一致：`{"detail": {"code", "message", "request_id", "details"}}`，响应带 `X-Request-ID`。

### `GET /api/me`

返回当前主体的非敏感授权摘要，**不返回** `tenant_id`：

```json
{
  "principal_id": "user-1",
  "roles": ["data_approver"],
  "capabilities": ["system_help", "cost_calculation", "report_generation"],
  "permissions": ["cost_data.read", "data_management.read", "data_import.approve", "audit.read"],
  "data_scope": {
    "source": "postgresql_cost_data",
    "allowed_part_ids": ["FG-001"],
    "allowed_period_start": "2026-01",
    "allowed_period_end": "2026-12"
  }
}
```

`permissions` 是该主体角色矩阵的授权并集；前端按它显示导航与拦截直接访问。

### `GET /api/iam/principals`

需要 `iam.manage`。分页参数 `page`（≥1，默认 1）、`page_size`（1..100，默认 20）。

```json
{
  "items": [
    {
      "principal_id": "user-1",
      "roles": ["data_operator"],
      "data_scope": {"allowed_part_ids": [], "allowed_period_start": null, "allowed_period_end": null},
      "updated_by": "admin-1",
      "updated_at": "2026-09-18T10:00:00Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

数据范围为空集表示该主体未被收窄；只允许管理员通过下述接口显式收窄。

### `PUT /api/iam/principals/{principal_id}/roles`

需要 `iam.manage`。请求体：

```json
{ "roles": ["data_operator", "data_approver"] }
```

- 角色取值固定为 `viewer`、`data_operator`、`data_approver`、`tenant_admin`，非空且去重后写库。
- 该接口同时是该租户新增主体成员关系的入口（upsert `iam_principal_roles`）。
- 成功后返回该主体的完整 [主体表示](#get-apiiamprincipals)。
- 追加审计事件 `iam.roles_updated`。

### `PUT /api/iam/principals/{principal_id}/scope`

需要 `data_scope.manage`。请求体：

```json
{
  "allowed_part_ids": ["FG-001", "FG-002"],
  "allowed_period_start": "2026-01",
  "allowed_period_end": "2026-12"
}
```

- 期间格式固定 `YYYY-MM`；开始不得晚于结束；违反返回 422。
- 主体必须是该租户已有成员，否则 404 `iam_principal_not_found`。
- `allowed_part_ids` 去重后写库；两个期间字段可为 `null` 表示该侧不限制。
- 追加审计事件 `iam.scope_updated`。

### `GET /api/audit/events`

需要 `audit.read`。参数：`page`、`page_size`、`actor_principal_id`（精确匹配）、`action`（精确匹配）。

```json
{
  "items": [
    {
      "event_id": 42,
      "job_id": "3f1c…",
      "batch_id": null,
      "actor_principal_id": "approver-1",
      "action": "approved",
      "target_type": "import_job",
      "target_id": "3f1c…",
      "outcome": "success",
      "request_id": "7b2d…",
      "detail": {"reason": "校验结果已复核"},
      "created_at": "2026-09-18T10:05:00Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

允许的 `action` 取值由 `ck_governance_audit_events_action` 约束：`submitted`、`validated`、
`validation_failed`、`approved`、`rejected`、`publishing`、`published`、`publish_failed`、
`cancelled`、`validation_retry_scheduled`、`publish_retry_scheduled`、
`iam.roles_updated`、`iam.scope_updated`。失败与重试排期事件的 `detail` 仅记录错误码、
状态、`retryable`、`retry_scheduled` 与尝试次数，不保存异常文本或载荷。

审计事件只追加：`cost_data.governance_audit_events` 上的触发器拒绝 `UPDATE`/`DELETE`；
接口永不返回访问令牌、数据库连接串或导入原始载荷。

## 角色与权限矩阵

| 角色 | 权限 |
| --- | --- |
| `viewer` | `cost_data.read` |
| `data_operator` | `cost_data.read`、`data_management.read`、`data_import.submit` |
| `data_approver` | `cost_data.read`、`data_management.read`、`data_import.approve`、`audit.read` |
| `tenant_admin` | 全部 7 项，含 `iam.manage`、`data_scope.manage`、`audit.read` |

四个角色都保留 `system_help`、`cost_calculation`、`report_generation` 三个 Agent 能力；
权限与能力的映射由 `backend/app/domain/authorization.py` 的 `ROLE_PERMISSIONS` /
`ROLE_CAPABILITIES` 唯一决定。

## 前端约定

- 访问令牌只保存在内存，不写 `localStorage`/`sessionStorage`/cookie。
- 采用 Authorization Code + PKCE 换取令牌。
- 401 清理内存令牌并回到未登录态；403 显示无权限页；404 显示不存在，不得伪装成空数据。
- 路由权限映射在 `frontend/src/auth/permissions.ts` 的 `ROUTE_PERMISSIONS`，按最长前缀匹配：
  `/data-admin` 与 `/data-admin/imports` 需 `data_management.read`，
  `/data-admin/imports/new` 需 `data_import.submit`，`/iam` 需 `iam.manage`，`/audit` 需 `audit.read`。
  映射只决定导航可见性与页面可达性，服务端仍是权威边界。
- 客户端封装：`frontend/src/api/iam.ts`（`listIamPrincipals`、`setPrincipalRoles`、
  `setPrincipalScope`）与 `frontend/src/api/audit.ts`（`listAuditEvents`）。
  `setPrincipalScope()` 发送平铺的 `allowed_part_ids`/`allowed_period_start`/`allowed_period_end`，
  不包 `data_scope` 外壳；期间格式 `YYYY-MM`，前端在起止颠倒时先拦截且不发请求。
- IAM 页面（`frontend/src/pages/IamPage.tsx`）未收窄的范围必须显示为“全部范围”，不得显示为空白；
  审计页面（`frontend/src/pages/AuditPage.tsx`）只渲染 `detail` 的一层键值摘要，筛选条件写入 URL。

## 相关文档

- 授权门与策略边界：[Harness](../architecture/harness.md)
- 数据治理接口：[数据治理契约](data-admin-contract.md)
- 表结构与存储：[数据字典](../data/data-dictionary.md)、[存储](../data/storage.md)
- 运行配置与排障：[运行手册](../operations/runbook.md)
