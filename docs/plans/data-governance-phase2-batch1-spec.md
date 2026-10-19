# 二期批次 1 权威规格（阶段一后端闭环）

> 状态：批次 1 执行契约。本文件是本轮唯一事实来源；实施必须严格按此文件，不得扩大范围。
> 用户已确认的四项决策见第 1 节。批次 2（OIDC/IAM/RLS）与批次 3（前端/审计页面）不在本轮。
>
> **后续状态（供阅读时对齐）**：本文描述的“配置注入主体”已在批次 2A 被 OIDC 取代，
> `get_server_principal()` 与 `AGENT_PRINCIPAL_*`/`AGENT_ALLOWED_*` 已删除，
> `iam_principal_roles`/`iam_data_scopes` 与新接口已交付；当前事实以
> [IAM 契约](../api/iam-contract.md) 与 [Harness](../architecture/harness.md) 为准。
> 本文其余段落保留为批次 1 当时的事实记录，不再修改。

## 0. 范围

本轮只交付**阶段一后端闭环**，并跑绿全部门禁后提交：

```text
数据导入任务提交 -> Worker 校验 -> 管理员审批 -> Worker 发布不可变快照 -> 任务/快照状态查询
```

不做：OIDC/JWKS 校验、`iam_principal_roles`/`iam_data_scopes` 表、PostgreSQL RLS、前端页面改动、`/api/iam/*` 与 `/api/audit/events` 接口。

`get_server_principal()` 在批次 1 仍来自配置（`AGENT_PRINCIPAL_ID`/`AGENT_TENANT_ID`/`AGENT_PRINCIPAL_ROLES`）；身份替换属于批次 2。但**权限与角色分类法在本批次就切换到最终形态**（决策 2），使审批接口从第一天起就有可用的 `data_import.approve`。

## 1. 用户已确认的四项决策（冻结，不得再议）

1. **迁移策略**：放开 `scripts/check_docs_sync.ps1` 的“只能有一条 migration”检查为“baseline 必须存在且允许增量”，同步更新 `backend/tests/test_baseline_and_boundaries.py`、`docs/README.md`、`docs/governance/documentation.md`。新增增量迁移，保留 `20260907_0001_costgraph_baseline.py`。
2. **权限体系**：批次 1 直接落地最终的 4 角色 / 7 权限体系，删除 `user`/`admin` 别名，不做临时扩展。
3. **对象存储**：实现**本地内容寻址对象存储**（`object_key` + `size` + `sha256` 落受控目录），接口形态与 S3 一致，接口永不返回原始载荷。不新增依赖，不做 S3 SDK 适配器。
4. **交付批次**：批次 1 = 阶段一后端跑绿并提交；阶段二/三另起批次。

## 2. 权限与角色（最终形态，批次 1 落地）

`backend/app/domain/authorization.py`：

```python
RoleId = Literal["viewer", "data_operator", "data_approver", "tenant_admin"]
PermissionId = Literal[
    "cost_data.read",
    "data_management.read",
    "data_import.submit",
    "data_import.approve",
    "iam.manage",
    "data_scope.manage",
    "audit.read",
]
```

角色 -> 权限（代码确定性映射，`ROLE_PERMISSIONS`）：

```text
viewer        -> cost_data.read
data_operator -> cost_data.read, data_management.read, data_import.submit
data_approver -> cost_data.read, data_management.read, data_import.approve, audit.read
tenant_admin  -> 全部 7 个权限
```

角色 -> 能力（`ROLE_CAPABILITIES`，保持现状语义）：四个角色都拥有
`("system_help", "cost_calculation", "report_generation")`。

硬约束：

- `ALL_PERMISSION_IDS` 覆盖全部 7 个权限；`_authorized_permissions()` 按角色并集计算。
- **`capabilities.py` 中 `CapabilitySpec.required_roles` 必须同步为新的四个角色元组**，否则模块导入时 `_validate_capability_role_metadata()` 会抛 `RuntimeError`。这是最容易漏的连带改动。
- `_configured_roles()` 的未知角色报错信息继续使用 `AGENT_PRINCIPAL_ROLES`；默认值在 `settings.py` 中从 `"user"` 改为 `"viewer"`。
- `AGENT_SERVER_ALLOWED_PERMISSIONS` 不在本轮引入；`server_allowed_permissions` 保持 `list(ALL_PERMISSION_IDS)`。
- `require_permission()` 语义不变。

## 3. 数据模型与迁移

### 3.1 新迁移

新增 `backend/alembic/versions/20260917_0002_import_jobs_and_immutable_snapshots.py`：

- `revision = "20260917_0002"`，`down_revision = "20260907_0001"`。
- 所有 DDL 用显式 `op.` 调用，**禁止** `Base.metadata.create_all`、`autogenerate`、导入运行时模型。
- 改动必须与 `backend/app/db/models.py` 保持一致（表集合断言见第 7 节）。

### 3.2 新表

**`cost_data.data_import_jobs`**（`DataImportJob`）：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `job_id` | Uuid PK | 任务 ID |
| `tenant_id` | String(64) NOT NULL | 租户 |
| `principal_id` | String(128) NOT NULL | 提交主体快照 |
| `principal_roles` | JSONB NOT NULL | 提交时角色快照（不重新解析客户端身份） |
| `source_system` | String(64) NOT NULL | 来源系统 |
| `source_object_key` | String(512) NOT NULL | 对象存储键 |
| `source_object_size` | BigInteger NOT NULL | 字节数 |
| `source_object_sha256` | String(64) NOT NULL | 载荷 SHA-256 |
| `snapshot_hash` | String(64) NOT NULL | 规范化快照哈希 |
| `calculation_rule_version` | String(64) NOT NULL | 规则版本 |
| `idempotency_key` | String(128) NOT NULL | 幂等键 |
| `status` | String(32) NOT NULL | 状态机 |
| `total_rows` / `valid_rows` / `error_rows` | Integer NOT NULL default 0 | 计数 |
| `error_summary` | JSONB NULL | 错误码聚合 |
| `decision` | String(16) NULL | `approved` / `rejected` |
| `decision_reason` | String(500) NULL | 审批/拒绝原因（脱敏，不含载荷） |
| `decided_by` | String(128) NULL | 审批主体 |
| `decided_at` | DateTime(timezone=True) NULL | 审批时间 |
| `published_batch_id` | Uuid NULL | 发布后的快照批次 |
| `attempt_count` | Integer NOT NULL default 0 | Worker 重试计数 |
| `lease_owner` | String(128) NULL | Worker 租约持有者 |
| `lease_expires_at` | DateTime(timezone=True) NULL | 租约到期 |
| `available_at` | DateTime(timezone=True) NOT NULL | 可领取时间（重试退避） |
| `started_at` / `finished_at` | DateTime(timezone=True) | 时间 |
| `created_at` | DateTime(timezone=True) NOT NULL | |

约束与索引：

- `UniqueConstraint("tenant_id", "idempotency_key", name="uq_data_import_jobs_idempotency")`
- `CheckConstraint` `ck_data_import_jobs_status`：状态取值限定在
  `('submitted','validating','validation_failed','approval_pending','approved','rejected','publishing','published','publish_failed','cancelled')`
- `CheckConstraint("total_rows >= 0 and valid_rows >= 0 and error_rows >= 0", name="ck_data_import_jobs_nonnegative_counts")`
- `Index("ix_data_import_jobs_claim", "status", "available_at", "lease_expires_at")`
- `Index("ix_data_import_jobs_tenant_created", "tenant_id", "created_at")`

**`cost_data.governance_audit_events`**（`GovernanceAuditEvent`，追加式）：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `event_id` | BigInteger PK autoincrement | |
| `tenant_id` | String(64) NOT NULL | |
| `job_id` | Uuid NULL | 关联任务 |
| `batch_id` | Uuid NULL | 关联批次 |
| `actor_principal_id` | String(128) NOT NULL | 操作者 |
| `action` | String(32) NOT NULL | `submitted`/`validated`/`validation_failed`/`approved`/`rejected`/`publishing`/`published`/`publish_failed`/`cancelled` |
| `target_type` | String(32) NOT NULL | `import_job` / `snapshot` |
| `target_id` | String(128) NOT NULL | |
| `outcome` | String(16) NOT NULL | `success` / `failure` |
| `request_id` | String(64) NULL | |
| `detail` | JSONB NOT NULL default `{}` | 只允许计数、错误码、状态等非敏感字段 |
| `created_at` | DateTime(timezone=True) NOT NULL | |

约束：

- `CheckConstraint` `ck_governance_audit_events_action`（上面 action 取值）
- `CheckConstraint` `ck_governance_audit_events_outcome`：`('success','failure')`
- `Index("ix_governance_audit_events_tenant_created", "tenant_id", "created_at")`
- `Index("ix_governance_audit_events_job", "job_id")`
- **追加式**：不提供 UPDATE/DELETE 代码路径。审计必须不含密钥、原始载荷和模型正文。
- `detail` 不得写入 `raw_payload`、文件内容、令牌或连接串。

### 3.3 不可变快照改造（五张表）

目标：`batch_id` 成为键的一部分，每个 `batch_id` 是真正不可变的成本快照；删除“按逻辑 ID 覆盖旧批次”的 upsert。

五张表与需入键的列：

```text
cost_data.parts                          -> 入键列 part_id       (uq_parts_tenant_part 变为 (tenant_id, batch_id, part_id))
cost_data.cost_events                    -> 入键列 event_id      (uq_cost_events_tenant_event)
cost_data.cost_event_inputs              -> 入键列 input_id      (uq_cost_event_inputs_id)
cost_data.cost_records                   -> 入键列 cost_record_id (uq_cost_records_id)
cost_data.finished_batch_cost_projections-> 入键列 event_id      (uq_finished_cost_projection_batch)
```

必须删除的租户级全局唯一约束（与多快照并存冲突）：

```text
uq_cost_events_tenant_output_batch   (tenant_id, output_batch_id)
uq_parts_source_record               (tenant_id, source_system, source_record_id)
uq_cost_events_source_record         (tenant_id, source_system, source_record_id)
uq_cost_event_inputs_* 源记录唯一（若存在同形态）
uq_cost_records_source_record        (tenant_id, source_system, source_record_id)
uq_finished_cost_projection_batch    (tenant_id, finished_batch_id)
```

改为包含 `batch_id` 的版本，例如
`UniqueConstraint("tenant_id", "batch_id", "source_system", "source_record_id", name="uq_cost_events_batch_source_record")`。

**主子表外键必须组合化**：`parts`、`cost_events`、`finished_batch_cost_projections` 的主键变为
`(tenant_id, batch_id, <逻辑 ID>)` 后，指向它们的外键必须同步带上父表的 `batch_id`，否则迁移会在
“父表主键唯一性”上失败或产生错配。具体：

- `finished_batch_cost_projections` -> `cost_events` 与 -> `parts` 的外键加入 `batch_id`
- `cost_records`、`cost_event_inputs`、`cost_events` 指向 `parts`/`cost_events` 的外键加入 `batch_id`

实现顺序（迁移内）：先 drop 旧约束 -> add `batch_id` 列（若缺）-> 建新唯一约束 -> drop 旧主键 -> 建新主键（含 `batch_id`）-> 建新外键。

注意：`finished_batch_cost_projections` 现有 `event_id` 仍作为逻辑列保留，但其唯一性必须包含 `batch_id`；不要假设 `finished_batch_id` 全局唯一。

### 3.4 查询必须显式绑定 batch

投影上有 `batch_id` 列，因此**无法**匿名地“按 event_id join 成本事件”；所有涉及投影的 join 必须额外带 `batch_id` 谓词。`get_published_snapshot()` 以及成本查询路径都要按批次定界。

`DataLoadBatch` 现有唯一索引 `uq_data_load_batches_active_tenant`（部分唯一索引，`status='published'`，租户级）保留：每个租户同时只有一个 published 快照；旧快照在新快照提交后变 `superseded`。

## 4. 服务端接口（批次 1 新增）

在既有 `/api/data-admin` 前缀下新增（`backend/app/api/data_admin.py`）：

```text
POST /api/data-admin/imports                    需要 data_import.submit
GET  /api/data-admin/imports                    需要 data_management.read
GET  /api/data-admin/imports/{job_id}           需要 data_management.read
GET  /api/data-admin/imports/{job_id}/errors    需要 data_management.read
POST /api/data-admin/imports/{job_id}/decision  需要 data_import.approve
POST /api/data-admin/imports/{job_id}/cancel    需要 data_import.submit
```

`POST /api/data-admin/imports` 请求体（`ImportJobCreateRequest`）：

```json
{
  "source_system": "mes",
  "source_file_name": "cost_events.json",
  "calculation_rule_version": "cost-v1",
  "payload": {"parts": [], "cost_events": [], "cost_event_inputs": [], "cost_records": []}
}
```

- 必须带 `Idempotency-Key` 请求头；缺失返回 422。
- 同租户 + 同 `idempotency_key` 只产生一个任务：重复提交返回**已有任务**（HTTP 200，`duplicate: true`），不得新建。
- 同租户 + 同 `source_system` + 同 `snapshot_hash` + 同 `calculation_rule_version` 重复提交返回已有任务。
- `payload` 落对象存储，DB 只存 `object_key`/`size`/`sha256`；**接口永不返回 `payload` 或对象内容**。
- HTTP 只创建任务或改变状态；校验、投影生成、发布全部由 Worker 完成。

`POST .../decision` 请求体（`ImportDecisionRequest`）：

```json
{"decision": "approve", "reason": "校验结果已复核"}
```

- `decision` 取 `approve` / `reject`；`reason` 最长 500。
- 单管理员审批：拥有 `data_import.approve` 即可批准或拒绝，不强制双人复核。
- 仅 `approval_pending` 状态可决策；其他状态返回 409 `import_job_state_conflict`。

`POST .../cancel`：仅 `submitted`、`validating`、`approval_pending` 可取消（无需取消 `approved`/`publishing`）。

状态机（合法迁移）：

```text
submitted        -> validating | cancelled
validating       -> validation_failed | approval_pending | cancelled
validation_failed-> (终态)
approval_pending -> approved | rejected | cancelled
approved         -> publishing
rejected         -> (终态)
publishing       -> published | publish_failed
publish_failed   -> publishing            (同一已批准任务重试)
published        -> (终态)
cancelled        -> (终态)
```

- 非法迁移统一 `409`，错误码 `import_job_state_conflict`。
- 发布失败**保留**原已发布快照；不提供回滚接口。

既有只读接口（`/batches`、`/batches/{id}`、`/batches/{id}/errors`、`/published`、`/quality`）改为**同时展示任务状态与快照状态**（新增可选字段，不删除既有字段以免破坏现有测试；若必须改名，同一提交内改测试）。

## 5. 错误与鉴权语义

- 未登录/令牌无效：`401`（批次 1 仍为配置主体，因此主要覆盖 403/404 路径）。
- 已登录但无权限：`403`，错误码保持 `data_management_access_denied`，新增导入相关码如 `data_import_submit_access_denied`。
- 不属于当前租户的资源：`404`（统一“未找到”，不泄露存在性）。
- 数据库不可用：`503`，错误码 `database_unavailable`（沿用既有 `_call` 外壳）。
- 错误响应必须沿用现有 `{"detail": {"code","message","request_id","details"}}` 外壳与 `X-Request-ID` 行为。

## 6. Worker 与导入服务

### 6.1 拆分 `CostDataImporter`

`backend/app/services/cost_data_import_service.py` 拆成两个纯函数式方法（保持既有校验与投影逻辑语义不变）：

- `validate_payload(...)`：只做校验（`_validate_records` + 现有事件标识校验的等价逻辑），返回错误列表与计数，不写事实表、不发布时间戳。
- `publish_validated_snapshot(...)`：在**单事务**中写入四类事实 + 投影 + 批次状态 + 审计事件；只接受已通过校验的输入。

删除 `import_records()` 的“直接发布”语义与 `_upsert` 的 `on_conflict_do_update`（按逻辑 ID 覆盖）行为：改为**只插入新批次行**（`batch_id` 已在键中，逻辑 ID 可跨批次重复）。重复 `(tenant_id, batch_id, 逻辑 ID)` 才冲突。

`_validate_existing_event_identity()` 与 `OUTPUT_BATCH_CONFLICT` / `EVENT_OUTPUT_BATCH_CONFLICT`、以及 `uq_cost_events_tenant_output_batch` 相关逻辑按第 3.3 节整体删除，不留死代码；`test_cost_data_import.py` 中对应测试同步删除或改写。

### 6.2 Worker 扩展

`backend/app/worker.py` 扩展为**统一任务调度器**：在现有 agent-run 循环旁增加 import-job 领取路径，复用同一 worker 注册表/心跳，但**不要重构 agent-run claim 路径**（避免回归 `test_durable_postgres.py`）。

要求：

- 使用数据库租约：`FOR UPDATE SKIP LOCKED` + `lease_owner`/`lease_expires_at` + 心跳延长 + `attempt_count`/`available_at` 重试退避。
- `status='submitted'` 领取后进入 `validating`，调用 `validate_payload()`；成功转 `approval_pending`，失败转 `validation_failed` 并写错误行 + 审计。
- `status='approved'` 领取后进入 `publishing`，调用 `publish_validated_snapshot()`；成功转 `published`（并写 `published_batch_id`、旧快照转 `superseded`），失败转 `publish_failed`。
- 校验失败任务不可查询为成本事实；未完成审批的任务不可发布。
- 旧 published 快照只有在新快照事务提交后才变 `superseded`。
- 已存在 `RuntimeAgentWorker` 注册表可复用；如需新方法，放在 import-job 专属 repository，不要在 `run_repository.py`（54KB，高风险）里大改。

### 6.3 CLI

`backend/scripts/import_cost_data.py` 删除直接发布路径，改为**提交导入任务**（可带 `--auto-approve` 走完整状态机，供开发与端到端验证）。

## 7. 前端

批次 1 **不改前端代码**。若后端响应字段变化导致前端类型/测试失败，只做最小类型同步（`frontend/src/api/dataAdmin.ts` 与相关测试），不得新增页面。

## 8. 门禁与文档同步（同一提交内完成）

必须同步修改：

- `scripts/check_docs_sync.ps1`：
  - baseline 检查改为“必须存在 `20260907_0001_costgraph_baseline.py`，且允许增量 migration”。
  - `$requiredDocs` 增加 `api/iam-contract.md`（本批次先建契约骨架，见下）与新的 `plans/*.md`。
  - 变更映射：新增导入任务/Worker 路径触发 `docs/data/governance.md`、`docs/architecture/runtime.md`、`docs/api/data-admin-contract.md`。
- `backend/tests/test_baseline_and_boundaries.py`：
  - 迁移断言改为“baseline 存在 + 最新 head 为 `20260917_0002`”。
  - `set(Base.metadata.tables)` 加入 `cost_data.data_import_jobs`、`cost_data.governance_audit_events`。
  - canonical API 路径集合加入 6 个新 `/api/data-admin/imports*` 路径。
- 文档：更新 `docs/README.md`、`docs/data/governance.md`、`docs/data/data-dictionary.md`、`docs/data/storage.md`、`docs/architecture/harness.md`、`docs/architecture/data-flow.md`、`docs/architecture/runtime.md`、`docs/api/data-admin-contract.md`、`docs/operations/runbook.md`、`docs/governance/documentation.md`；新增 `docs/api/iam-contract.md`；重写 `docs/plans/data-management-and-permission-governance.md`（保留一期完成事实，替换旧的“后续候选阶段”）。`AGENTS.md` 若改动必须同提交更新 `docs/README.md` 或 `docs/governance/documentation.md`。

## 9. 验证与完成标准

必须真实执行（有可用 PostgreSQL：`postgresql://rainknows:rainknows@47.94.88.78:5432/costgraph_test`，superuser）：

```powershell
$env:PYTHONPATH="D:\work\costgraph\backend"
$env:TEST_COST_DATABASE_URL="postgresql://rainknows:rainknows@47.94.88.78:5432/costgraph_test"
backend\.venv\Scripts\python.exe -m alembic upgrade head   # 在 backend 目录
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
.\scripts\check_python_quality.ps1
.\scripts\check_docs_sync.ps1
git diff --check
```

后端测试必须覆盖：状态机全部合法/非法迁移、幂等提交、重复快照返回已有任务、取消、租约过期、重试、审批与发布事务原子性、新旧快照并存、查询只读当前 published、跨租户隔离、审计事件不可修改且不含密钥/原始载荷。

交付说明必须列出改动、依据、实际命令、真实跑过的测试与未验证项。
