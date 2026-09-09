# API/成本数据契约

三条 canonical 接口均为只读，使用服务端注入的 `tenant_id + principal_id + data_scope` 查询当前已发布快照。客户端不能提交租户、主体、金额、图关系或授权范围。响应由 `backend/app/schemas/cost_data.py` 的 Pydantic 契约约束；所有 Decimal 在 JSON 中作为字符串传输，币种固定为 `CNY`。

## 公共值对象

`PartIdentity` 包含 `part_id/part_number/part_description/part_type/product_family`。

`CostMetric` 包含：

| 字段 | 语义 |
| --- | --- |
| `amount` | 批次总金额，两位 Decimal 字符串 |
| `unit_cost` | 总金额除以完工总量，两位 Decimal 字符串 |

批次身份字段固定为：

- `finished_batch_id/event_id/part/period/completion_time`
- `cost_center_code/cost_center_name/work_order_number/lot_number/process_code/process_name`
- `qualified_quantity/defective_quantity/completed_quantity/quality_rate/unit`
- `machine_hours/labor_hours`

数量和工时为四位 Decimal 字符串；`completed_quantity = qualified_quantity + defective_quantity`；`quality_rate` 是合格量占完工总量的两位百分数。

每个批次返回三套已计算视图：

- `manufacturing_view`：`groups/total`。每个组含 `group_code/group_label/metric/share/leaves`；每个叶项含 `cost_code/label/metric/share`。六组和全部 45 个制造费用叶代码按固定顺序返回，零值不省略。
- `material_labor_overhead_view`：`material/labor/overhead/total`，每项为 `CostMetric`。
- `variable_fixed_view`：`variable_cost_1/fixed_cost_1/manufacturing_total/after_sales_compensation/transportation/storage_fee/variable_cost_2/fixed_cost_2/total_cost_2`，每项为 `CostMetric`。

费用分组、公式、舍入及不良品承接规则见[成本核算格式](../data/cost-accounting-format.md)。前端不得从叶项重新计算权威汇总。

## 总览

```text
GET /api/cost-data/overview?period=YYYY-MM
```

响应 `CostOverview`：

| 字段 | 语义 |
| --- | --- |
| `period/currency` | 请求期间与 `CNY` |
| `batch_count` | scope 内该期间的最终产成品批次数 |
| `part_count` | 上述批次覆盖的产成品零件数 |
| `completed_quantity` | 完工总量；仅作质量规模汇总，不用于跨零件单位成本 |
| `qualified_quantity/defective_quantity/quality_rate` | 质量数量与服务端计算的合格率 |
| `manufacturing_cost` | 六类制造成本总金额 |
| `post_manufacturing_cost` | 售后赔偿、运输和制造后仓储总金额 |
| `total_cost` | 制造成本与制造后费用合计 |

总览不返回跨零件平均单位成本。期间无数据时返回合法的零值总览，不伪造批次、零件或趋势。

## 产成品批次列表

```text
GET /api/cost-data/finished-batches?period=YYYY-MM&query=&cost_center_code=&sort=completion_time_desc&page=1&page_size=20
```

- `period` 必填且必须是合法月份，按最终事件 `completion_time` 归属。
- `query` 最长 200 字符，对零件号、零件描述、成本中心、工单号和批号进行包含搜索。
- `cost_center_code` 是精确成本中心过滤。
- `sort` 只允许 `completion_time_asc/completion_time_desc/unit_cost_asc/unit_cost_desc`，默认 `completion_time_desc`；单位成本排序使用服务端 `total_cost_2.unit_cost`。
- `page >= 1`；`page_size` 为 1 到 100，默认 20。

响应 `FinishedBatchCostList` 包含 `period/items/page/page_size/total`。每个 item 包含公共批次身份和 `manufacturing_view/material_labor_overhead_view/variable_fixed_view`。无匹配数据时 `items=[]` 且 `total=0`。

## 批次详情与追溯图

```text
GET /api/cost-data/finished-batches/{finished_batch_id}
```

响应 `FinishedBatchCostDetail`，包含与列表相同的批次身份、三套视图及完整 `trace: CostTraceGraph`。服务端以当前批次的最终事件为根，返回为其成本提供贡献的可见子图。

`CostTraceGraph` 固定包含：

| 字段 | 结构与语义 |
| --- | --- |
| `root_event_id` | 最终产成品事件 ID |
| `nodes` | 去重后的事件节点；一个事件只出现一次 |
| `edges` | 上游输出到下游工艺事件的实际领用边 |
| `records` | 子图内逐笔费用及来源记录 |

节点包含 `event_id/event_type/output_batch_id/part/completion_time/cost_center_code/cost_center_name/work_order_number/lot_number/process_code/process_name/qualified_quantity/defective_quantity/completed_quantity/unit`，以及：

- `direct_costs`：事件本次发生费用向量。
- `inherited_costs`：所有上游边分配到本事件的费用向量。
- `accumulated_costs`：本次与继承费用之和。
- `display_unit_cost`：累计总额除以完工总量。
- `transfer_unit_cost`：累计总额除以合格量，用于向后续工序转移不良品成本。

三个费用向量结构均为 `items/total_amount`；每个 item 为 `cost_code/cost_group/label/amount`。48 个叶项使用稳定顺序并以金额字符串传输。

投入边包含 `input_id/source_event_id/target_event_id/consumed_quantity/unit/allocation_ratio/allocated_costs`。`allocation_ratio` 是 `consumed_quantity / source.qualified_quantity` 的六位 Decimal 比例；由于数量保留四位而展示比例保留六位，极小但合法的比例可能显示为 `0.000000`，服务端仍按未舍入 Decimal 比例分配金额。`allocated_costs` 使用与节点相同的费用向量结构。树形前端遇到共享上游可显示引用，但不得复制节点金额。

来源记录包含 `cost_record_id/event_id/cost_code/cost_group/cost_label/amount/currency/incurred_at/source_system/source_document_no/source_document_line/source_record_id/raw_payload`。`raw_payload` 仅供详情审计展示，服务端计算不读取它。

## 响应示例

```json
{
  "period": "2026-06",
  "items": [
    {
      "finished_batch_id": "FG-A-2026-06",
      "event_id": "E-FG-001",
      "part": {
        "part_id": "PART-FG-A",
        "part_number": "FG-A",
        "part_description": "装饰总成 A",
        "part_type": "finished_good",
        "product_family": "装饰件"
      },
      "period": "2026-06",
      "completion_time": "2026-06-30T18:00:00+08:00",
      "cost_center_code": "CC-ASSEMBLY",
      "cost_center_name": "装配车间",
      "work_order_number": "WO-FG-001-06",
      "lot_number": "LOT-FG-001-06",
      "process_code": "ASSEMBLY",
      "process_name": "装配",
      "qualified_quantity": "950.0000",
      "defective_quantity": "50.0000",
      "completed_quantity": "1000.0000",
      "quality_rate": "95.00",
      "unit": "件",
      "machine_hours": "120.0000",
      "labor_hours": "260.0000",
      "manufacturing_view": {
        "groups": [],
        "total": {"amount": "50000.00", "unit_cost": "50.00"}
      },
      "material_labor_overhead_view": {
        "material": {"amount": "20000.00", "unit_cost": "20.00"},
        "labor": {"amount": "12000.00", "unit_cost": "12.00"},
        "overhead": {"amount": "18000.00", "unit_cost": "18.00"},
        "total": {"amount": "50000.00", "unit_cost": "50.00"}
      },
      "variable_fixed_view": {
        "variable_cost_1": {"amount": "30000.00", "unit_cost": "30.00"},
        "fixed_cost_1": {"amount": "20000.00", "unit_cost": "20.00"},
        "manufacturing_total": {"amount": "50000.00", "unit_cost": "50.00"},
        "after_sales_compensation": {"amount": "1000.00", "unit_cost": "1.00"},
        "transportation": {"amount": "2000.00", "unit_cost": "2.00"},
        "storage_fee": {"amount": "500.00", "unit_cost": "0.50"},
        "variable_cost_2": {"amount": "33000.00", "unit_cost": "33.00"},
        "fixed_cost_2": {"amount": "20500.00", "unit_cost": "20.50"},
        "total_cost_2": {"amount": "53500.00", "unit_cost": "53.50"}
      }
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

示例裁剪了 `manufacturing_view.groups`，正式响应始终返回全部六组和固定叶项。

## 错误

- 非法期间、sort、过滤或分页：`422 validation_error`。
- 批次不存在，或其追溯链不在数据范围内：`404 cost_data_not_found`。
- 主体没有成本读取能力：`403 cost_data_access_denied`。
- PostgreSQL 暂时不可用：`503 database_unavailable`。

错误响应遵守 [Agent 契约](agent-contract.md) 的 request ID 与脱敏外壳。

依据：`backend/app/api/cost_data.py`、`backend/app/schemas/cost_data.py`、`backend/app/services/cost_data_query_service.py`、`backend/app/services/cost_calculation_service.py`、`backend/app/repositories/postgres_cost_repository.py`。
