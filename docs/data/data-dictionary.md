# 数据/PostgreSQL 数据字典

最终结构以 `backend/app/db/models.py` 与唯一 Alembic baseline 为准。时间使用 `TIMESTAMPTZ`，生产日期使用 `DATE`，金额使用 `NUMERIC(18,2)`，数量使用 `NUMERIC(18,4)`，追溯载荷使用 `JSONB`。

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

部分唯一索引 `tenant_id WHERE status='published'` 保证每租户只有一个生效批次。

### `products`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`product_id` | 复合主键 |
| `product_name`、`spec`、`is_active` | 展示名、可空规格、有效标记 |
| `source_system`、`source_record_id` | 来源定位；租户+来源记录唯一 |
| `batch_id` | 同租户导入批次外键 |
| `created_at`、`updated_at` | 生命周期时间 |

### `production_outputs`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`output_record_id` | 复合主键 |
| `product_id` | 同租户产品外键 |
| `period`、`production_date` | 月份与发生日，二者月份必须一致 |
| `qualified_output_qty`、`unit` | 大于零的合格产量与单位 |
| `source_system`、`source_record_id`、`source_batch` | 来源定位 |
| `batch_id`、`raw_payload` | 同租户批次和追溯原文 |
| `created_at`、`updated_at` | 生命周期时间 |

按租户+产品+期间及租户+产品+日期建查询索引。

### `process_cost_entries`

| 字段 | 类型与用途 |
| --- | --- |
| `tenant_id`、`cost_entry_id` | 复合主键 |
| `product_id`、`period`、`production_date` | 产品、月份与发生日，月份必须一致 |
| `process_code`、`process_name`、`process_sort` | 工序标识、名称和正整数顺序 |
| `cost_item` | `material/labor/equipment/energy/overhead` |
| `amount`、`currency` | 非负金额，币种固定 `CNY` |
| `source_system`、`source_record_id`、`source_batch` | 来源定位 |
| `batch_id`、`raw_payload` | 同租户批次和追溯原文 |
| `created_at`、`updated_at` | 生命周期时间 |

按租户+产品+期间及租户+产品+日期建查询索引。

### `data_load_errors`

`error_id` 为自增主键；`tenant_id + batch_id` 指向同租户批次；`source_table/source_record_id` 定位来源；`error_code/error_message/raw_payload/created_at` 保存稳定错误和追溯信息。

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

`artifact_id` 为主键，`artifact_type` 固定 `cost_report`。owner 为 `tenant_id + principal_id`；关联字段为 conversation/turn/message/run ID；展示字段为会话标题、产品 ID、产品名和期间；内容字段为 `report_json/report_sha256/data_snapshot_id`；版本字段为 report/rule/prompt/code version；`created_at/deleted_at` 管理软删除生命周期。租户+Run 和租户+消息唯一，并按 owner+生命周期+时间及 owner+产品+期间建索引。

## 关系

```text
data_load_batches 1--n products/production_outputs/process_cost_entries/data_load_errors
products 1--n production_outputs/process_cost_entries
conversations 1--n turns/messages/audit_traces/agent_runs
agent_runs 1--n agent_run_events
turns 1--n messages/audit_traces
agent_runs.run_id == agent_checkpoint.thread_id (runtime association, no FK)
artifacts --business IDs--> runtime records (no cross-schema FK)
```
