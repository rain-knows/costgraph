# API/数据治理契约

数据治理接口是当前服务端主体的只读管理视图，不属于 Agent 工具注册表，也不提供认证或多用户 IAM。主体由 `AGENT_PRINCIPAL_ID`、`AGENT_TENANT_ID` 和 `AGENT_PRINCIPAL_ROLES` 服务端配置注入。

## 授权

- 角色只有 `user` 与 `admin`；`user` 保留系统说明、成本查询和报表能力，`admin` 继承这些能力并拥有 `data_management.read`。
- 所有接口先从服务端主体构造请求 `data_management.read` 的 `ExecutionContext`，再查询；客户端不能提交 principal、tenant、role、permission 或 data scope。
- `user` 或其他无权限主体固定返回 `403`，错误码为 `data_management_access_denied`。
- 批次及错误明细以 `tenant_id + batch_id` 查询，跨租户和不存在统一返回 `404/data_admin_batch_not_found`。质量与发布统计使用同一租户的 published batch，并应用 ExecutionContext 的零件/期间 DataScope；批次元数据本身是租户级管理视图。

## 端点

| 方法 | 路径 | 返回 |
| --- | --- | --- |
| `GET` | `/api/me` | `principal_id`、角色、当前有效能力和角色授权权限；不返回 `tenant_id` 或秘密 |
| `GET` | `/api/data-admin/batches` | 分页批次元数据，可按 `status` 过滤；`page_size` 最大 100 |
| `GET` | `/api/data-admin/batches/{batch_id}` | 批次元数据和错误码聚合 |
| `GET` | `/api/data-admin/batches/{batch_id}/errors` | 分页错误码、来源表、来源记录、脱敏消息和时间；不返回 `raw_payload` |
| `GET` | `/api/data-admin/published` | 当前 published 批次、规则版本、DataScope 内事实行数、投影行数、期间和零件数 |
| `GET` | `/api/data-admin/quality` | 服务端确定性质量摘要；不由前端重算 |

成功响应均为 Pydantic `extra=forbid` 契约。数据库连接、查询失败、缺少当前规则版本投影或投影规则版本混合时返回统一 `503/database_unavailable` 外壳，并携带现有 request id。接口只读，不触发导入、发布、回滚或重算。

## 快照门禁

发布统计必须匹配当前 `CALCULATION_RULE_VERSION`，且 published batch 至少存在投影行；投影行必须全部绑定当前批次并使用同一当前规则版本。门禁失败时不返回部分可信统计。

## 代码与测试锚点

- Router：`backend/app/api/data_admin.py`、`backend/app/api/me.py`
- Service/schema：`backend/app/services/data_admin_service.py`、`backend/app/schemas/data_admin.py`
- Repository：`backend/app/repositories/postgres_data_admin_repository.py`
- 测试：`backend/tests/test_data_admin_api.py`、`backend/tests/test_baseline_and_boundaries.py`
