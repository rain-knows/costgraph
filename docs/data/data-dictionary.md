# 数据/PostgreSQL 数据字典

最终结构以 `backend/app/db/models.py` 与 Alembic 迁移链（baseline `20260907_0001` + 增量 `20260917_0002`）为准。时间使用 `TIMESTAMPTZ`，金额使用 `NUMERIC(18,2)`，数量和工时使用 `NUMERIC(18,4)`，追溯载荷使用 `JSONB`。费用代码和计算口径见[成本核算格式](cost-accounting-format.md)。

## `cost_data`

### `data_load_batches`

| 字段 | 类型与用途 |
| --- | --- |
| `batch_id` | `UUID` 主键 |
| `tenant_id`、`source_system` | 租户与来源系统 |
| `source_file`、`source_snapshot_hash` | 无凭据的来源定位与 SHA-256 |
| `calculation_rule_version` | 发布时生成成本投影所用规则版本；租户+来源+哈希+规则版本唯一 |
| `status` | `created/validating/validated/published/superseded/failed` |
| `total_rows`、`valid_rows`、`error_rows` | 非负计数，合法+错误不超过总数 |
| `error_summary` | 仅用于错误码聚合的 JSONB |
| `started_at`、`finished_at`、`created_at` | 生命周期时间 |

部分唯一索引 `tenant_id WHERE status='published'` 保证每租户只有一个生效快照。

### `data_import_jobs`

导入任务与 Worker 调度状态，`job_id` 为 `UUID` 主键。`tenant_id` + `idempotency_key`
唯一（`uq_data_import_jobs_idempotency`）。

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`principal_id`、`principal_roles` | 租户与提交主体快照（`JSONB`，不重新解析客户端身份） |
| `source_system` | 来源系统；响应里的 `source_file_name` 由 `source_object_key` 的 basename 派生，不是表字段 |
| `source_object_key`、`source_object_size`、`source_object_sha256` | 对象存储定位，绝不存原始载荷 |
| `snapshot_hash`、`calculation_rule_version` | 规范化载荷哈希与规则版本 |
| `idempotency_key`、`status` | 幂等键与状态机 |
| `total_rows`、`valid_rows`、`error_rows`、`error_summary` | 非负计数与错误码聚合 `JSONB` |
| `decision`、`decision_reason`、`decided_by`、`decided_at` | 审批结论（`approved`/`rejected`）、脱敏原因与主体 |
| `published_batch_id` | 发布后的快照批次；校验失败批次用 `job_id` 关联 `data_load_batches` |
| `attempt_count`、`lease_owner`、`lease_expires_at`、`available_at` | Worker 重试计数、租约与退避 |
| `started_at`、`finished_at`、`created_at` | 生命周期时间 |

`CheckConstraint` `ck_data_import_jobs_status` 把 `status` 限定在
`submitted/validating/validation_failed/approval_pending/approved/rejected/publishing/published/publish_failed/cancelled`；
`ck_data_import_jobs_nonnegative_counts` 保证三个计数非负。
`ix_data_import_jobs_claim`（`status`、`available_at`、`lease_expires_at`）服务 `FOR UPDATE SKIP LOCKED` 领取，
`ix_data_import_jobs_tenant_created` 服务租户内按时间列表。完整状态机见[治理与质量](governance.md)。

### `governance_audit_events`

只追加的治理审计事件，`event_id` 为自增 `BIGINT` 主键。`tenant_id`、`actor_principal_id`、`action`、
`target_type`（`import_job`/`snapshot`）、`target_id`、`outcome` 为必填；`job_id`、`batch_id`、
`request_id` 可空；`detail` 为 `JSONB`，只允许计数、错误码、状态、`attempt_count`、`batch_id` 等
非敏感字段，不得写入 `raw_payload`、文件内容、令牌或连接串。
`ck_governance_audit_events_action` 与 `ck_governance_audit_events_outcome` 限定动作与结果取值；
`trg_governance_audit_events_append_only`（`BEFORE UPDATE OR DELETE`）对任何修改或删除报 `restrict_violation`，
使“只追加”由数据库约束而不是调用约定保证；
`ix_governance_audit_events_tenant_created`、`ix_governance_audit_events_job` 支持租户时间线与任务查询。
无 UPDATE/DELETE 代码路径。

### `parts`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`batch_id`、`part_id` | 复合主键；`batch_id` 是快照键的一部分 |
| `part_number` | 零件号，同租户唯一并用于查询 |
| `part_description`、`product_family` | 零件描述和产品族 |
| `part_type` | `raw_material/purchased_semi_finished/work_in_progress/finished_good` |
| `unit` | 零件数量单位 |
| `source_system`、`source_record_id` | 来源定位；`tenant_id + batch_id + source_system + source_record_id` 唯一 |
| `raw_payload` | 来源零件原文 JSONB，仅供审计追溯 |
| `created_at`、`updated_at` | 生命周期时间 |

### `cost_events`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`batch_id`、`event_id` | 复合主键；`batch_id` 是快照键的一部分 |
| `event_type` | `purchase/process` |
| `output_batch_id` | 本事件输出批次；仅同一快照内唯一（`tenant_id + batch_id + output_batch_id`） |
| `part_id` | 同租户同快照零件外键；表示本事件输出的零件 |
| `period` | `YYYY-MM`；必须与 `completion_time` 所在月份一致 |
| `cost_center_code`、`cost_center_name` | 成本中心与车间名称；购置事件可空 |
| `work_order_number`、`lot_number` | 车间工单号和批号 |
| `process_code`、`process_name` | 通用工艺标识与名称；购置事件可空 |
| `completion_time` | 完工时间；最终事件由此归属期间 |
| `qualified_quantity`、`defective_quantity` | 合格量大于零，不良量不小于零 |
| `unit` | 输出单位，必须与输出零件一致 |
| `machine_hours`、`labor_hours` | 非负机器与人工工时 |
| `source_system`、`source_record_id` | 来源定位；`tenant_id + batch_id + source_system + source_record_id` 唯一 |
| `raw_payload` | 审计原文 |
| `created_at`、`updated_at` | 生命周期时间 |

`completed_quantity`、`quality_rate`、累计成本和单位成本不是存储字段。工艺事件必须有工艺信息；
购置事件不允许成为投入边的目标。

### `cost_event_inputs`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`batch_id`、`input_id` | 复合主键 |
| `event_id` | 消费投入的同租户同快照目标工艺事件 |
| `source_event_id` | 生产被领用批次的同租户同快照上游事件 |
| `consumed_quantity`、`unit` | 大于零的实际领用合格量和单位 |
| `source_system`、`source_record_id` | 来源定位 |
| `raw_payload` | 审计原文 |
| `created_at`、`updated_at` | 生命周期时间 |

目标与来源事件不能相同；两端、投入边及导入批次必须属于同一租户和同一快照（外键都带 `batch_id`）。
DAG、单位及累计超量领用由发布校验门禁。

### `cost_records`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`batch_id`、`cost_record_id` | 复合主键 |
| `event_id` | 费用归集的同租户同快照事件 |
| `cost_code` | 固定 48 项费用代码之一 |
| `amount`、`currency` | 非负金额，币种固定 `CNY` |
| `incurred_at` | 费用发生时间 |
| `source_system`、`source_document_no`、`source_document_line`、`source_record_id` | 来源系统、单据、行号和记录定位 |
| `raw_payload` | 审计原文 |
| `created_at`、`updated_at` | 生命周期时间 |

### `data_load_errors`

`error_id` 为自增主键；`tenant_id + batch_id` 指向同租户导入批次；`source_table/source_record_id` 定位四类来源记录；`error_code/error_message/raw_payload/created_at` 保存稳定错误和追溯信息。

### `finished_batch_cost_projections`

服务端在发布事务中从四张成本事实表确定性生成的不可变读模型，不属于导入输入。
`tenant_id + batch_id + event_id` 为主键，`batch_id` 绑定精确快照，
`tenant_id + batch_id + finished_batch_id` 唯一（同一逻辑成品批次可在多个快照中各自存在）；
`part_id/period/completion_time/cost_center_code/search_text/total_unit_cost` 支持权限过滤、搜索、
排序和分页。数量、制造成本、制造后成本和总成本支持总览聚合；`summary_json/trace_json` 保存经当前
Pydantic 契约校验的批次摘要与追溯图，`rule_version` 必须等于导入批次规则版本。投影可由事实快照和
规则完整重建，不能反向修改成本事实。`batch_id` 列存在但不引用 `cost_events`，因此查询必须显式带
`batch_id` 谓词。

### 已删除的旧唯一约束（`20260917_0002`）

下列租户级全局唯一约束与多快照并存冲突，已随 `batch_id` 入键被删除：
`uq_parts_source_record`、`uq_cost_events_tenant_output_batch`、`uq_cost_events_source_record`、
`uq_cost_records_source_record`、`uq_finished_cost_projection_batch`。
替代版本为含 `batch_id` 的 `uq_parts_batch_source_record`、`uq_cost_events_batch_source_record`、
`uq_cost_records_batch_source_record` 等；`uq_cost_event_inputs_*` 源记录唯一约束在 baseline 中不存在，
因此没有需要迁移的同形态约束。

## `agent_runtime`

| 表 | 关键字段与约束 |
| --- | --- |
| `conversations` | 会话 ID、owner、标题、路由模式、能力与上下文 JSON、时间；owner+更新时间索引 |
| `turns` | Turn ID、会话、owner、消息 ID、问题、结果、时间；会话级联，租户+消息唯一 |
| `messages` | 消息 ID、会话、Turn、`user/assistant` 角色、内容、Run 摘要、时间 |
| `audit_traces` | Run、会话、Turn、Audit V3 JSON、时间；会话/Turn 级联 |
| `agent_runs` | 请求与 owner 快照、版本、预算、状态、attempt、租约、取消、结果、安全错误和时间 |
| `agent_run_events` | 递增事件 ID、Run、graph sequence、event key、类型、公开 payload 与时间 |
| `agent_workers` | Worker ID、状态、主机、PID、版本、当前 Run、启动和心跳时间 |

`agent_runs.status` 为 `queued/running/finalizing/retry_wait/succeeded/failed/cancelled`，前四项为活跃状态；会话活跃状态部分唯一。租户+`message_id` 唯一，规范化请求哈希用于幂等冲突判断。`api_version` 固定为 `2.0`，另保存 Runtime/Workflow 版本、预算 limits/usage 和下一个事件 sequence。只有成功 Run 保存结果，只有失败 Run 保存脱敏公开错误。

`agent_run_events` 的公开类型为 `status/node/tool/policy/budget/clarification/result/error`；schema 固定为 `2.0`。Run+graph sequence 与 event key 唯一，数据库事件 ID 是 SSE 恢复游标。

## `agent_checkpoint`

| 表 | 主键与用途 |
| --- | --- |
| `checkpoint_migrations` | checkpoint 库自己的 schema 版本 |
| `checkpoints` | `thread_id + checkpoint_ns + checkpoint_id`；图状态与元数据 |
| `checkpoint_blobs` | `thread_id + checkpoint_ns + channel + version`；通道 blob |
| `checkpoint_writes` | `thread_id + checkpoint_ns + checkpoint_id + task_id + idx`；节点写入 |

本项目 `thread_id` 等于 `agent_runs.run_id`。checkpoint 没有公共读取 API。

## `agent_output.artifacts`

`artifact_id` 为主键，`artifact_type` 固定 `cost_report`。owner 为 `tenant_id + principal_id`；关联字段为 conversation/turn/message/run ID；展示字段为会话标题、`part_id/part_number/part_description` 和目标期间；内容字段为 `report_json/report_sha256/data_snapshot_id`，其中 `report_json` 只接受当前 `CostReportV3`，周期对比基准期保存在 `comparison.baseline_period`；版本字段为 report/rule/prompt/code version；`created_at/deleted_at` 管理软删除生命周期。租户+Run 和租户+消息唯一，并按 owner+生命周期+时间及 owner+零件+期间建索引。

## 数据治理视图

数据治理 API 复用 `data_load_batches`、`data_load_errors` 与 `data_import_jobs` 的字段：批次响应同时
展示快照状态与任务状态，并从 `parts`、`cost_events`、`cost_event_inputs`、`cost_records` 和
`finished_batch_cost_projections` 聚合租户/当前发布快照统计。该视图不读取 `raw_payload`，
`source_file`/`source_file_name` 只返回 basename。

导入载荷本身不在关系表：它写入本地内容寻址对象存储，数据库只保留
`source_object_key`/`source_object_size`/`source_object_sha256`。

## 关系

```text
data_load_batches 1--n parts/cost_events/cost_event_inputs/cost_records/data_load_errors
parts 1--n cost_events                        (FK 带 tenant_id + batch_id)
cost_events 1--n cost_records                 (FK 带 tenant_id + batch_id)
cost_events(source) 1--n cost_event_inputs n--1 cost_events(target)  (FK 带 tenant_id + batch_id)
data_import_jobs 1--n governance_audit_events (job_id，无 FK)
data_import_jobs.published_batch_id --> data_load_batches.batch_id (业务关联，无 FK)
conversations 1--n turns/messages/audit_traces/agent_runs
agent_runs 1--n agent_run_events
turns 1--n messages/audit_traces
agent_runs.run_id == agent_checkpoint.thread_id (runtime association, no FK)
artifacts --business IDs--> runtime records (no cross-schema FK)
```
