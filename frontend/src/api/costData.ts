import { apiRequest } from './http'

export type DecimalValue = string
export type PeriodComparison = {
  previous_period: string
  total_cost_delta: DecimalValue
  total_cost_delta_rate: DecimalValue
  unit_cost_delta: DecimalValue
  unit_cost_delta_rate: DecimalValue
}

export type CostOverview = {
  period: string
  currency: 'CNY'
  product_count: number
  total_output_qty: DecimalValue
  total_cost: DecimalValue
  average_unit_cost: DecimalValue | null
  comparison: PeriodComparison | null
}

export type ProductPeriodSummary = {
  product_id: string
  product_name: string
  spec: string | null
  period: string
  currency: 'CNY'
  output_qty: DecimalValue
  total_cost: DecimalValue
  unit_cost: DecimalValue
  comparison: (PeriodComparison & { previous_total_cost: DecimalValue; previous_unit_cost: DecimalValue }) | null
}

export type ProductListResponse = {
  period: string
  items: ProductPeriodSummary[]
  page: number
  page_size: number
  total: number
}

export type ProductPeriodDetail = {
  product: { product_id: string; product_name: string; spec: string | null }
  period: string
  currency: 'CNY'
  output_qty: DecimalValue
  total_cost: DecimalValue
  unit_cost: DecimalValue
  comparison: ProductPeriodSummary['comparison']
  processes: Array<{
    process_code: string
    process_name: string
    process_sort: number
    total_cost: DecimalValue
    items: Array<{ cost_item: string; label: string; amount: DecimalValue; source_record_count: number }>
  }>
  source_summary: {
    production_output_count: number
    process_cost_entry_count: number
    start_date: string
    end_date: string
    source_systems: string[]
  }
}

export const getCostOverview = (period: string, signal?: AbortSignal) =>
  apiRequest<CostOverview>(`/api/cost-data/overview?period=${encodeURIComponent(period)}`, { signal })

export function listProductCosts(options: { period: string; query?: string; sort?: string; page?: number; pageSize?: number; signal?: AbortSignal }) {
  const params = new URLSearchParams({
    period: options.period,
    sort: options.sort ?? 'product_id',
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 20),
  })
  if (options.query?.trim()) params.set('query', options.query.trim())
  return apiRequest<ProductListResponse>(`/api/cost-data/products?${params}`, { signal: options.signal })
}

export const getProductCost = (productId: string, period: string, signal?: AbortSignal) =>
  apiRequest<ProductPeriodDetail>(`/api/cost-data/products/${encodeURIComponent(productId)}?period=${encodeURIComponent(period)}`, { signal })
