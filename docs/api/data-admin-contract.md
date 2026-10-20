# API/数据治理契约

`/api/data-admin` 是服务端主体的数据治理面：批次与质量只读查询，以及导入任务的提交、查询、
审批与取消。它不属于 Agent 工具注册表，也不承担身份认证；主体由 OIDC Bearer 令牌构造，
本地 `APP_ENV=demo` 且显式开启本地演示认证时才使用固定开发主体，见 [Harness](../architecture/harness.md)。导入任务状态机与不可变快照规则见
[治理与质量](../data/governance.md)，Worker 调度见 [Runtime](../architecture/runtime.md)。

## 授权

- 角色为 `viewer` / `data_operator` / `data_approver` / `tenant_admin`，权限为 7 个
  `PermissionId`；没有 `user`/`admin` 别名。
- 每个接口在服务入口构造只请求所需权限的 `ExecutionContext` 并调用 `require_permission()`；
  客户端不能提交 principal、tenant、role、permission 或 data scope。
- 权限不足固定 `403`，错误码按权限区分：`data_management_access_denied`（读取）、
  `data_import_submit_access_denied`（提交/取消）、`data_import_approve_access_denied`（审批）。
- 批次、任务与错误明细以 `tenant_id` 查询，跨租户和不存在统一返回 `404`：
  `data_admin_batch_not_found`（批次）、`import_job_not_found`（任务）。
  质量与发布统计使用同一租户的 published batch，并应用 ExecutionContext 的零件/期间 DataScope。
- 认证使用 OIDC Bearer 令牌（见 [IAM 契约](iam-contract.md)），API 以受限角色
  `costgraph_app` 连接 PostgreSQL，因此仓储的 `WHERE tenant_id` 与数据库 RLS 同时生效；
  跨租户读取在数据库层也会被策略拦成 0 行。

## 只读端点

| 方法 | 路径 | 权限 | 返回 |
| --- | --- | --- | --- |
| `GET` | `/api/me` | 无 | `principal_id`、角色、当前有效能力和角色授权权限；不返回配置秘密 |
| `GET` | `/api/data-admin/batches` | `data_management.read` | 分页批次元数据，可按 `status` 过滤；`page_size` 最大 100 |
| `GET` | `/api/data-admin/batches/{batch_id}` | `data_management.read` | 批次元数据和错误码聚合 |
| `GET` | `/api/data-admin/batches/{batch_id}/errors` | `data_management.read` | 分页错误码、来源表、来源记录、脱敏消息和时间；不返回 `raw_payload` |
| `GET` | `/api/data-admin/published` | `data_management.read` | 当前 published 批次、规则版本、DataScope 内事实行数、投影行数、期间和零件数 |
| `GET` | `/api/data-admin/quality` | `data_management.read` | 服务端确定性质量摘要；不由前端重算 |

批次响应新增可选 `job_status` 字段（快照状态与任务状态同时展示），既有字段不删除。
批次的 `job_status` 通过任务 `job_id` / `published_batch_id` 与该批次关联：已发布批次的
`batch_id` 等于任务的 `published_batch_id`，校验失败批次的 `batch_id` 等于任务 `job_id`。

## 导入任务端点

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/data-admin/imports` | `data_import.submit` | 创建导入任务 |
| `GET` | `/api/data-admin/imports` | `data_management.read` | 分页任务列表，可按 `status` 过滤 |
| `GET` | `/api/data-admin/imports/{job_id}` | `data_management.read` | 任务详情与 `error_codes` 聚合 |
| `GET` | `/api/data-admin/imports/{job_id}/errors` | `data_management.read` | 任务错误明细分页；不返回 `raw_payload` |
| `POST` | `/api/data-admin/imports/{job_id}/decision` | `data_import.approve` | 审批：`approve` / `reject` |
| `POST` | `/api/data-admin/imports/{job_id}/cancel` | `data_import.submit` | 取消任务 |

### `POST /api/data-admin/imports`

请求头必须带 `Idempotency-Key`（1–128 字符，去除首尾空白后非空）；缺失、空白或超长返回
`422/idempotency_key_required`，`details` 携带 `max_length`。

```json
{
  "source_system": "mes",
  "source_file_name": "cost_events.json",
  "calculation_rule_version": "cost-v1",
  "payload": {"parts": [], "cost_events": [], "cost_event_inputs": [], "cost_records": []}
}
```

- `payload` 只允许 `parts`、`cost_events`、`cost_event_inputs`、`cost_records` 四个键；
  出现其他键返回 `422/import_payload_invalid`，`details.unknown_tables` 列出未知键。
- `payload` 写入本地内容寻址对象存储，数据库只保存 `source_object_key` / `source_object_size` /
  `source_object_sha256`；接口**永不返回** `payload` 或对象内容。
- `snapshot_hash` 是归一化载荷的规范化哈希，用于第二次幂等判定。

响应（HTTP 200，`ImportJobSubmission`）：

```json
{
  "job_id": "…", "status": "submitted", "duplicate": false,
  "source_system": "mes", "source_file_name": "cost_events.json",
  "source_object_key": "imports/ab/<sha256>", "source_object_size": 123,
  "source_object_sha256": "<64 hex>", "snapshot_hash": "<64 hex>",
  "calculation_rule_version": "cost-v1", "total_rows": 0, "created_at": "…"
}
```

幂等（两层，均由数据库唯一约束确定性保证）：

1. 同租户 + 同 `idempotency_key` 复用已有任务。
2. 同租户 + 同 `source_system` + 同 `snapshot_hash` + 同 `calculation_rule_version` 复用已有任务。

重复提交返回已有任务且 `duplicate: true`，不得新建任务，也不追加新的 `submitted` 审计事件。

每次状态变化都追加一条 `governance_audit_events`；该表由
`trg_governance_audit_events_append_only` 触发器禁止 UPDATE/DELETE，
接口也不提供修改历史事件的路径。

### `POST /api/data-admin/imports/{job_id}/decision`

```json
{"decision": "approve", "reason": "校验结果已复核"}
```

- `decision` 取 `approve` / `reject`；`reason` 可选，最长 500 字符，必须脱敏且不含载荷。
- 单管理员审批：拥有 `data_import.approve` 即可批准或拒绝，不强制双人复核。
- 仅 `approval_pending` 状态可决策；其他状态返回 `409/import_job_state_conflict`，
  `details` 携带 `current_status` 与 `target_status`。

### `POST /api/data-admin/imports/{job_id}/cancel`

仅 `submitted`、`validating`、`approval_pending` 可取消；`approved`、`publishing` 及终态不可取消，
非法状态返回 `409/import_job_state_conflict`。

### 任务响应

`ImportJob` 返回 `job_id`、`tenant_id`、`principal_id`、`status`、来源系统与 basename、
对象引用、`snapshot_hash`、规则版本、三个计数、`error_summary`、审批字段
（`decision` / `decision_reason` / `decided_by` / `decided_at`）、`published_batch_id`、
`attempt_count`、`created_at` / `started_at` / `finished_at` 与 `duplicate`。
租约内部字段（`lease_owner` / `lease_expires_at` / `available_at`）和 `payload` 不出现在响应里。
详情响应额外返回 `error_codes: [{"error_code", "count"}]`。
Worker 对瞬时故障最多执行 4 次（含首次领取），使用 `available_at` 退避；达到上限后进入失败终态。发布的新快照、旧快照退役、任务 `published` 状态与成功审计在同一事务提交。

## 错误码

| HTTP | 错误码 | 触发条件 |
| --- | --- | --- |
| `401` | — | 未登录或令牌无效；本地 demo 也必须显式携带固定 Bearer 令牌 |
| `403` | `data_management_access_denied` | 缺少 `data_management.read` |
| `403` | `data_import_submit_access_denied` | 缺少 `data_import.submit` |
| `403` | `data_import_approve_access_denied` | 缺少 `data_import.approve` |
| `404` | `data_admin_batch_not_found` / `import_job_not_found` | 不存在或不属于当前租户（不泄露存在性） |
| `409` | `import_job_state_conflict` | 状态机非法迁移（审批、取消、Worker 租约推进） |
| `422` | `idempotency_key_required` / `import_payload_invalid` | 缺少或非法 `Idempotency-Key`；载荷含未知记录集合 |
| `503` | `database_unavailable` | 数据库不可用、投影缺失或规则版本混合 |

所有错误响应沿用 `{"detail": {"code", "message", "request_id", "details"}}` 外壳与
`X-Request-ID` 行为，`details` 不包含 SQL、路径、密钥、原始载荷或完整来源记录。

## 快照门禁

发布统计必须匹配当前 `CALCULATION_RULE_VERSION`，且 published batch 至少存在投影行；
投影行必须全部绑定当前批次并使用同一当前规则版本。门禁失败时不返回部分可信统计，
固定返回 `503/database_unavailable`。

历史 `superseded` 批次不被描述为可回滚目标：仓库不提供快照切换、回滚或重算接口。

## 代码与测试锚点

- Router：`backend/app/api/data_admin.py`、`backend/app/api/me.py`
- Service/schema：`backend/app/services/data_admin_service.py`、
  `backend/app/services/import_job_service.py`、`backend/app/services/import_object_store.py`、
  `backend/app/schemas/data_admin.py`
- Repository：`backend/app/repositories/postgres_data_admin_repository.py`、
  `backend/app/repositories/postgres_import_job_repository.py`
- Worker：`backend/app/worker.py`
- 测试：`backend/tests/test_data_admin_api.py`、`backend/tests/test_data_admin_postgres.py`、
  `backend/tests/test_baseline_and_boundaries.py`

## 前端认证与权限呈现

- staging/production 登录使用 OIDC Authorization Code + PKCE；本地 APP_ENV=demo 可在前后端同时显式开启固定开发主体。两种路径的访问令牌只保存在内存，不写 localStorage、
  sessionStorage、cookie 或 URL。
- `GET /api/me` 返回的 `permissions` 决定导航可见性与路由可访问性；`/api/me` 不返回 `tenant_id`。
- 401 清空内存令牌并回到登录页；403 保留会话并渲染无权限面板；404 渲染不存在面板。
  三者不得退化成"空数据"。
- 配置项为 `VITE_OIDC_ISSUER`、`VITE_OIDC_CLIENT_ID`、`VITE_OIDC_REDIRECT_URI`、`VITE_OIDC_SCOPE`，
  见 `frontend/.env.example`。

## 前端页面与路由

导入任务接口由治理工作区的三个页面消费，页面按权限分治：

| 路由 | 页面（`frontend/src/pages/`） | 所需权限 | 消费的接口 |
| --- | --- | --- | --- |
| `/data-admin/imports` | `ImportJobsPage.tsx` | `data_management.read` | `GET /api/data-admin/imports` |
| `/data-admin/imports/new` | `GovernanceSubmitPage.tsx` | `data_import.submit` | `POST /api/data-admin/imports` |
| `/data-admin/imports/:jobId` | `ImportJobDetailPage.tsx` | `data_management.read`（动作另需 `data_import.approve` / `data_import.submit`） | `GET /imports/{job_id}`、`GET /imports/{job_id}/errors`、`POST /decision`、`POST /cancel` |

客户端封装在 `frontend/src/api/importJobs.ts`。`submitImportJob()` 必须携带
`Idempotency-Key` 头，同一次提交尝试内重试要复用同一个键；服务端返回
`duplicate: true` 时页面要明示“已存在相同快照的任务”并给出既有任务链接，而不是创建新任务。

任务详情的可用动作严格由服务端返回的 `status` 与主体权限共同决定：只在
`approval_pending` 且持有 `data_import.approve` 时出现审批与拒绝（拒绝必须填原因）；
只在 `submitted`/`validating`/`approval_pending` 且持有 `data_import.submit` 时出现取消；
`publish_failed` 没有客户端重试接口，页面只说明已发布快照保持不变与 Worker 自动重试计数。
页面不展示 `payload`、不提供回滚与删除、不在前端重算金额。
