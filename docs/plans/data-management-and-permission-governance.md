# 数据管理面板与权限治理实施计划

> 状态：时间线记录。一期（只读面板与两角色模型）与二期批次 1（导入任务后端闭环）已完成，
> 但本文只记录各阶段交付顺序与当时依据；**当前能力事实以原子文档和代码为准**：
> [数据治理契约](../api/data-admin-contract.md)、[治理与质量](../data/governance.md)、
> [数据字典](../data/data-dictionary.md)、[Runtime](../architecture/runtime.md)、
> [Harness](../architecture/harness.md)、[运行手册](../operations/runbook.md)。
> 二期批次 1 的唯一事实来源是[二期批次 1 权威规格](data-governance-phase2-batch1-spec.md)，
> 与本文冲突时以规格为准。

## 一期（已完成，历史事实）

一期交付“只读数据治理面板 + 两角色配置主体”，当时的能力与依据如下：

- 角色只有 `user` 与 `admin`；`admin` 额外拥有 `data_management.read`。
- 新增只读 `/api/data-admin` 五个接口（`/batches`、`/batches/{id}`、`/batches/{id}/errors`、
  `/published`、`/quality`）与 `/api/me` 授权摘要。
- 事实表仍按租户级全局唯一键 upsert，同一逻辑 ID 在新批次覆盖旧行；旧 `DataLoadBatch`
  元数据保留，因此当时只能展示 `published/superseded/failed` 历史，不构成可回滚的不可变快照。
- 保留单一 Alembic baseline `20260907_0001`，新增表被明确排除在一期范围外。
- 前端新增“数据治理”导航与只读页面，含 loading/empty/error/unavailable/unauthorized 状态。

一期交付后，`user/admin` 两角色模型与“事实表可覆盖”的能力被二期批次 1 取代，见下节。

## 二期批次 1（已完成，当前能力）

二期批次 1 按[权威规格](data-governance-phase2-batch1-spec.md)交付阶段一后端闭环：

```text
数据导入任务提交 -> Worker 校验 -> 管理员审批 -> Worker 发布不可变快照 -> 任务/快照状态查询
```

已交付事实（每条可回指代码、迁移或测试）：

- **权限分类法切换到最终形态**：4 角色 `viewer/data_operator/data_approver/tenant_admin`
  / 7 权限，删除 `user/admin` 别名与兼容回退；`backend/app/domain/authorization.py` 的
  `ROLE_PERMISSIONS` 与 `backend/app/agent/capabilities.py` 的 `CapabilitySpec.required_roles`
  必须一致，否则模块导入即失败。默认主体角色由 `user` 改为 `viewer`（`backend/app/settings.py`）。
- **增量迁移**：`backend/alembic/versions/20260917_0002_import_jobs_and_immutable_snapshots.py`
  新增 `cost_data.data_import_jobs`、`cost_data.governance_audit_events`，并把
  `batch_id` 纳入五张成本事实表的主键与唯一约束，删除租户级全局唯一约束。
- **导入任务接口**：`/api/data-admin/imports*` 六个接口，需要 `Idempotency-Key`，
  载荷落本地内容寻址对象存储，响应永不返回载荷。
- **统一 Worker 调度器**：`backend/app/worker.py` 在 agent-run 循环旁增加 import-job
  领取路径，使用租约、心跳、`attempt_count` 与 `available_at` 退避重试。
- **CLI**：`backend/scripts/import_cost_data.py` 改为提交导入任务，`--auto-approve`
  驱动完整状态机。
- **门禁放开为多迁移**：`scripts/check_docs_sync.ps1` 断言 baseline 存在且允许增量迁移；
  `backend/tests/test_baseline_and_boundaries.py` 断言最新 head 为 `20260917_0002`。

## 二期批次 2A（已完成，当前能力）：OIDC 身份与 IAM

交付提交点 `feat(iam): OIDC 身份认证与租户角色/数据范围治理`。

```text
Authorization: Bearer
  -> OIDC 签名 / iss / aud / exp / 必需 claim 校验（JWKS）
  -> sub 作为 principal_id，租户与角色取自受信 claim
  -> 与 iam_principal_roles 本地成员关系求交集
  -> iam_data_scopes 读取数据范围，随主体固化
```

已交付事实（可回指代码与测试）：

- `backend/app/auth/{oidc,context,errors}.py`：令牌校验与主体构造；算法白名单
  `RS256/RS384/RS512/ES256/ES384`，必需 claim `exp,iat,iss,aud,sub`。
- `ExecutionPrincipal` 增加 `data_scope` 字段；`build_execution_context()` 与
  `execution_context_from_state()` 的 `principal` 改为必填，`data_scope` 缺省取主体自带范围。
  `RuntimeRunRequest.principal` 同样改为必填，运行边界不存在匿名主体。
- `get_server_principal()` 与 `AGENT_PRINCIPAL_ID`/`AGENT_TENANT_ID`/`AGENT_PRINCIPAL_ROLES`/
  `AGENT_ALLOWED_*` 全部删除，没有配置回退路径。
- 迁移 `20260918_0003_iam_and_audit_read.py`：新增 `cost_data.iam_principal_roles`、
  `cost_data.iam_data_scopes`，并扩展 `governance_audit_events` 动作集合增加
  `iam.roles_updated`/`iam.scope_updated`。
- 新接口：`GET /api/iam/principals`、`PUT /api/iam/principals/{principal_id}/roles`、
  `PUT /api/iam/principals/{principal_id}/scope`、`GET /api/audit/events`；
  `/api/me` 增加 `data_scope`，仍不返回 `tenant_id`。
- 401/403/404 语义分开，401 响应带 `WWW-Authenticate: Bearer` 挑战头。
- 测试：`backend/tests/test_oidc_and_iam.py` 覆盖签名、issuer、audience、过期、错误 claim、
  缺失令牌、角色矩阵与审计写入；全量 `pytest` 共 247 passed / 8 skipped。

## 二期批次 2B（已完成，当前能力）：数据库 RLS 租户隔离

交付提交点 `feat(security): 租户级行级安全与应用角色隔离`。

已交付事实（可回指迁移、代码与测试）：

- 迁移 `backend/alembic/versions/20260920_0004_rls_tenant_isolation.py` 为数据表启用租户策略
  `USING (tenant_id = current_setting('app.tenant_id', true))`，未设置 `app.tenant_id` 时策略
  返回 0 行（fail-closed）；策略用 `ENABLE` 而非 `FORCE`，表所有者不受约束。
- 只有调度表（`cost_data.data_import_jobs`、`agent_runtime.agent_runs`）允许
  `costgraph_worker` 角色跨租户读取待领取工作；子表 `messages`/`agent_run_events` 经父表
  判租户；`agent_workers` 与 checkpoint 系列表不加策略。
- `backend/app/db/tenant.py`：`set_tenant()`/`release_session()` 按请求设置并清理会话级
  `app.tenant_id`。会话依赖在 FastAPI 同步依赖的拷贝上下文中执行，因此以会话对象本身
  传递租户，不用 ContextVar。
- Worker 领取任务后用被领取行的 `tenant_id` 注入会话，Worker 角色不会变成跨租户后门。
- `backend/app/db/engine.py` 暴露 `get_session_factory()`/`reset_engine()`；应用角色连接串
  单独配置。
- 测试 `backend/tests/test_rls_postgres.py` 以受限角色 `costgraph_app` 连接真实
  PostgreSQL，验证越权读写被策略拦截；未配置 `TEST_COST_DATABASE_URL` 时跳过。

## 二期批次 2C（进行中）：前端治理工作区

- OIDC Authorization Code + PKCE，访问令牌只存内存；
- 导入任务提交向导、任务详情、IAM 与审计页面；
- 按 `permissions` 控制导航与直接访问，无权限不得伪装为空数据。

计划内容只写入 `docs/plans/`，不得作为已实现依据引用。批次 2B/2C 的当前能力以
[存储](../data/storage.md)、[运行手册](../operations/runbook.md) 与代码为准。
