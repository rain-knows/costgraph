# API/成本数据契约

三条接口均为只读，使用服务端注入的 `tenant_id + principal_id + data_scope` 查询当前已发布快照。客户端不能提交租户、主体、金额或授权范围。响应由 `backend/app/schemas/cost_data.py` 的 Pydantic 契约约束；Decimal 在 JSON 中作为字符串传输，币种固定为 `CNY`。

## 总览

```text
GET /api/cost-data/overview?period=YYYY-MM
```

响应 `CostOverview`：

| 字段 | 语义 |
| --- | --- |
| `period/currency` | 请求期间与 `CNY` |
| `product_count` | 该期间有可计算数据且在 scope 内的产品数 |
| `total_output_qty` | 产品合格产量之和，2 位 Decimal 字符串 |
| `total_cost` | 产品总成本之和，2 位 Decimal 字符串 |
| `average_unit_cost` | `total_cost / total_output_qty`，无产量时为 `null` |
| `comparison` | 有上月可比数据时返回 `previous_period`、总成本/单位成本差额与百分比，否则 `null` |

期间无数据时返回合法的零值总览，不伪造产品或趋势。

## 产品列表

```text
GET /api/cost-data/products?period=YYYY-MM&query=&sort=product_id&page=1&page_size=20
```

- `period` 必填且必须是合法月份。
- `query` 最长 200 字符，对产品 ID、名称与规格进行不区分大小写的包含搜索。
- `sort` 允许 `product_id/product_name/total_cost_asc/total_cost_desc/unit_cost_asc/unit_cost_desc`，默认 `product_id`。
- `page >= 1`；`page_size` 为 1 到 100，默认 20。

响应 `ProductPeriodList` 包含 `period/items/page/page_size/total`。每个 `ProductPeriodSummary` 包含产品 ID、名称、可空规格、期间、币种、产量、总成本、单位成本，以及可空上月比较。比较对象额外给出上月总成本、上月单位成本、差额和百分比。无匹配数据时 `items=[]` 且 `total=0`。

## 产品详情

```text
GET /api/cost-data/products/{product_id}?period=YYYY-MM
```

响应 `ProductPeriodDetail`：

- `product`：`product_id/product_name/spec`。
- `period/currency/output_qty/total_cost/unit_cost/comparison`：与列表使用同一计算和比较服务。
- `processes`：按 `process_sort + process_code` 排序；每项包含工序代码、名称、顺序、总成本和成本项。
- `processes[].items`：`cost_item/label/amount/source_record_count`，成本项为材料、人工、设备、能耗或制造费用。
- `source_summary`：产量记录数、成本明细记录数、来源起止日期和去重后的来源系统，不返回原始记录或敏感载荷。

当前详情层级严格为“产品 -> 工序 -> 成本项 -> 来源摘要”；接口不推断批次节点、BOM、半成品或异常工单。

## 计算与比较

- 总成本为范围内成本项 Decimal 求和；单位成本为总成本除以合格产量。
- 金额、数量和单位成本输出 2 位，使用 `ROUND_HALF_UP`。
- 比较期间为自然上月；百分比为 `delta / previous * 100` 并保留 2 位。上期基数为零时百分比返回 `0.00`，没有上月数据时整个比较为 `null`。
- 总览、列表、详情、Agent 报告和 Artifact 必须复用同一确定性计算服务，不允许前端独立重算权威结果。

## 错误

- 非法期间、sort 或分页：`422 validation_error`。
- 产品/期间不存在，或不在数据范围内：`404 cost_data_not_found`。
- 主体没有成本读取能力：`403 cost_data_access_denied`。
- PostgreSQL 暂时不可用：`503 database_unavailable`。

错误响应遵守 [Agent 契约](agent-contract.md) 的 request ID 与脱敏外壳。

依据：`backend/app/api/cost_data.py`、`backend/app/schemas/cost_data.py`、`backend/app/services/cost_data_query_service.py`、`backend/app/services/cost_calculation_service.py`、`backend/app/repositories/postgres_cost_repository.py`。
