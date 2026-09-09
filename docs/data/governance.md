# 数据/治理与质量

## 导入闭环

```text
source records -> normalized snapshot hash -> validating batch
  -> row and relation validation -> error records
  -> all valid -> transactional publish
  -> previous published batch becomes superseded
  -> read services use only the current published snapshot
```

`data_load_batches.status` 只允许 `created`、`validating`、`validated`、`published`、`superseded`、`failed`。数据库部分唯一索引保证每个租户最多一个 `published` 批次；发布新批次与替换旧批次在同一事务完成。相同租户、来源系统和快照哈希幂等复用，不重复导入。

## 数据质量

- `period` 使用 `YYYY-MM`，并与 `production_date` 同月。
- `qualified_output_qty > 0`；成本 `amount >= 0`；`process_sort > 0`。
- `cost_item` 只允许 `material/labor/equipment/energy/overhead`，币种固定为 `CNY`。
- 每条产品、产量和成本事实属于一个租户和一个导入批次，关联不能跨租户或跨批次。
- 批次计数非负，且 `valid_rows + error_rows <= total_rows`。
- 任何错误行使整个批次无法发布；错误详情写入 `data_load_errors`，不能被 Agent 或成本 API 查询。

应用校验由 `backend/app/services/cost_data_import_service.py` 执行，数据库约束作为最终门禁。

## 隔离与追溯

- 成本数据以 `tenant_id` 隔离，产品/期间 scope 由服务端执行上下文确定；客户端查询条件不能扩大授权范围。
- 会话、Run、事件、Trace 与产出以 `tenant_id + principal_id` 隔离；不属于当前 owner 的对象表现为未找到。
- 报告和 Artifact 保存 `data_snapshot_id`、报告哈希及 report/rule/prompt/code 版本，用于重现输入版本，不保存完整敏感源数据。
- Artifact 为 owner 范围内软删除，可从回收站恢复；删除与恢复不修改原报告、哈希、Turn 或审计记录。
- Audit Trace 只保存允许的运行元数据、usage、策略快照和规范化哈希；敏感内容边界见 [Event、Trace、Replay 与 Eval](../architecture/event-trace-replay.md)。
- 终态 checkpoint 默认按服务端保留期清理，活跃 Run 不清理；删除终态会话时同步清除关联 checkpoint。

## 当前不提供

完整身份认证、数据库 RLS、导入审批 UI、质量看板、自动备份和生产恢复演练不属于当前仓库能力。当前 principal 由服务端可信配置注入，不能描述为完整认证系统。
