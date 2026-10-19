import type { DataAdminErrorList } from './dataAdmin'
import { apiRequest } from './http'

export type ImportJobStatus =
  | 'submitted'
  | 'validating'
  | 'validation_failed'
  | 'approval_pending'
  | 'approved'
  | 'rejected'
  | 'publishing'
  | 'published'
  | 'publish_failed'
  | 'cancelled'

export type ImportJobDecision = 'approve' | 'reject'

/** 导入任务响应；与 ``backend/app/schemas/data_admin.py`` 的 ``ImportJob`` 一一对应。 */
export type ImportJob = {
  job_id: string
  tenant_id: string
  principal_id: string
  status: ImportJobStatus
  source_system: string
  source_file_name: string | null
  source_object_key: string
  source_object_size: number
  source_object_sha256: string
  snapshot_hash: string
  calculation_rule_version: string
  total_rows: number
  valid_rows: number
  error_rows: number
  error_summary: Record<string, number> | null
  decision: 'approved' | 'rejected' | null
  decision_reason: string | null
  decided_by: string | null
  decided_at: string | null
  published_batch_id: string | null
  attempt_count: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  duplicate: boolean
}

export type ImportJobDetail = ImportJob & { error_codes: Array<{ error_code: string; count: number }> }
export type ImportJobList = { items: ImportJob[]; page: number; page_size: number; total: number }

/** ``POST /api/data-admin/imports`` 的响应。 */
export type ImportJobSubmission = {
  job_id: string
  status: ImportJobStatus
  duplicate: boolean
  source_system: string
  source_file_name: string | null
  source_object_key: string
  source_object_size: number
  source_object_sha256: string
  snapshot_hash: string
  calculation_rule_version: string
  total_rows: number
  created_at: string
}

/** 后端 ``ImportJobCreateRequest`` 为 ``extra="forbid"``，字段必须完全一致。 */
export type ImportJobCreateRequest = {
  source_system: string
  source_file_name: string
  calculation_rule_version: string
  payload: Record<string, Array<Record<string, unknown>>>
}

function query(input: { status?: ImportJobStatus; page?: number; pageSize?: number } = {}) {
  const value = new URLSearchParams({ page: String(input.page ?? 1), page_size: String(input.pageSize ?? 20) })
  if (input.status) value.set('status', input.status)
  return value
}

function jobPath(jobId: string, suffix = '') {
  return `/api/data-admin/imports/${encodeURIComponent(jobId)}${suffix}`
}

export const listImportJobs = (input: { status?: ImportJobStatus; page?: number; pageSize?: number; signal?: AbortSignal } = {}) =>
  apiRequest<ImportJobList>(`/api/data-admin/imports?${query(input)}`, { signal: input.signal })

export const getImportJob = (jobId: string, signal?: AbortSignal) =>
  apiRequest<ImportJobDetail>(jobPath(jobId), { signal })

export const listImportJobErrors = (jobId: string, input: { page?: number; pageSize?: number; signal?: AbortSignal } = {}) =>
  apiRequest<DataAdminErrorList>(`${jobPath(jobId, '/errors')}?${query(input)}`, { signal: input.signal })

export const submitImportJob = (body: ImportJobCreateRequest, idempotencyKey: string, signal?: AbortSignal) =>
  apiRequest<ImportJobSubmission>('/api/data-admin/imports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(body),
    signal,
  })

export const decideImportJob = (jobId: string, input: { decision: ImportJobDecision; reason?: string }, signal?: AbortSignal) =>
  apiRequest<ImportJobDetail>(jobPath(jobId, '/decision'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision: input.decision, ...(input.reason ? { reason: input.reason } : {}) }),
    signal,
  })

export const cancelImportJob = (jobId: string, signal?: AbortSignal) =>
  apiRequest<ImportJobDetail>(jobPath(jobId, '/cancel'), { method: 'POST', signal })
