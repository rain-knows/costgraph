import { apiRequest } from './http'

export type AuditOutcome = 'success' | 'failure'

/** 与 ``backend/app/schemas/iam.py`` 的 ``AuditEvent`` 一致。 */
export type AuditEvent = {
  event_id: number
  job_id: string | null
  batch_id: string | null
  actor_principal_id: string
  action: string
  target_type: string
  target_id: string
  outcome: AuditOutcome
  request_id: string | null
  detail: Record<string, unknown>
  created_at: string
}

export type AuditEventList = { items: AuditEvent[]; page: number; page_size: number; total: number }

export const listAuditEvents = (input: { page?: number; pageSize?: number; actorPrincipalId?: string; action?: string; signal?: AbortSignal } = {}) => {
  const query = new URLSearchParams({ page: String(input.page ?? 1), page_size: String(input.pageSize ?? 20) })
  if (input.actorPrincipalId) query.set('actor_principal_id', input.actorPrincipalId)
  if (input.action) query.set('action', input.action)
  return apiRequest<AuditEventList>(`/api/audit/events?${query}`, { signal: input.signal })
}
