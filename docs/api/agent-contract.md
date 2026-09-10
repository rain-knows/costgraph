# API/Agent 契约

## 能力与 owner

当前能力为 `system_help`、`cost_calculation` 和 `report_generation`。服务端按 `requested ∩ role_permissions ∩ server_allowlist` 计算有效能力；`report_generation` 依赖 `cost_calculation`，缺少底层成本数据能力时不会进入有效能力集。客户端字段不是授权事实。所有会话、Run、事件和 Artifact 端点按 `tenant_id + principal_id` 过滤；无权访问的对象表现为 `404`。

## 会话

```text
POST   /api/conversations
GET    /api/conversations
GET    /api/conversations/{conversation_id}
PATCH  /api/conversations/{conversation_id}
DELETE /api/conversations/{conversation_id}
GET    /api/conversations/{conversation_id}/messages?limit=30&before={message_id}
```

消息列表返回 `items/next_cursor/has_more`；消息中的 Run 仅为摘要，完整生命周期通过其 `run_id` 访问 Runtime API。删除存在活跃 Run 的会话返回 `409 conversation_busy`。

## Runtime Run 与 SSE

持久化协议固定为 `/api/v2`：

```text
POST /api/v2/conversations/{conversation_id}/runs
GET  /api/v2/conversations/{conversation_id}/runs/active
GET  /api/v2/conversations/{conversation_id}/runs/{run_id}
GET  /api/v2/conversations/{conversation_id}/runs/{run_id}/events
POST /api/v2/conversations/{conversation_id}/runs/{run_id}/cancel
```

创建请求要求客户端生成的非空 `message_id/content`；可选字段为 `routing_mode/enabled_capabilities/reply_to_clarification_id`。创建固定返回 `202`。Run 响应包含协议、Runtime、Workflow 与 Trace 版本，Run/会话/Turn/消息 ID，生命周期时间，预算 limits/usage，事件 URL，以及终态结果或安全错误。

Run 状态只使用 `queued/running/finalizing/retry_wait/succeeded/failed/cancelled`。只有 `succeeded` 带业务结果，只有 `failed` 带公开错误。`active` 没有活跃 Run 时返回 JSON `null`。

事件端点使用 SSE，支持查询参数 `after_event_id` 与请求头 `Last-Event-ID`，取较大游标。公开类型为 `status/node/tool/policy/budget/clarification/result/error`；事件包含 sequence、kind、name、status、summary、时间和可选预算/详情。服务端先重放遗漏事件，再等待新事件；空闲时发送非持久化心跳注释，终态事件发完后关闭。客户端按 sequence 去重。

取消幂等：排队或等待重试的 Run 立即取消；运行 Run 记录请求并在控制边界生效；终态 Run 原样返回。取消保留用户问题并形成明确终态，不生成成本 Artifact。

## 澄清、结果与幂等

明确出现“报表/报告/展示型/周期对比”等产出词时进入 `report_generation` 路由；普通成本问题进入 `cost_calculation`。两条路由共用确定性成本链路，但 Trace 和状态栏保留实际路由。成本计算必须解析到唯一 `finished_good` 零件和 `period`。问题理解节点同时输出 `report_style`；服务端只接受 `presentation`（展示型）与 `period_comparison`（周期对比型）。周期对比显式提供两个自然月时，较早月份为基准期、较晚月份为目标期；只提供目标期时，环比或普通对比采用上一自然月，同比采用上年同月。条件不完整、两个期间相同或匹配多个零件时只返回 `needs_clarification`，不得读取成本事实、编造金额或生成 Artifact；后续回答通过 `reply_to_clarification_id` 关联。

业务结果包括 `final_message`、可空结构化 `report_json`、事件摘要、状态栏、`outcome`、可空澄清和已使用上下文。`outcome` 为 `completed/needs_clarification/blocked/failed`。报告金额经 Pydantic 校验并由 Decimal 计算：金额与单位成本 2 位、数量和工时 4 位、合格率与占比 2 位百分数，舍入为 `ROUND_HALF_UP`。

`message_id` 在租户内绑定会话、owner 和规范化请求快照；相同请求复用同一 Run，不同请求返回 `409 message_id_conflict`。同一会话同时只能有一个活跃 Run。

## CostReportV3

成功的成本计算直接输出 report schema `3.0`，不提供旧报告字段或转换层。报告按指定产成品零件汇总目标期间内全部最终批次，结构固定为：

| 字段 | 语义 |
| --- | --- |
| `report_schema_version/report_style` | 固定 `3.0`；样式为 `presentation` 或 `period_comparison` |
| `rule_version/prompt_version/code_version` | 确定性规则、解释 Prompt 和代码版本 |
| `data_snapshot_id/run_id` | 已发布输入快照 SHA-256 与本次 Run |
| `part/period` | `PartIdentity` 与自然月 |
| `comparison` | 展示型固定为 `null`；周期对比型包含基准期三视图、基准批次、四项核心指标和六类制造成本的基准值、目标值、差额及变化率 |
| `batch_summary` | `batch_count/completed_quantity/qualified_quantity/defective_quantity/quality_rate` |
| `summary_cards` | 至少三张由服务端生成的标签、值和单位 |
| `manufacturing_view` | 六类制造成本、45 个制造叶项及合计 |
| `material_labor_overhead_view` | 料、工、费及制造合计 |
| `variable_fixed_view` | 变动/固定成本1、三项制造后费用及成本2 |
| `finished_batches` | 期间内每个最终批次的身份与三视图，至少一项 |
| `insight_cards/analysis_text` | 模型解释；不能覆盖结构化数字 |
| `calculation_formula/calculation_policy` | 服务端公式说明和 Decimal/舍入策略 |
| `source_summary/lineage` | 可读来源摘要及四张成本事实表的记录计数和 ID 样本 |
| `model_info/ai_trace/agent_steps` | 脱敏模型、Trace 与执行步骤元数据 |

`lineage.schema_version` 固定 `2.0`，`source` 固定 `postgresql_cost_data`，`tables` 必须恰好覆盖 `cost_data.parts`、`cost_data.cost_events`、`cost_data.cost_event_inputs` 和 `cost_data.cost_records`，并与报告的 `data_snapshot_id` 一致。`LineageTable.record_count` 与 `record_id_sample` 由服务端查询生成，不能由模型提供。

`comparison.delta = current_value - baseline_value`，`change_rate = delta / baseline_value * 100`；基准值为零时变化率为 `null`。这些值由服务端 Decimal 代码计算，模型与前端不重算。三视图和 `finished_batches` 的值对象与[成本数据契约](cost-data-contract.md)一致；Report 和 Artifact 中 Decimal 也使用 JSON 字符串。

## Artifact

```text
GET    /api/artifacts?state=active|trashed&query=&period=&run_id=&limit=50&offset=0
GET    /api/artifacts/{artifact_id}
DELETE /api/artifacts/{artifact_id}
POST   /api/artifacts/{artifact_id}/restore
```

列表返回 owner 范围内摘要、总数和分页；摘要展示 `part_id/part_number/part_description/period`，详情才返回 `report_json`。`state` 默认 `active`。删除与恢复均幂等软操作；回收站不提供永久删除或导出。

## 错误与健康

每个 HTTP 请求校验或生成 UUID request ID，并在 `X-Request-ID` 返回。HTTP 错误使用 `detail.code/message/request_id/details`；SSE 错误使用同一安全字段。公开响应不得包含 Python 异常、SQL、路径、连接串、Prompt、模型原文或密钥。

- 未找到、owner 不匹配或游标无效：`404`。
- 幂等冲突或会话忙：`409`。
- 数据库、Alembic head 或 Worker 未就绪：`503 runtime_unavailable`。
- 请求 Schema 不合法：`422 validation_error`。

```text
GET /api/health
GET /api/livez
GET /api/readyz
```

`livez` 只证明进程可响应；`readyz` 检查数据库、迁移和 Worker，不调用付费模型；`health` 汇总版本、执行模式、`alembic` 和 `runtime_ready`。迁移检查同时校验当前模型必需的表和列：revision 相同但缺表或缺列时，`health.alembic` 与 `readyz.checks.alembic` 为 `schema_mismatch`，`runtime_ready=false`，`readyz` 返回 503。该检查不校验类型、索引或约束。前端在 `runtime_ready=false` 时禁止创建 Run。

依据：`backend/app/api/conversations.py`、`backend/app/api/runtime_runs.py`、`backend/app/api/artifacts.py`、`backend/app/schemas/runtime.py`、`backend/app/schemas/agent.py`、`backend/app/schemas/artifact.py`。
