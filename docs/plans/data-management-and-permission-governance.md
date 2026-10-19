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

## 二期后续批次（尚未实现）

- **批次 2（OIDC/IAM/RLS）**：OIDC/JWKS 校验替换配置主体；`iam_principal_roles`、
  `iam_data_scopes` 表；PostgreSQL RLS；`/api/iam/*` 与 `/api/audit/events` 接口。
  目标契约骨架见 [IAM 契约](../api/iam-contract.md)，其中路径与请求形态**尚未实现**。
- **批次 3（前端与审计页面）**：导入任务提交、审批、错误明细与审计查询的前端页面，
  以及批次 2 接口的前端类型同步。

计划内容只写入 `docs/plans/`，不得作为已实现依据引用。
