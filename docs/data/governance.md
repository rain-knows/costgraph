# 数据/治理与质量

## 导入闭环

```text
source records -> normalized snapshot hash -> validating batch
  -> row, relation and DAG validation -> error records
  -> all valid -> transactional publish
  -> previous published batch becomes superseded
  -> read services use only the current published snapshot
```

`data_load_batches.status` 只允许 `created`、`validating`、`validated`、`published`、`superseded`、`failed`。数据库部分唯一索引保证每个租户最多一个 `published` 批次；发布新批次与替换旧批次在同一事务完成。相同租户、来源系统和快照哈希幂等复用，不重复导入。

## 数据质量

- 零件类型只允许 `raw_material/purchased_semi_finished/work_in_progress/finished_good`；事件类型只允许 `purchase/process`。
- 每个事件只生成一个全局唯一的 `output_batch_id`；`qualified_quantity >= 0`、`defective_quantity >= 0` 且二者之和大于零，投入量必须大于零且单位与来源事件一致。合格量为零的事件不得被领用。
- `purchase` 事件不得声明工艺投入；`process` 事件必须有工艺标识，允许一个或多个上游投入。
- 费用代码必须属于固定的 48 项清单，`amount >= 0`，币种固定为 `CNY`。
- 每条零件、事件、投入边和费用记录属于同一租户与同一导入批次；关联不能跨租户、跨快照，不能引用不存在的事件或零件。
- 投入关系必须为 DAG；禁止自环和间接环路。同一来源事件的累计领用量不得超过其合格数量，不良品数量不可领用。
- 批次期间由最终产成品事件的 `completion_time` 推导，不接受另一份可冲突的期间事实。
- 批次计数非负，且 `valid_rows + error_rows <= total_rows`。
- 任何错误行使整个批次无法发布；错误详情写入 `data_load_errors`，不能被 Agent 或成本 API 查询。

费用代码、输入字段、DAG 领用关系、四位数量/两位金额尺度和三套视图恒等式以[成本核算格式](cost-accounting-format.md)为唯一规则来源；本文件只定义发布、隔离和质量门禁。

应用校验由 `backend/app/services/cost_data_import_service.py` 执行，数据库约束作为最终门禁。

## 隔离与追溯

- 成本数据以 `tenant_id` 隔离，产成品零件/期间 scope 由服务端执行上下文确定；客户端查询条件不能扩大授权范围。最终批次可见不自动授予对越权上游节点的读取权限，发布前必须保证其整条可用追溯链满足同一执行范围。
- 会话、Run、事件、Trace 与产出以 `tenant_id + principal_id` 隔离；不属于当前 owner 的对象表现为未找到。
- 报告和 Artifact 保存 `data_snapshot_id`、报告哈希及 report/rule/prompt/code 版本，用于重现输入版本，不保存完整敏感源数据。
- Artifact 为 owner 范围内软删除，可从回收站恢复；删除与恢复不修改原报告、哈希、Turn 或审计记录。
- Audit Trace 只保存允许的运行元数据、usage、策略快照和规范化哈希；敏感内容边界见 [Event、Trace、Replay 与 Eval](../architecture/event-trace-replay.md)。
- 终态 checkpoint 默认按服务端保留期清理，活跃 Run 不清理；删除终态会话时同步清除关联 checkpoint。

## 当前不提供

完整身份认证、数据库 RLS、库存结存、返工分支、副产品、导入审批 UI、质量看板、自动备份和生产恢复演练不属于当前仓库能力。当前 principal 由服务端可信配置注入，不能描述为完整认证系统。
