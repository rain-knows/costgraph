# 数据/治理与质量

## 导入闭环

导入是服务端任务，不是同步写库脚本。提交、校验、审批、发布四个阶段由不同组件负责：

```text
POST /api/data-admin/imports  (需 Idempotency-Key)
  -> payload 落本地内容寻址对象存储；DB 只存 object_key/size/sha256
  -> status=submitted
Worker 领取 submitted -> validating
  -> 字段/关系/DAG/业务校验 -> data_load_errors 错误行 + error_summary
  -> 全部通过 -> approval_pending；有错 -> validation_failed（终态）
管理员审批（需 data_import.approve）
  -> approve -> approved；reject -> rejected（终态）
Worker 领取 approved -> publishing
  -> 单事务写入四类事实 + 确定性投影 + 新 published 批次 + 审计
  -> 旧 published 批次在同一事务内转 superseded
  -> 成功 -> published；失败 -> publish_failed（保留旧 published 快照）
应用读取只使用当前 published 快照
```

### 导入任务状态机

`cost_data.data_import_jobs.status` 只允许
`submitted/validating/validation_failed/approval_pending/approved/rejected/publishing/published/publish_failed/cancelled`，
由数据库 `ck_data_import_jobs_status` 约束。合法迁移：

```text
submitted         -> validating | cancelled
validating        -> validation_failed | approval_pending | cancelled
validation_failed -> (终态)
approval_pending  -> approved | rejected | cancelled
approved          -> publishing
rejected          -> (终态)
publishing        -> published | publish_failed
publish_failed    -> publishing            (同一已批准任务重试)
published         -> (终态)
cancelled         -> (终态)
```

非法迁移由持久层确定性校验并统一返回 `409/import_job_state_conflict`；HTTP 只创建任务或改变
状态，校验、投影生成与发布全部由 Worker 完成。取消只允许 `submitted/validating/approval_pending`，
不提供取消 `approved/publishing` 的接口。

### 幂等

- `Idempotency-Key` 请求头必填；同租户 + 同 `idempotency_key` 只产生一个任务，重复提交返回
  已有任务（HTTP 200，`duplicate: true`），不新建（`uq_data_import_jobs_idempotency`）。
- 同租户 + 同 `source_system` + 同 `snapshot_hash` + 同 `calculation_rule_version` 也复用已有任务。
- `snapshot_hash` 是四类记录集合的规范化哈希；规则版本变化必须重新提交并重新发布投影。

### 审批与发布

- 单管理员审批：拥有 `data_import.approve` 即可批准或拒绝，不强制双人复核；只有
  `approval_pending` 可决策，`decision` 取 `approve`/`reject`，`reason` 脱敏且不含载荷。
- 发布在**单事务**内完成四类事实写入、确定性投影生成、新批次发布与旧批次 `superseded`；
  任一投影失败整体回滚，**原有已发布快照完整保留**。
- 发布失败没有回滚接口；`publish_failed` 任务只允许重试发布（回到 `publishing`），
  不回滚、不删除已发布快照，也不提供历史快照切换。
- 校验失败任务不可查询为成本事实；未完成审批的任务不可发布。

### 不可变快照

`batch_id` 是 `parts`、`cost_events`、`cost_event_inputs`、`cost_records` 和
`finished_batch_cost_projections` 主键与唯一约束的一部分，事实行不再按逻辑 ID 覆盖旧批次。
同一逻辑 ID 可以稳定地跨批次重复，只有 `(tenant_id, batch_id, 逻辑 ID)` 冲突才是真正的重复。
因此每个 `batch_id` 是真正不可变的成本快照；旧快照在新快照事务提交后才转为 `superseded`，
租户级部分唯一索引保证每租户同时只有一个 `published` 快照。
所有涉及投影的 join 必须额外带 `batch_id` 谓词，不能匿名地按 `event_id` 关联。

### 对象存储边界

导入载荷写入本地内容寻址对象存储（`imports/<前两位>/<sha256>`），`object_key` 由内容
SHA-256 派生，写入为同目录临时文件 + 原子替换，读回时重新校验摘要。数据库只保存
`source_object_key` / `source_object_size` / `source_object_sha256`；接口永不返回载荷或对象内容。

### 治理审计

`cost_data.governance_audit_events` 只追加：不提供 UPDATE/DELETE 代码路径，并由
`trg_governance_audit_events_append_only` 触发器在数据库层拦截任何修改或删除（`restrict_violation`）。记录
`submitted/validated/validation_failed/approved/rejected/publishing/published/publish_failed/cancelled`
动作与 `import_job`/`snapshot` 目标，`outcome` 只允许 `success/failure`。
`detail` 只允许计数、错误码、状态、`attempt_count`、`batch_id` 等非敏感字段，
不得写入 `raw_payload`、文件内容、令牌或连接串；没有查询 API。

审计写入不依赖调用方自觉：`PostgresImportJobRepository` 只暴露 `append_audit_event`，
且 `_safe_audit_detail()` 按白名单过滤细节字段，未列入白名单的键一律丢弃。

## 数据质量

- 零件类型只允许 `raw_material/purchased_semi_finished/work_in_progress/finished_good`；事件类型只允许 `purchase/process`。
- 每个事件只生成一个输出批次，`output_batch_id` 在同一快照内唯一（`tenant_id + batch_id + output_batch_id`，旧租户级全局唯一约束已删除）；`qualified_quantity >= 0`、`defective_quantity >= 0` 且二者之和大于零，投入量必须大于零且单位与来源事件一致。合格量为零的事件不得被领用。
- `purchase` 事件不得声明工艺投入；`process` 事件必须有工艺标识，允许一个或多个上游投入。
- 费用代码必须属于固定的 48 项清单，`amount >= 0`，币种固定为 `CNY`。
- 每条零件、事件、投入边和费用记录属于同一租户与同一导入批次；关联不能跨租户、跨快照，不能引用不存在的事件或零件。主子表外键都带 `batch_id`，禁止跨批次引用。
- 投入关系必须为 DAG；禁止自环和间接环路。同一来源事件的累计领用量不得超过其合格数量，不良品数量不可领用。
- 批次期间由最终产成品事件的 `completion_time` 推导，不接受另一份可冲突的期间事实。
- 批次计数非负，且 `valid_rows + error_rows <= total_rows`。
- 任何错误行使整个批次无法发布；错误详情写入 `data_load_errors`，不能被 Agent 或成本 API 查询。

费用代码、输入字段、DAG 领用关系、四位数量/两位金额尺度和三套视图恒等式以[成本核算格式](cost-accounting-format.md)为唯一规则来源；本文件只定义发布、隔离和质量门禁。

应用校验由 `backend/app/services/cost_data_import_service.py` 的 `validate_payload()` 在 Worker 的
`validating` 阶段执行，数据库约束作为最终门禁；校验失败时错误行写入 `data_load_errors`
（`batch_id = job_id`，懒创建一条 `status=failed` 的批次行满足外键），且不写任何成本事实。

仓库只保留 `parts/cost_events/cost_event_inputs/cost_records` 四份规范导入样例。当前汽车装饰件样例覆盖 100 个产成品 SKU 和 `2025-01` 至 `2026-12` 连续 24 个月，每月最少 10 个产成品批次；每个 SKU 至少在两个年度的同一自然月出现，并在追溯链中复用多种公共部件定义。`backend/scripts/generate_sample_cost_data.py` 负责确定性重建该数据集。原有黄金批次必须继续包含 5 个事件节点、4 条投入边和 3 道连续生产工序；导入测试同时校验规模、可比性、公共部件复用、行业语义、工序顺序与关系完整性，旧版扁平产品/产量/工序费用样例不再保留。

## 隔离与追溯

- 成本数据以 `tenant_id` 隔离，产成品零件/期间 scope 由服务端执行上下文确定；客户端查询条件不能扩大授权范围。最终批次可见不自动授予对越权上游节点的读取权限，发布前必须保证其整条可用追溯链满足同一执行范围。
- 会话、Run、事件、Trace 与产出以 `tenant_id + principal_id` 隔离；不属于当前 owner 的对象表现为未找到。
- 报告和 Artifact 保存 `data_snapshot_id`、报告哈希及 report/rule/prompt/code 版本，用于重现输入版本，不保存完整敏感源数据。
- Artifact 为 owner 范围内软删除，可从回收站恢复；删除与恢复不修改原报告、哈希、Turn 或审计记录。
- Audit Trace 只保存允许的运行元数据、usage、策略快照和规范化哈希；敏感内容边界见 [Event、Trace、Replay 与 Eval](../architecture/event-trace-replay.md)。
- 终态 checkpoint 默认按服务端保留期清理，活跃 Run 不清理；删除终态会话时同步清除关联 checkpoint。

## 只读数据治理视图

`/api/data-admin` 的批次、错误、发布与质量只读接口需要 `data_management.read`；导入提交需要
`data_import.submit`，决策需要 `data_import.approve`。它读取已有批次元数据、脱敏错误明细、
当前 published 批次和基础质量统计，不提供上传（提交走导入任务接口）、回滚、删除、编辑或重算。
批次元数据按租户隔离；发布统计和四类事实/投影计数还应用服务端 `DataScope`。
错误接口不返回 `raw_payload`，来源文件只展示 basename。批次响应同时展示任务状态与快照状态
（新增可选字段，不删除既有字段）。

发布质量门禁必须同时满足当前规则版本、非空投影、单一投影批次和统一 `rule_version`；
否则服务端返回 `503/database_unavailable`，不以不完整统计伪装质量状态。
历史批次是各自独立的不可变快照，但仓库不提供快照切换或回滚能力，因此接口不得把
`superseded` 描述为可回滚目标。

## 身份与范围治理

导入任务的提交、审批与数据读取都绑定 OIDC 认证后的主体：

- 身份由 `Authorization: Bearer` 访问令牌校验确定（签名、`iss`、`aud`、`exp`），
  `sub` 映射 `principal_id`，租户与角色取自受信 claim 并与 `cost_data.iam_principal_roles`
  的本地成员关系取交集；令牌角色本身不构成授权。
- 权限固定为 7 项，角色固定为 4 个；提交需 `data_import.submit`，审批需 `data_import.approve`，
  查询需 `data_management.read`，审计查询需 `audit.read`。
- 数据范围来自 `cost_data.iam_data_scopes`，随主体固化到 Run 与导入任务；
  Worker 只用固化快照，不重新解析客户端身份。
- 角色与范围的每次变更都追加 `iam.roles_updated` / `iam.scope_updated` 审计事件。
- 跨租户资源一律按未找到（404）处理，已登录无权限返回 403，未登录或令牌无效返回 401。

接口细节见 [IAM 契约](../api/iam-contract.md)，授权门见 [Harness](../architecture/harness.md)。

## 当前不提供

以下能力**已经提供**，不属于排除项：OIDC/JWKS 认证、主体级角色与数据范围表、
`/api/iam/*` 与 `/api/audit/events` 接口。

以下仍不属于当前仓库能力：库存结存、返工分支、副产品、快照回滚、自动备份和生产恢复演练。
数据治理前端页面与数据库 RLS 的交付状态以各自原子的文档为准，不在本节宣称。

## 前端认证与权限呈现

- 登录使用 OIDC Authorization Code + PKCE；访问令牌只保存在内存，不写 localStorage、
  sessionStorage、cookie 或 URL，刷新页面即回到未登录态。
- `GET /api/me` 返回的 `permissions` 决定导航可见性与路由可访问性；`/api/me` 不返回 `tenant_id`。
- 401 清空内存令牌并回到登录页；403 保留会话并渲染无权限面板；404 渲染不存在面板。
  三者不得退化成"空数据"。
- 配置项为 `VITE_OIDC_ISSUER`、`VITE_OIDC_CLIENT_ID`、`VITE_OIDC_REDIRECT_URI`、`VITE_OIDC_SCOPE`，
  见 `frontend/.env.example`。
