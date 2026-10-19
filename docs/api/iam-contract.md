# API/IAM 契约（批次 2 计划，尚未实现）

> 状态：**契约骨架，尚未实现**。本文只冻结批次 2 的目标接口形态，防止实现时出现第二套命名。
> 当前代码没有注册任何 `/api/iam/*` 或 `/api/audit/events` 路由，也没有 `iam_principal_roles`
> 与 `iam_data_scopes` 表。可核查依据：`backend/tests/test_baseline_and_boundaries.py` 的
> canonical API 路径集合与 `set(Base.metadata.tables)` 断言、`backend/app/api/` 下不存在
> IAM Router、`backend/app/db/models.py` 中不存在 IAM 表。
> 权威规格见 [二期批次 1 规格](../plans/data-governance-phase2-batch1-spec.md) 第 0 节：“不做 OIDC/JWKS 校验、
> `iam_principal_roles`/`iam_data_scopes` 表、PostgreSQL RLS、前端页面改动、`/api/iam/*` 与
> `/api/audit/events` 接口。”

## 当前事实（批次 1）

- 主体仍由服务端配置注入：`AGENT_PRINCIPAL_ID`、`AGENT_TENANT_ID`、`AGENT_PRINCIPAL_ROLES`，
  经 `get_server_principal()` 构造 `ExecutionPrincipal`；
  见 [Harness](../architecture/harness.md)。
- 角色与权限分类法已经是最终形态（4 角色 / 7 权限），批次 2 只替换身份来源，不改变分类法。
- 只有 `/api/me` 返回当前主体的非敏感授权摘要；没有主体、角色绑定或数据范围的写接口。
- 治理审计事件只通过 `cost_data.governance_audit_events` 追加写入，没有查询 API。

## 批次 2 计划接口（未实现）

以下路径、请求与响应形态是批次 2 的设计目标，**当前调用一律 404**：

| 方法 | 计划路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/iam/principals` | 列出当前租户主体及其角色绑定 |
| `POST` | `/api/iam/principals/{principal_id}/roles` | 绑定/解绑角色（需要 `iam.manage`） |
| `GET` | `/api/iam/data-scopes` | 列出主体级数据范围 |
| `PUT` | `/api/iam/data-scopes/{principal_id}` | 设置主体级零件/期间范围（需要 `data_scope.manage`） |
| `GET` | `/api/audit/events` | 分页查询治理审计事件（需要 `audit.read`） |

计划约束（实现前必须由后续规格重新冻结，本文不构成已实现事实）：

- OIDC/JWKS 校验替换配置主体；`get_server_principal()` 的配置路径随批次 2 一起删除，不留回退。
- `iam_principal_roles` / `iam_data_scopes` 表与 PostgreSQL RLS 由新迁移引入，Alembic 仍是结构唯一入口。
- 权限检查复用现有 `require_permission()` 与 7 个 `PermissionId`，不新增第二套角色名。
- `/api/audit/events` 只返回 `governance_audit_events` 的非敏感字段（计数、错误码、状态、主体、时间），
  永不返回原始载荷、令牌或连接串；审计仍只追加，不提供 UPDATE/DELETE。
- 错误外壳沿用 `{"detail": {"code", "message", "request_id", "details"}}` 与 `X-Request-ID`。

## 相关文档

- 已实现的角色/权限分类法与授权门：[Harness](../architecture/harness.md)
- 已实现的数据治理接口：[数据治理契约](data-admin-contract.md)
- 当前已完成批次与后续批次划分：[数据管理面板与权限治理](../plans/data-management-and-permission-governance.md)、
  [二期批次 1 权威规格](../plans/data-governance-phase2-batch1-spec.md)
