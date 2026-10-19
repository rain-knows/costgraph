import { apiRequest } from './http'

export type RoleId = 'viewer' | 'data_operator' | 'data_approver' | 'tenant_admin'

/** 与 ``backend/app/schemas/iam.py`` 的 ``IamDataScope`` 一致；期间格式 ``YYYY-MM``。 */
export type IamDataScope = {
  allowed_part_ids: string[]
  allowed_period_start: string | null
  allowed_period_end: string | null
}

export type IamPrincipal = {
  principal_id: string
  roles: RoleId[]
  data_scope: IamDataScope
  updated_by: string
  updated_at: string
}

export type IamPrincipalList = { items: IamPrincipal[]; page: number; page_size: number; total: number }

function principalPath(principalId: string, suffix: string) {
  return `/api/iam/principals/${encodeURIComponent(principalId)}/${suffix}`
}

export const listIamPrincipals = (input: { page?: number; pageSize?: number; signal?: AbortSignal } = {}) =>
  apiRequest<IamPrincipalList>(`/api/iam/principals?${new URLSearchParams({ page: String(input.page ?? 1), page_size: String(input.pageSize ?? 20) })}`, { signal: input.signal })

export const setPrincipalRoles = (principalId: string, roles: RoleId[], signal?: AbortSignal) =>
  apiRequest<IamPrincipal>(principalPath(principalId, 'roles'), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ roles }),
    signal,
  })

/** 后端 ``ScopeUpdateRequest`` 不接受 ``data_scope`` 包装，三个字段必须平铺。 */
export const setPrincipalScope = (principalId: string, scope: IamDataScope, signal?: AbortSignal) =>
  apiRequest<IamPrincipal>(principalPath(principalId, 'scope'), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      allowed_part_ids: scope.allowed_part_ids,
      allowed_period_start: scope.allowed_period_start,
      allowed_period_end: scope.allowed_period_end,
    }),
    signal,
  })
