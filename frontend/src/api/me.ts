import { apiRequest } from './http'

export type CapabilityId = 'system_help' | 'cost_calculation' | 'report_generation'
export type PermissionId = 'data_management.read'

export type MeSummary = {
  principal_id: string
  roles: Array<'user' | 'admin'>
  capabilities: CapabilityId[]
  permissions: PermissionId[]
}

export const getMe = (signal?: AbortSignal) => apiRequest<MeSummary>('/api/me', { signal })
