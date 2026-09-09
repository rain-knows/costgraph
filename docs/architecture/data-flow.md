# 架构/数据流

## 成本导入

```text
sample JSON -> CostDataImporter -> source snapshot SHA-256
  -> data_load_batches -> field/relation/business validation
  -> data_load_errors for rejected rows -> transactional publish
  -> previous published batch becomes superseded
  -> application reads only the new published snapshot
```

JSON 是导入输入，不是应用请求的 Repository。失败批次不可查询；相同租户、来源系统和快照哈希幂等跳过。导入目标始终是独立 CostGraph PostgreSQL 数据库。

## 成本只读查询

```text
HTTP query -> server principal/tenant/data scope
  -> published product/output/cost entries
  -> Decimal aggregation + comparison + display serialization
  -> CostOverview | ProductPeriodSummary | ProductPeriodDetail
```

总览、产品列表和产品详情复用同一确定性成本服务。详情层级只包含“产品 -> 工序 -> 成本项 -> 来源摘要”，不推断生产批次节点或 BOM。

## Agent 查询与产出

```text
HTTP -> conversation context -> capability/data-scope gate
  -> product + period/date range -> clarification gate
  -> published facts -> Decimal calculation
  -> report + lineage + trace
  -> atomic Run/Turn/Message/Audit/Artifact finalization
```

缺少产品或期间时在读取成本输入前停止，不返回金额，也不创建 Artifact。合法完成的成本报告以租户+Run/message 幂等生成 Artifact；软删除 Artifact 不修改原报告、哈希或运行记录。

## Durable 控制流

1. Run API 校验 owner、会话和澄清关联，将请求与可信上下文固化到 PostgreSQL；部分唯一索引阻止同一会话并发活跃 Run。
2. Worker 领取 Run、维持租约并追加有序事件。
3. LangGraph 每个节点后写 checkpoint，Worker 只追加尚未持久化的图事件。
4. 租约过期后新 Worker 从同一 checkpoint 接管；活动节点允许至少一次重做。
5. 图终态经 `finalizing` 原子写入业务记录和结果事件。
6. 终态 checkpoint 按保留策略清理；活跃 Run 不清理。

控制面细节见 [Runtime](runtime.md)、[Harness](harness.md)、[Tool 与 Provider](tool-provider.md) 和 [Event、Trace、Replay 与 Eval](event-trace-replay.md)。

代码锚点：`backend/app/services/cost_data_import_service.py`、`backend/app/services/cost_calculation_service.py`、`backend/app/services/runtime_service.py`、`backend/app/repositories/`、`backend/app/worker.py`。
