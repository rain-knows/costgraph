# 数据/成本核算格式

本文是成本导入格式、费用代码和确定性计算口径的事实 owner。数据库字段与约束见[数据字典](data-dictionary.md)，HTTP 响应见[成本数据契约](../api/cost-data-contract.md)。样例 JSON 只用于导入，不是运行时 Repository。

## 核算对象与事件图

一次购置或工艺步骤是一条成本事件。事件类型固定为：

- `purchase`：购置原材料或外购半成品，没有上游投入，产生一个可领用输出批次。
- `process`：注塑、喷漆、电镀、装配等通用工艺代码均使用这一类型；消费一个或多个上游批次，发生本步骤费用，并产生一个新的输出批次。

每个事件必须且只能生成一个唯一 `output_batch_id`。`cost_event_inputs` 记录实际领用，不是计划 BOM：一个工艺可有多个投入，一个上游批次可部分领用或分流到多个后续事件。最终零件类型为 `finished_good` 的事件构成完工批次列表；查询期间由该事件的 `completion_time` 推导。

```text
PP/EPDM 单件料包购置 ─> 注塑成型 ─> 火焰处理与表皮包覆 ─┐
                                                          ├─> 卡扣压装与门板总成装配 ─> 左前门内饰板总成
外购门板金属卡扣套件 ────────────────────────────────────┘
```

图必须为 DAG。服务端使用 `graphlib.TopologicalSorter` 校验和排序；孤立引用、自环、间接环路、跨租户、跨发布快照、单位不一致及累计超量领用均使整个导入批次无法发布。

## 四份导入文件

导入命令一次读取 `data/samples/` 下四个顶层 JSON 数组。`tenant_id` 与 `source_system` 来自可信导入上下文，`batch_id` 由导入服务生成，均不由数组记录自行指定。所有 ID 是来源内稳定、非空的字符串；未知字段拒绝导入。

### `parts.json`

| 字段 | 必填 | 约束与语义 |
| --- | --- | --- |
| `part_id` | 是 | 零件内部稳定 ID，同一租户唯一 |
| `part_number` | 是 | 零件号，用于搜索与展示 |
| `part_description` | 是 | 零件描述 |
| `part_type` | 是 | `raw_material/purchased_semi_finished/work_in_progress/finished_good` |
| `product_family` | 否 | 可空产品族 |
| `unit` | 是 | 数量单位；同一领用关系两端必须一致 |
| `source_record_id` | 是 | 来源系统中的零件记录 ID |

```json
{
  "part_id": "PART-FG-A",
  "part_number": "FG-A",
  "part_description": "左前门内饰板总成",
  "part_type": "finished_good",
  "product_family": "装饰件",
  "unit": "件",
  "source_record_id": "ITEM-FG-A"
}
```

### `cost_events.json`

| 字段 | 必填 | 约束与语义 |
| --- | --- | --- |
| `event_id` | 是 | 事件稳定 ID，同一租户唯一 |
| `event_type` | 是 | `purchase/process` |
| `output_batch_id` | 是 | 本事件唯一输出批次 ID，同一租户唯一 |
| `part_id` | 是 | 指向 `parts.part_id`；表示本事件输出的零件 |
| `period` | 是 | `YYYY-MM`；必须与 `completion_time` 所在月份一致 |
| `cost_center_code/name` | 工艺事件 | 成本中心代码与车间名称；购置事件可空 |
| `work_order_number` | 工艺事件 | 车间工单号；购置事件可空 |
| `lot_number` | 是 | 批号 |
| `process_code/name` | 工艺事件 | 通用工艺代码与名称；不把四种样例工艺写死为枚举 |
| `completion_time` | 是 | ISO 8601 完工时间；决定最终批次期间 |
| `qualified_quantity` | 是 | 合格数量，四位小数且不小于零 |
| `defective_quantity` | 是 | 不良数量，四位小数且不小于零 |
| `unit` | 是 | 输出数量单位，必须等于输出零件单位 |
| `machine_hours/labor_hours` | 工艺事件 | 机器与人工工时，四位小数且不小于零；购置事件为零 |
| `source_record_id` | 是 | 来源系统事件 ID |
| `raw_payload` | 否 | 若提供必须是 JSON 对象；导入器会保存原始行用于审计追溯，不参与计算 |

`completed_quantity = qualified_quantity + defective_quantity` 且必须大于零。若某事件全部不良（合格量为零），它不能作为后续投入来源，且没有可计算的转移单位成本；一次工艺事件只对应这一次产成品状态变化，后续工艺必须通过新的事件和投入边表达，禁止原地修改上一步输出批次。

### `cost_event_inputs.json`

| 字段 | 必填 | 约束与语义 |
| --- | --- | --- |
| `input_id` | 是 | 投入边稳定 ID，同一租户唯一 |
| `event_id` | 是 | 消费投入的目标 `process` 事件 |
| `source_event_id` | 是 | 生产被领用输出批次的上游事件 |
| `consumed_quantity` | 是 | 实际领用合格数量，四位小数且大于零 |
| `unit` | 是 | 必须与来源事件输出单位一致 |
| `source_record_id` | 是 | 来源系统领料行 ID |
| `raw_payload` | 否 | 若提供必须是 JSON 对象；导入器会保存原始行用于审计追溯 |

同一 `source_event_id` 的所有投入边合计不得超过来源事件的 `qualified_quantity`。不良数量不进入可领用数量；其成本由合格品承接，随合格品的投入边继续向上卷积。

### `cost_records.json`

| 字段 | 必填 | 约束与语义 |
| --- | --- | --- |
| `cost_record_id` | 是 | 逐笔费用稳定 ID，同一租户唯一 |
| `event_id` | 是 | 费用归集的购置或工艺事件 |
| `cost_code` | 是 | 下节固定 48 项之一 |
| `amount` | 是 | 非负 CNY 金额，两位小数 |
| `currency` | 是 | 固定 `CNY` |
| `incurred_at` | 是 | ISO 8601 费用发生时间 |
| `source_document_no` | 是 | 来源单据号 |
| `source_document_line` | 否 | 来源单据行号，可空 |
| `source_record_id` | 是 | 来源系统费用记录 ID |
| `raw_payload` | 否 | JSON 对象，保存审计所需原始字段，不参与金额计算 |

数据库另保存导入上下文中的 `source_system`。`source_system + source_document_no + source_document_line + source_record_id` 用于从批次汇总追到来源记录；模型生成文本不能替代这些字段。

## 48 项费用代码

代码及归组固定，不接受导入方自行扩展。两个“仓储保管费”分别属于制造费用和制造后费用，必须使用不同代码。

| # | 成本组 | `cost_code` | 中文标签 |
| ---: | --- | --- | --- |
| 1 | `direct_material` | `raw_material` | 原材料 |
| 2 | `direct_material` | `purchased_semi_finished` | 外购半成品 |
| 3 | `direct_material` | `outsourced_processing` | 外协加工费 |
| 4 | `direct_labor` | `direct_wages` | 工资 |
| 5 | `direct_labor` | `direct_welfare` | 福利费 |
| 6 | `direct_labor` | `direct_social_insurance` | 社会保险费 |
| 7 | `direct_labor` | `direct_housing_fund` | 住房公积金 |
| 8 | `direct_labor` | `direct_commercial_insurance` | 员工商业保险 |
| 9 | `direct_labor` | `direct_disability_fund` | 残疾人保障金 |
| 10 | `direct_labor` | `direct_employee_education` | 职工教育经费 |
| 11 | `direct_energy` | `water` | 水费 |
| 12 | `direct_energy` | `electricity` | 电费 |
| 13 | `direct_energy` | `natural_gas` | 天然气费 |
| 14 | `indirect_labor` | `indirect_wages` | 工资 |
| 15 | `indirect_labor` | `indirect_welfare` | 福利费 |
| 16 | `indirect_labor` | `indirect_social_insurance` | 社会保险费 |
| 17 | `indirect_labor` | `indirect_housing_fund` | 住房公积金 |
| 18 | `indirect_labor` | `indirect_commercial_insurance` | 员工商业保险 |
| 19 | `indirect_labor` | `indirect_disability_fund` | 残疾人保障金 |
| 20 | `indirect_labor` | `indirect_employee_education` | 职工教育经费 |
| 21 | `indirect_labor` | `allocated_indirect_labor` | 分摊人工（间接部门） |
| 22 | `main_equipment_depreciation` | `equipment_depreciation` | 设备类 |
| 23 | `main_equipment_depreciation` | `building_depreciation` | 房屋类 |
| 24 | `main_equipment_depreciation` | `office_electronics_depreciation` | 办公电子类 |
| 25 | `main_equipment_depreciation` | `vehicle_depreciation` | 运输类 |
| 26 | `main_equipment_depreciation` | `intangible_asset_amortization` | 无形资产摊销 |
| 27 | `manufacturing_overhead` | `workshop_depreciation_amortization` | 车间折旧及摊销（除主设备折旧） |
| 28 | `manufacturing_overhead` | `allocated_indirect_depreciation` | 分摊折旧（间接部门） |
| 29 | `manufacturing_overhead` | `workshop_indirect_energy` | 车间间接能耗 |
| 30 | `manufacturing_overhead` | `allocated_indirect_energy` | 分摊能耗（间接部门） |
| 31 | `manufacturing_overhead` | `allocated_indirect_overhead` | 分摊制费（间接部门） |
| 32 | `manufacturing_overhead` | `rent` | 租赁费 |
| 33 | `manufacturing_overhead` | `manufacturing_storage_fee` | 仓储保管费（制造） |
| 34 | `manufacturing_overhead` | `loading_handling` | 装卸搬运费 |
| 35 | `manufacturing_overhead` | `testing_inspection` | 试验检测费 |
| 36 | `manufacturing_overhead` | `repair` | 维修费 |
| 37 | `manufacturing_overhead` | `maintenance` | 维保费 |
| 38 | `manufacturing_overhead` | `safety_environmental` | 安全环保 |
| 39 | `manufacturing_overhead` | `low_value_consumables` | 低值易耗品 |
| 40 | `manufacturing_overhead` | `machine_material_consumption` | 机物料消耗 |
| 41 | `manufacturing_overhead` | `labor_protection` | 劳动保护费 |
| 42 | `manufacturing_overhead` | `office_expense` | 办公费 |
| 43 | `manufacturing_overhead` | `travel_expense` | 差旅费 |
| 44 | `manufacturing_overhead` | `business_entertainment` | 业务招待费 |
| 45 | `manufacturing_overhead` | `other_manufacturing_overhead` | 其他 |
| 46 | `post_manufacturing` | `after_sales_compensation` | 售后赔偿费 |
| 47 | `post_manufacturing` | `transportation` | 运输费 |
| 48 | `post_manufacturing` | `post_manufacturing_storage_fee` | 仓储保管费（制造后） |

## 卷积、数量与舍入

对事件 `e`，按 48 个叶级代码建立成本向量：

```text
本次费用(e, code) = Σ cost_records.amount
继承费用(e, code) = Σ 上游投入边.分配费用(code)
累计费用(e, code) = 本次费用(e, code) + 继承费用(e, code)
边分配比例 = consumed_quantity / source.qualified_quantity
边分配费用(code) = source.累计费用(code) × 边分配比例
```

- 不良品发生的全部成本留在来源事件累计向量中，但不增加可领用数量。因此后续领用按 `累计费用 / qualified_quantity` 转移，全部领用合格品时会带走来源事件全部成本。
- 对同一来源事件按稳定的投入边顺序逐叶分配。部分领用按比例保留两位金额；累计领用恰好等于全部合格量时，最后一条边承接每个叶级代码的舍入尾差，保证“已分配合计 = 来源累计费用”。
- 累计结果、三视图、单位成本、比例和占比均为服务端派生值，不写回成本事实表。
- 金额与单位成本使用 `Decimal`、两位小数和 `ROUND_HALF_UP`；数量与工时四位小数；边分配比例六位小数；合格率和占比显示为两位百分比。HTTP 中所有 Decimal 均序列化为字符串。

## 三套成本视图

所有公式先在批次总金额上计算，再以 `completed_quantity = qualified_quantity + defective_quantity` 求展示单位成本。`unit(x) = x / completed_quantity`，最后按金额规则舍入。

### 六类制造成本

六组分别为 `direct_material`、`direct_labor`、`direct_energy`、`indirect_labor`、`main_equipment_depreciation` 和 `manufacturing_overhead`。每组返回稳定的全部叶项、组金额、组单位成本及其占制造成本总额的 `share`；叶项 `share` 是该叶项占所属组金额的百分比；零值叶项不得省略。`share` 均为 0..100 的两位百分数。

```text
manufacturing_total = 直接材料 + 直接人工 + 直接能耗
                    + 间接人工 + 主设备折旧 + 制造费用
制造单位成本1 = unit(manufacturing_total)
```

### 料工费

```text
料 = 直接材料
工 = 直接人工 + 间接人工
费 = 直接能耗 + 主设备折旧 + 制造费用
单位成本1 = unit(料 + 工 + 费)
```

### 变动/固定

```text
变动成本1 = 直接材料 + 直接人工 + 直接能耗
固定成本1 = 间接人工 + 主设备折旧 + 制造费用
制造单位成本1 = unit(变动成本1 + 固定成本1)

变动成本2 = 变动成本1 + 售后赔偿费 + 运输费
固定成本2 = 固定成本1 + 制造后仓储保管费
合计单位成本2 = unit(变动成本2 + 固定成本2)
```

恒等式必须成立：`六类制造成本合计 = 料 + 工 + 费 = 变动成本1 + 固定成本1`；制造后总成本为三项制造后费用之和；`total_cost_2 = variable_cost_2 + fixed_cost_2`。

## API 结构示例

批次列表只返回服务端已计算的三视图，详情再追加完整追溯图。以下为裁剪示例；正式字段以[成本数据契约](../api/cost-data-contract.md)为准。

```json
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
  "qualified_quantity": "950.0000",
  "defective_quantity": "50.0000",
  "completed_quantity": "1000.0000",
  "quality_rate": "95.00",
  "manufacturing_view": {
    "groups": [],
    "total": { "amount": "50000.00", "unit_cost": "50.00" }
  },
  "material_labor_overhead_view": {
    "material": { "amount": "20000.00", "unit_cost": "20.00" },
    "labor": { "amount": "12000.00", "unit_cost": "12.00" },
    "overhead": { "amount": "18000.00", "unit_cost": "18.00" },
    "total": { "amount": "50000.00", "unit_cost": "50.00" }
  },
  "variable_fixed_view": {
    "variable_cost_1": { "amount": "30000.00", "unit_cost": "30.00" },
    "fixed_cost_1": { "amount": "20000.00", "unit_cost": "20.00" },
    "manufacturing_total": { "amount": "50000.00", "unit_cost": "50.00" },
    "after_sales_compensation": { "amount": "1000.00", "unit_cost": "1.00" },
    "transportation": { "amount": "2000.00", "unit_cost": "2.00" },
    "storage_fee": { "amount": "500.00", "unit_cost": "0.50" },
    "variable_cost_2": { "amount": "33000.00", "unit_cost": "33.00" },
    "fixed_cost_2": { "amount": "20500.00", "unit_cost": "20.50" },
    "total_cost_2": { "amount": "53500.00", "unit_cost": "53.50" }
  }
}
```

详情的 `trace` 固定包含：

- `root_event_id`：最终产成品事件。
- `nodes`：事件身份、输出零件与数量、`direct_costs/inherited_costs/accumulated_costs`、展示单位成本和按合格量计算的转移单位成本。
- `edges`：`source_event_id -> target_event_id`、领用量、分配比例和 48 项叶级分配向量。
- `records`：逐笔费用、归组、发生时间和来源单据字段。

## 黄金批次

样例核算对象固定为汽车装饰件工厂，包含左前门内饰板总成、中控台钢琴黑装饰面板和仪表板真空镀铬装饰条 3 个产成品，跨 2 个期间形成 6 个最终批次。黄金批次追溯链包含原材料与外购卡扣两类投入，以及“注塑成型 → 火焰处理与表皮包覆 → 卡扣压装与门板总成装配”三道连续生产工序；同时覆盖多投入、部分领用、批次分流、零/非零不良品和三项制造后费用。

其中黄金批次完工数量为 `1000.0000`，合格 `950.0000`，不良 `50.0000`，合格率 `95.00%`。六类制造成本金额固定为：

| 直接材料 | 直接人工 | 直接能耗 | 间接人工 | 主设备折旧 | 制造费用 | 制造合计 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `20000.00` | `8000.00` | `2000.00` | `4000.00` | `3000.00` | `13000.00` | `50000.00` |

对应料/工/费单位成本为 `20.00/12.00/18.00`；变动成本1、固定成本1与制造单位成本1为 `30.00/20.00/50.00`。售后赔偿、运输、制造后仓储单位成本为 `1.00/2.00/0.50`，因此变动成本2、固定成本2与合计单位成本2为 `33.00/20.50/53.50`。API、Agent report schema `2.0` 与 Artifact 必须得到完全一致的值。
