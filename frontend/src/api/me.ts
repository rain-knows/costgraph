import type { CapabilityId } from './agent'
import { apiRequest } from './http'

export type PermissionId =
  | 'cost_data.read'
  | 'data_management.read'
  | 'data_import.submit'
  | 'data_import.approve'
  | 'iam.manage'
  | 'data_scope.manage'
  | 'audit.read'

export type DataScope = {
  source: 'postgresql_cost_data'
  allowed_part_ids: string[]
  allowed_period_start: string | null
  allowed_period_end: string | null
}

/**
 * ``GET /api/me`` 的实际响应形状（见 backend/app/api/me.py）；租户来自令牌 claim，
 * 接口不返回 ``tenant_id``，``roles`` 按响应模型是字符串列表而非固定枚举。
 */
export type MeSummary = {
  principal_id: string
  roles: string[]
  capabilities: CapabilityId[]
  permissions: PermissionId[]
  data_scope: DataScope
}

export const getMe = (signal?: AbortSignal) => apiRequest<MeSummary>('/api/me', { signal })
