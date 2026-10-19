# 架构/数据流

## 成本导入

```text
POST /api/data-admin/imports  (data_import.submit + Idempotency-Key)
  -> payload 落本地内容寻址对象存储 -> data_import_jobs (status=submitted)
Worker: submitted -> validating
  -> CostDataImporter.validate_payload -> 字段/关系/DAG/业务校验
  -> 有错 -> data_load_errors + error_summary -> validation_failed (终态)
  -> 全部通过 -> approval_pending
管理审批: approval_pending -> approved | rejected (data_import.approve)
Worker: approved -> publishing
  -> publish_validated_snapshot 单事务：四类事实 + 确定性卷积投影
     + 新 published 批次 + 旧 published 转 superseded + 审计
  -> 成功 -> published；失败整体回滚 -> publish_failed（保留旧快照）
应用只读当前 published 快照
```

JSON 是导入输入，不是应用请求的 Repository。HTTP 只创建任务或改变状态；校验、投影生成与发布
全部由 Worker 完成。相同租户 + 幂等键，或相同租户 + 来源系统 + 快照哈希 + 规则版本，都复用已有任务。
事实与成本投影在同一事务发布，任一投影失败都会回滚且完整保留旧发布快照；发布失败没有回滚接口。
`batch_id` 是五张成本事实表主键的一部分，因此每个批次是不可变快照，同一逻辑 ID 可以跨批次重复。
所有涉及投影的 join 必须额外带 `batch_id` 谓词。导入目标始终是独立 CostGraph PostgreSQL 数据库。
详细状态机、幂等与对象存储边界见 [治理与质量](../data/governance.md)。

规范样例以汽车装饰件工厂为业务语境。`backend/scripts/generate_sample_cost_data.py` 确定性生成 100 个产成品 SKU、连续 24 个月的可比批次及其公共部件追溯链；它只重建四份 JSON，不绕过校验或直接写库。黄金批次从 PP/EPDM 单件料包与外购卡扣开始，经过注塑成型、火焰处理与表皮包覆、总成装配三道工艺后形成左前门内饰板总成，用于验证多层、多投入成本卷积与追溯。扩展数据和黄金子集均由同一 `CostDataImporter` 校验，再由 Worker 在审批后的 `publishing` 阶段发布为单一快照。

## 成本只读查询

```text
HTTP query -> Bearer 令牌校验（OIDC/JWKS）
  -> 租户内成员关系求交集 + 主体数据范围
  -> published finished batch cost projections
  -> SQL filtering/sorting/pagination or overview aggregation
  -> CostOverview | FinishedBatchCostList | FinishedBatchCostDetail
```

每个 `purchase/process` 事件只生成一个输出批次；`cost_event_inputs` 将上游输出批次的实际领用量连接到下游工艺事件，支持多投入、部分领用和同一批次分流。发布时服务端使用 `graphlib.TopologicalSorter` 校验和排序事件图，按叶级费用向量逐边分配；完整领用的最后一条边承接舍入尾差。总览、批次列表、批次详情、Agent 报告和 Artifact 读取同一规则版本的确定性投影，前端不重算金额；查询时不再重复读取整图或执行卷积。

追溯图以最终产成品事件为根向上返回事件节点、投入边和逐笔来源记录。树形 UI 遇到共享上游只显示引用标识；图中的一个事件仍只计算一次，不能因投影成树而重复累计。

## 数据治理与导入任务查询

```text
React 数据治理页面 / CLI
  -> /api/me 权限摘要
  -> 提交：data_import.submit + Idempotency-Key -> 对象存储 -> data_import_jobs
  -> 查询：data_management.read -> /api/data-admin/imports*
       任务状态、计数、脱敏错误明细与 error_codes；永不返回载荷
  -> 审批：data_import.approve -> POST /imports/{job_id}/decision -> approved/rejected
  -> 批次视图：/api/data-admin/batches* 同时展示快照状态与任务状态；错误的
       job_status 通过 job_id / published_batch_id 关联
  -> 发布快照：/api/data-admin/published + /quality
       仅当前 published batch + 当前规则版本投影完整性门禁 + DataScope 计数
```

导入任务接口只创建任务或改变状态；校验、发布由 Worker 完成；不提供回滚或重算。
错误明细不返回 `raw_payload`，来源文件仅暴露 basename。跨租户任务与批次按未找到处理（404）。
发布统计缺少当前规则版本投影、投影为空或规则混合时返回 `503/database_unavailable`，
不返回部分可信质量数据。完整接口契约见 [数据治理契约](../api/data-admin-contract.md)。

## Agent 查询与产出

```text
HTTP -> conversation context -> capability/data-scope gate
  -> finished part + period -> clarification gate
  -> published part/period cost projections
  -> presentation | period_comparison report schema 3.0 + lineage + trace
  -> atomic Run/Turn/Message/Audit/Artifact finalization
```

缺少唯一产成品零件或期间时在读取成本输入前停止，不返回金额，也不创建 Artifact。展示型报告读取一个目标期间；周期对比型报告分别读取基准期与目标期，用同一聚合服务生成两期三视图，再由报告服务确定性计算差额与变化率。合法完成的成本报告以租户+Run/message 幂等生成 Artifact；软删除 Artifact 不修改原报告、哈希或运行记录。传给模型的确定性 Decimal 事实保持原精度字符串表示，批次 `completion_time` 由报告 Pydantic 契约从内部 `datetime` 统一序列化为公开 JSON 时间字符串。

## Durable 控制流

1. Run API 校验 owner、会话和澄清关联，将请求与可信上下文固化到 PostgreSQL；部分唯一索引阻止同一会话并发活跃 Run。
2. Worker 领取 Run、维持租约并追加有序事件。
3. LangGraph 每个节点后写 checkpoint，Worker 只追加尚未持久化的图事件。
4. 租约过期后新 Worker 从同一 checkpoint 接管；活动节点允许至少一次重做。
5. 图终态经 `finalizing` 原子写入业务记录和结果事件。
6. 终态 checkpoint 按保留策略清理；活跃 Run 不清理。

控制面细节见 [Runtime](runtime.md)、[Harness](harness.md)、[Tool 与 Provider](tool-provider.md) 和 [Event、Trace、Replay 与 Eval](event-trace-replay.md)。

代码锚点：`backend/scripts/generate_sample_cost_data.py`、`backend/app/services/cost_data_import_service.py`、`backend/app/services/import_job_service.py`、`backend/app/services/import_object_store.py`、`backend/app/services/cost_data_query_service.py`、`backend/app/domain/cost.py`、`backend/app/services/lineage_service.py`、`backend/app/services/runtime_service.py`、`backend/app/repositories/`、`backend/app/worker.py`。
