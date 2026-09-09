# 架构/Event、Trace、Replay 与 Eval

## Runtime Event

公开 SSE 事件协议版本为 `2.0`，事件类型固定为：

```text
status | node | tool | policy | budget | clarification | result | error
```

每条持久化事件包含递增 sequence、稳定 event key、kind、name、status、summary、时间和可选的预算、耗时或脱敏 details。Run 行锁原子分配 sequence，event key 防止 Worker 恢复与重试重复写入。入队状态使用 `queued`。

SSE 使用数据库事件 ID 作为游标，支持 `Last-Event-ID` 和 `after_event_id`，取两者较大值；先重放遗漏事件，再等待新事件。心跳是非持久化注释。客户端按 sequence 去重，收到终态结果、错误或取消后关闭连接。

模型调用的内部事件在持久化边界投影为允许的公开类型，并通过 name/details 保留操作语义，不能新增未声明的公网事件类型。

## Trace 与脱敏

Public Trace schema 为 `2.0`，供 Inspector 和报表展示；Audit Trace schema 为 `3.0`，保存 Runtime/Workflow/Provider/Tool 版本、能力与策略快照、调用状态、预算、usage 和规范化 SHA-256。

两类 Trace 均禁止保存 Prompt、会话正文、模型正文、reasoning content、完整工具参数、确定性计算原始输入、SQL、密钥和成本来源记录 ID。哈希只用于一致性与审计关联，不能替代原始业务事实。

## Replay 与 Eval

Replay 注入固定 Fixture Provider 和 Tool Registry，重新执行同一 LangGraph，而不是读取已保存结果。它按顺序消费响应，并验证脱敏参数摘要、输入/输出 Schema、节点顺序、能力/策略快照、`outcome` 与稳定 `report_json`；额外调用、剩余响应、顺序或摘要不一致均失败。离线 Eval 使用 `backend/evaluation/fixture_model_provider.py` 显式注入同类 Provider；生产图没有隐式模型回退。

Replay 不访问真实模型或成本 Repository，重新生成的时间戳不参与稳定比较。离线 Eval 还必须验证事件结构、服务端能力交集、澄清先于事实读取、版本元数据和脱敏；多次 trial 只有全部通过才算 Case 通过。真实 DeepSeek 验证单独执行和记录，不能由 fixture 结果替代。

依据：`backend/app/agent/runtime_services.py`、`backend/app/agent/trace.py`、`backend/app/agent/replay.py`、`backend/scripts/evaluate_agent.py`、`backend/app/repositories/run_repository.py`。
