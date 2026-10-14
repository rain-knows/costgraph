import { apiRequest } from './http'

export type BatchStatus = 'created' | 'validating' | 'validated' | 'published' | 'superseded' | 'failed'
export type DataAdminBatch = {
  batch_id: string
  status: BatchStatus
  source_system: string
  source_file: string | null
  source_snapshot_hash: string
  calculation_rule_version: string
  total_rows: number
  valid_rows: number
  error_rows: number
  started_at: string
  finished_at: string | null
  created_at: string
}
export type DataAdminBatchList = { items: DataAdminBatch[]; page: number; page_size: number; total: number }
export type ErrorCodeCount = { error_code: string; count: number }
export type DataAdminBatchDetail = DataAdminBatch & { error_codes: ErrorCodeCount[] }
export type DataAdminError = { error_id: number; source_table: string; source_record_id: string | null; error_code: string; error_message: string; created_at: string }
export type DataAdminErrorList = { items: DataAdminError[]; page: number; page_size: number; total: number }
export type PublishedSnapshot = { batch: DataAdminBatch | null; fact_rows: Record<string, number>; projection_rows: number; part_count: number; period_start: string | null; period_end: string | null; rule_version_matches: boolean | null }
export type DataQualitySummary = Omit<PublishedSnapshot, 'batch'> & { published: boolean; error_rows: number }

function params(input: { status?: BatchStatus; page?: number; pageSize?: number } = {}) {
  const value = new URLSearchParams({ page: String(input.page ?? 1), page_size: String(input.pageSize ?? 20) })
  if (input.status) value.set('status', input.status)
  return value
}

export const listDataAdminBatches = (input: { status?: BatchStatus; page?: number; pageSize?: number; signal?: AbortSignal } = {}) => apiRequest<DataAdminBatchList>(`/api/data-admin/batches?${params(input)}`, { signal: input.signal })
export const getDataAdminBatch = (batchId: string, signal?: AbortSignal) => apiRequest<DataAdminBatchDetail>(`/api/data-admin/batches/${encodeURIComponent(batchId)}`, { signal })
export const listDataAdminErrors = (batchId: string, input: { page?: number; pageSize?: number; signal?: AbortSignal } = {}) => apiRequest<DataAdminErrorList>(`/api/data-admin/batches/${encodeURIComponent(batchId)}/errors?${params(input)}`, { signal: input.signal })
export const getPublishedSnapshot = (signal?: AbortSignal) => apiRequest<PublishedSnapshot>('/api/data-admin/published', { signal })
export const getDataQuality = (signal?: AbortSignal) => apiRequest<DataQualitySummary>('/api/data-admin/quality', { signal })
