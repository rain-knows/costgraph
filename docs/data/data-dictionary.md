# 数据/PostgreSQL 数据字典

最终结构以 `backend/app/db/models.py` 与唯一 Alembic baseline 为准。时间使用 `TIMESTAMPTZ`，金额使用 `NUMERIC(18,2)`，数量和工时使用 `NUMERIC(18,4)`，追溯载荷使用 `JSONB`。费用代码和计算口径见[成本核算格式](cost-accounting-format.md)。

## `cost_data`

### `data_load_batches`

| 字段 | 类型与用途 |
| --- | --- |
| `batch_id` | `UUID` 主键 |
| `tenant_id`、`source_system` | 租户与来源系统 |
| `source_file`、`source_snapshot_hash` | 无凭据的来源定位与 SHA-256；租户+来源+哈希唯一 |
| `status` | `created/validating/validated/published/superseded/failed` |
| `total_rows`、`valid_rows`、`error_rows` | 非负计数，合法+错误不超过总数 |
| `error_summary` | 仅用于错误码聚合的 JSONB |
| `started_at`、`finished_at`、`created_at` | 生命周期时间 |

部分唯一索引 `tenant_id WHERE status='published'` 保证每租户只有一个生效快照。

### `parts`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`part_id` | 复合主键 |
| `part_number` | 零件号，同租户唯一并用于查询 |
| `part_description`、`product_family` | 零件描述和产品族 |
| `part_type` | `raw_material/purchased_semi_finished/work_in_progress/finished_good` |
| `unit` | 零件数量单位 |
| `source_system`、`source_record_id` | 来源定位；租户+来源记录唯一 |
| `batch_id` | 同租户导入批次外键 |
| `raw_payload` | 来源零件原文 JSONB，仅供审计追溯 |
| `created_at`、`updated_at` | 生命周期时间 |

### `cost_events`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`event_id` | 复合主键 |
| `event_type` | `purchase/process` |
| `output_batch_id` | 本事件唯一输出批次；同租户唯一 |
| `part_id` | 同租户零件外键；表示本事件输出的零件 |
| `period` | `YYYY-MM`；必须与 `completion_time` 所在月份一致 |
| `cost_center_code`、`cost_center_name` | 成本中心与车间名称；购置事件可空 |
| `work_order_number`、`lot_number` | 车间工单号和批号 |
| `process_code`、`process_name` | 通用工艺标识与名称；购置事件可空 |
| `completion_time` | 完工时间；最终事件由此归属期间 |
| `qualified_quantity`、`defective_quantity` | 合格量大于零，不良量不小于零 |
| `unit` | 输出单位，必须与输出零件一致 |
| `machine_hours`、`labor_hours` | 非负机器与人工工时 |
| `source_system`、`source_record_id` | 来源定位；租户+来源记录唯一 |
| `batch_id`、`raw_payload` | 同租户导入批次和审计原文 |
| `created_at`、`updated_at` | 生命周期时间 |

`completed_quantity`、`quality_rate`、累计成本和单位成本不是存储字段。工艺事件必须有工艺信息；购置事件不允许成为投入边的目标。

### `cost_event_inputs`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`input_id` | 复合主键 |
| `event_id` | 消费投入的同租户目标工艺事件 |
| `source_event_id` | 生产被领用批次的同租户上游事件 |
| `consumed_quantity`、`unit` | 大于零的实际领用合格量和单位 |
| `source_system`、`source_record_id` | 来源定位；租户+来源记录唯一 |
| `batch_id`、`raw_payload` | 同租户导入批次和审计原文 |
| `created_at`、`updated_at` | 生命周期时间 |

目标与来源事件不能相同；两端、投入边及导入批次必须属于同一租户和同一快照。DAG、单位及累计超量领用由发布校验门禁。

### `cost_records`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`cost_record_id` | 复合主键 |
| `event_id` | 费用归集的同租户事件 |
| `cost_code` | 固定 48 项费用代码之一 |
| `amount`、`currency` | 非负金额，币种固定 `CNY` |
| `incurred_at` | 费用发生时间 |
| `source_system`、`source_document_no`、`source_document_line`、`source_record_id` | 来源系统、单据、行号和记录定位；租户+来源记录唯一 |
| `batch_id`、`raw_payload` | 同租户导入批次和审计原文 |
| `created_at`、`updated_at` | 生命周期时间 |

### `data_load_errors`

`error_id` 为自增主键；`tenant_id + batch_id` 指向同租户导入批次；`source_table/source_record_id` 定位四类来源记录；`error_code/error_message/raw_payload/created_at` 保存稳定错误和追溯信息。

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

## 关系

```text
data_load_batches 1--n parts/cost_events/cost_event_inputs/cost_records/data_load_errors
parts 1--n cost_events
cost_events 1--n cost_records
cost_events(source) 1--n cost_event_inputs n--1 cost_events(target)
conversations 1--n turns/messages/audit_traces/agent_runs
agent_runs 1--n agent_run_events
turns 1--n messages/audit_traces
agent_runs.run_id == agent_checkpoint.thread_id (runtime association, no FK)
artifacts --business IDs--> runtime records (no cross-schema FK)
```
