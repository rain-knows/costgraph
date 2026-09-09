import type { ReportJson } from './agent'
import { apiRequest } from './http'

export type ArtifactSummary = {
  artifact_id: string
  artifact_type: 'cost_report'
  conversation_id: string
  turn_id: string
  message_id: string
  run_id: string
  conversation_title: string
  part_id: string
  part_number: string
  part_description: string
  period: string
  report_sha256: string
  data_snapshot_id: string
  report_schema_version: string
  rule_version: string
  prompt_version: string
  code_version: string
  created_at: string
  deleted_at: string | null
}

export type ArtifactDetail = ArtifactSummary & { report_json: ReportJson }
export type ArtifactListResponse = { items: ArtifactSummary[]; total: number; limit: number; offset: number }

export function listArtifacts(options: { state?: 'active' | 'trashed'; query?: string; period?: string; runId?: string; limit?: number; offset?: number; signal?: AbortSignal } = {}) {
  const params = new URLSearchParams({
    state: options.state ?? 'active',
    limit: String(options.limit ?? 20),
    offset: String(options.offset ?? 0),
  })
  if (options.query?.trim()) params.set('query', options.query.trim())
  if (options.period?.trim()) params.set('period', options.period.trim())
  if (options.runId) params.set('run_id', options.runId)
  return apiRequest<ArtifactListResponse>(`/api/artifacts?${params}`, { signal: options.signal })
}

export const getArtifact = (artifactId: string) => apiRequest<ArtifactDetail>(`/api/artifacts/${encodeURIComponent(artifactId)}`)
export const trashArtifact = (artifactId: string) => apiRequest<void>(`/api/artifacts/${encodeURIComponent(artifactId)}`, { method: 'DELETE' })
export const restoreArtifact = (artifactId: string) => apiRequest<ArtifactSummary>(`/api/artifacts/${encodeURIComponent(artifactId)}/restore`, { method: 'POST' })
