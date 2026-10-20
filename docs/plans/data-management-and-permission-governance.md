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

## 二期批次 2B（已完成，已在真实 PostgreSQL 验证）：数据库 RLS 租户隔离

交付提交点 `feat(security): 租户级行级安全与应用角色隔离` 与本轮修正
`backend/alembic/versions/20260921_0005_rls_scheduler_and_child_tables.py`。

已实现事实（可回指迁移与代码）：

- 迁移 `backend/alembic/versions/20260920_0004_rls_tenant_isolation.py` 为带 `tenant_id` 的表启用
  租户策略 `USING (tenant_id = current_setting('app.tenant_id', true))`，未设置 `app.tenant_id`
  时策略返回 0 行（fail-closed）；策略用 `ENABLE` 而非 `FORCE`，表所有者不受约束。
- `20260921_0005` 补齐落地时发现的缺口：只有调度表（`cost_data.data_import_jobs`、
  `agent_runtime.agent_runs`）允许 `costgraph_worker` 角色跨租户读取待领取工作；子表
  `messages`/`agent_run_events`/`audit_traces`/`agent_workers` 无 `tenant_id`，经父表判租户。
- `backend/app/db/tenant.py`：`set_tenant()`/`release_session()` 按请求设置并清理会话级
  `app.tenant_id`；RESET 失败时作废物理连接，不把仍带旧租户的连接还回连接池。
  会话依赖在 FastAPI 同步依赖的拷贝上下文中执行，因此以会话对象本身传递租户，不用 ContextVar。
- `release_session()` 必须先 `expunge_all()` 再 `rollback()`：`rollback()` 会把身份映射实例
  标记为 expired，而仓储习惯在会话关闭后把 ORM 行映射成 dict，后置属性访问会抛
  `DetachedInstanceError`。
- Worker 领取任务后用被领取行的 `tenant_id` 注入会话，Worker 角色不会变成跨租户后门。

**验证状态：已在真实 PostgreSQL 上执行。** 以受限角色 `costgraph_app` 连接专用
`costgraph_test` 后全量 `pytest` 为 `288 passed`（0 skipped），其中
`test_rls_postgres.py`（6）、`test_data_admin_postgres.py`（3）、
`test_durable_postgres.py`（3）、`test_governance_audit_postgres.py`（2）、
`test_import_jobs_postgres.py`（17）共 31 个真实库用例全部通过，包含最后一次租约领取后崩溃
的失败终态。

## 二期批次 2C（已完成，前端能力）：前端治理工作区

交付提交点 `feat(governance): 前端治理工作区与权限分治页面`。

- 页面：`ImportJobsPage`（列表）、`GovernanceSubmitPage`（提交向导）、
  `ImportJobDetailPage`（校验错误分页、审批/拒绕、取消、发布状态）、`IamPage`、`AuditPage`。
- 路由权限在 `frontend/src/auth/permissions.ts` 按最长前缀匹配；`/data-admin/imports/new`
  需 `data_import.submit`，覆盖 `/data-admin` 的 `data_management.read`。
- 任务详情动作由服务端 `status` 与主体权限共同决定；`publish_failed` 不提供伪造的手动重试接口。
- 前端测试 92 passed（含新增 7 个测试文件）；`npm run build` 与 `npm run test:bundle` 通过，
  初始 JS 210.5 KiB（门禁 500 KiB）。

真实 Chromium 浏览器验收已通过：`scripts/verify_reports_browser.ps1 -LocalDemoAuth` 覆盖
成本列表、批次详情、周期对比报表、展示型报表，以及桌面/移动、浅/深主题；结果与四张截图
写入 `.pi/goals/evidence/reports-browser/`，并检查 ECharts canvas、横向溢出、数量格式和 Tab
键焦点。

计划内容只写入 `docs/plans/`，不得作为已实现依据引用。批次 2B/2C 的当前能力以
[存储](../data/storage.md)、[运行手册](../operations/runbook.md) 与代码为准。

## 未关闭的验证项

以下验证项已具备代码与测试用例，但尚未取得运行证据，交付时不得声称已验证：

1. 真实 OIDC IdP 对接（当前环境没有可用的外部 issuer/audience/JWKS/client 配置；本地 demo
   认证和本地 RSA/JWKS 测试不等价于真实 IdP 验证）。

**真实 DeepSeek 调用已验证：** 2026-10-19 09:22 UTC 执行
`backend\.venv\Scripts\python.exe backend\scripts\verify_deepseek.py`，模型
`deepseek-v4-flash` 的意图解析与成本分析两次调用均返回 `success`，合计用量 3,304 tokens。
该运行使用本地黄金 fixture；它证明真实 provider 入口可调用，不替代 Chromium 或 OIDC 验收。

真实 PostgreSQL 上的 RLS 拦截、数据治理、durable、审计不可变和租约耗尽验证已在本轮取得运行证据
（`288 passed`，0 skipped），不再列为未关闭项。浏览器验收也已取得真实 Chromium 证据；仅真实
外部 OIDC IdP 对接仍需部署环境提供配置后执行。
