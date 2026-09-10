# 架构/数据流

## 成本导入

```text
parts/cost_events/cost_event_inputs/cost_records JSON
  -> CostDataImporter -> source snapshot SHA-256
  -> data_load_batches -> field/relation/DAG/business validation
  -> data_load_errors for rejected rows -> transactional publish
  -> previous published batch becomes superseded
  -> application reads only the new published snapshot
```

JSON 是导入输入，不是应用请求的 Repository。失败批次不可查询；相同租户、来源系统和快照哈希幂等跳过。导入目标始终是独立 CostGraph PostgreSQL 数据库。

规范样例以汽车装饰件工厂为业务语境。黄金批次从 PP/EPDM 单件料包与外购卡扣开始，经过注塑成型、火焰处理与表皮包覆、总成装配三道工艺后形成左前门内饰板总成，用于验证多层、多投入成本卷积与追溯。

## 成本只读查询

```text
HTTP query -> server principal/tenant/data scope
  -> published parts/events/input edges/cost records
  -> topological convolution of leaf cost vectors
  -> six-class + material/labor/overhead + variable/fixed summaries
  -> CostOverview | FinishedBatchCostList | FinishedBatchCostDetail
```

每个 `purchase/process` 事件只生成一个输出批次；`cost_event_inputs` 将上游输出批次的实际领用量连接到下游工艺事件，支持多投入、部分领用和同一批次分流。服务端使用 `graphlib.TopologicalSorter` 校验和排序事件图，按叶级费用向量逐边分配；完整领用的最后一条边承接舍入尾差。总览、批次列表、批次详情、Agent 报告和 Artifact 复用同一确定性结果，前端不重算金额。

追溯图以最终产成品事件为根向上返回事件节点、投入边和逐笔来源记录。树形 UI 遇到共享上游只显示引用标识；图中的一个事件仍只计算一次，不能因投影成树而重复累计。

## Agent 查询与产出

```text
HTTP -> conversation context -> capability/data-scope gate
  -> finished part + period -> clarification gate
  -> published event graph -> Decimal convolution
  -> report schema 2.0 + lineage + trace
  -> atomic Run/Turn/Message/Audit/Artifact finalization
```

缺少唯一产成品零件或期间时在读取成本输入前停止，不返回金额，也不创建 Artifact。合法完成的成本报告以租户+Run/message 幂等生成 Artifact；软删除 Artifact 不修改原报告、哈希或运行记录。

## Durable 控制流

1. Run API 校验 owner、会话和澄清关联，将请求与可信上下文固化到 PostgreSQL；部分唯一索引阻止同一会话并发活跃 Run。
2. Worker 领取 Run、维持租约并追加有序事件。
3. LangGraph 每个节点后写 checkpoint，Worker 只追加尚未持久化的图事件。
4. 租约过期后新 Worker 从同一 checkpoint 接管；活动节点允许至少一次重做。
5. 图终态经 `finalizing` 原子写入业务记录和结果事件。
6. 终态 checkpoint 按保留策略清理；活跃 Run 不清理。

控制面细节见 [Runtime](runtime.md)、[Harness](harness.md)、[Tool 与 Provider](tool-provider.md) 和 [Event、Trace、Replay 与 Eval](event-trace-replay.md)。

代码锚点：`backend/app/services/cost_data_import_service.py`、`backend/app/services/cost_data_query_service.py`、`backend/app/domain/cost.py`、`backend/app/services/lineage_service.py`、`backend/app/services/runtime_service.py`、`backend/app/repositories/`、`backend/app/worker.py`。
