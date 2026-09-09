import { apiRequest } from './http'

export type DecimalValue = string

export type PartIdentity = {
  part_id: string
  part_number: string
  part_description: string
  part_type: 'raw_material' | 'purchased_semi_finished' | 'work_in_progress' | 'finished_good'
  product_family: string | null
}

export type CostMetric = {
  amount: DecimalValue
  unit_cost: DecimalValue
}

export type ManufacturingCostLeaf = {
  cost_code: string
  label: string
  metric: CostMetric
  share: DecimalValue
}

export type ManufacturingCostGroup = {
  group_code: string
  group_label: string
  metric: CostMetric
  share: DecimalValue
  leaves: ManufacturingCostLeaf[]
}

export type ManufacturingCostView = {
  groups: ManufacturingCostGroup[]
  total: CostMetric
}

export type MaterialLaborOverheadView = {
  material: CostMetric
  labor: CostMetric
  overhead: CostMetric
  total: CostMetric
}

export type VariableFixedCostView = {
  variable_cost_1: CostMetric
  fixed_cost_1: CostMetric
  manufacturing_total: CostMetric
  after_sales_compensation: CostMetric
  transportation: CostMetric
  storage_fee: CostMetric
  variable_cost_2: CostMetric
  fixed_cost_2: CostMetric
  total_cost_2: CostMetric
}

export type FinishedBatchIdentity = {
  finished_batch_id: string
  event_id: string
  part: PartIdentity
  period: string
  completion_time: string
  cost_center_code: string | null
  cost_center_name: string | null
  work_order_number: string | null
  lot_number: string | null
  process_code: string | null
  process_name: string | null
  qualified_quantity: DecimalValue
  defective_quantity: DecimalValue
  completed_quantity: DecimalValue
  quality_rate: DecimalValue
  unit: string
  machine_hours: DecimalValue
  labor_hours: DecimalValue
}

export type FinishedBatchCostSummary = FinishedBatchIdentity & {
  manufacturing_view: ManufacturingCostView
  material_labor_overhead_view: MaterialLaborOverheadView
  variable_fixed_view: VariableFixedCostView
}

export type FinishedBatchCostList = {
  period: string
  items: FinishedBatchCostSummary[]
  page: number
  page_size: number
  total: number
}

export type CostVectorItem = {
  cost_code: string
  cost_group: string
  label: string
  amount: DecimalValue
}

export type CostVector = {
  items: CostVectorItem[]
  total_amount: DecimalValue
}

export type CostTraceNode = {
  event_id: string
  event_type: 'purchase' | 'process'
  output_batch_id: string
  part: PartIdentity
  completion_time: string
  cost_center_code: string | null
  cost_center_name: string | null
  work_order_number: string | null
  lot_number: string | null
  process_code: string | null
  process_name: string | null
  qualified_quantity: DecimalValue
  defective_quantity: DecimalValue
  completed_quantity: DecimalValue
  unit: string
  direct_costs: CostVector
  inherited_costs: CostVector
  accumulated_costs: CostVector
  display_unit_cost: DecimalValue
  transfer_unit_cost: DecimalValue | null
}

export type CostTraceEdge = {
  input_id: string
  source_event_id: string
  target_event_id: string
  consumed_quantity: DecimalValue
  unit: string
  allocation_ratio: DecimalValue
  allocated_costs: CostVector
}

export type CostTraceRecord = {
  cost_record_id: string
  event_id: string
  cost_code: string
  cost_group: string
  cost_label: string
  amount: DecimalValue
  currency: 'CNY'
  incurred_at: string
  source_system: string
  source_document_no: string
  source_document_line: string | null
  source_record_id: string
  raw_payload: Record<string, unknown> | null
}

export type CostTraceGraph = {
  root_event_id: string
  nodes: CostTraceNode[]
  edges: CostTraceEdge[]
  records: CostTraceRecord[]
}

export type FinishedBatchCostDetail = FinishedBatchCostSummary & {
  trace: CostTraceGraph
}

export type CostOverview = {
  period: string
  currency: 'CNY'
  batch_count: number
  part_count: number
  completed_quantity: DecimalValue
  qualified_quantity: DecimalValue
  defective_quantity: DecimalValue
  quality_rate: DecimalValue
  manufacturing_cost: DecimalValue
  post_manufacturing_cost: DecimalValue
  total_cost: DecimalValue
}

export type FinishedBatchSort = 'completion_time_asc' | 'completion_time_desc' | 'unit_cost_asc' | 'unit_cost_desc'

export const getCostOverview = (period: string, signal?: AbortSignal) =>
  apiRequest<CostOverview>(`/api/cost-data/overview?period=${encodeURIComponent(period)}`, { signal })

export function listFinishedBatchCosts(options: {
  period: string
  query?: string
  costCenterCode?: string
  sort?: FinishedBatchSort
  page?: number
  pageSize?: number
  signal?: AbortSignal
}) {
  const params = new URLSearchParams({
    period: options.period,
    sort: options.sort ?? 'completion_time_desc',
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 20),
  })
  if (options.query?.trim()) params.set('query', options.query.trim())
  if (options.costCenterCode?.trim()) params.set('cost_center_code', options.costCenterCode.trim())
  return apiRequest<FinishedBatchCostList>(`/api/cost-data/finished-batches?${params}`, { signal: options.signal })
}

export const getFinishedBatchCost = (finishedBatchId: string, signal?: AbortSignal) =>
  apiRequest<FinishedBatchCostDetail>(`/api/cost-data/finished-batches/${encodeURIComponent(finishedBatchId)}`, { signal })
