import { describe, expect, it } from 'vitest'
import { canAccessRoute, hasPermission, requiredPermissionFor } from './permissions'

describe('route permissions', () => {
  it('maps list, detail, and nested paths to the same permission', () => {
    expect(requiredPermissionFor('/')).toBe('cost_data.read')
    expect(requiredPermissionFor('/cost-data')).toBe('cost_data.read')
    expect(requiredPermissionFor('/cost-data/batch-1')).toBe('cost_data.read')
    expect(requiredPermissionFor('/reports/artifact-1')).toBe('cost_data.read')
    expect(requiredPermissionFor('/data-admin')).toBe('data_management.read')
    expect(requiredPermissionFor('/data-admin/batch-1')).toBe('data_management.read')
  })

  it('leaves unknown routes unguarded so the router can render them', () => {
    expect(requiredPermissionFor('/auth/callback')).toBeUndefined()
  })

  it('checks the permission list without treating an empty list as allowed', () => {
    expect(hasPermission(['cost_data.read'], 'cost_data.read')).toBe(true)
    expect(hasPermission([], 'cost_data.read')).toBe(false)
    expect(hasPermission(undefined, 'cost_data.read')).toBe(false)
    expect(canAccessRoute(['cost_data.read'], '/data-admin')).toBe(false)
    expect(canAccessRoute(['cost_data.read', 'data_management.read'], '/data-admin/batch-1')).toBe(true)
  })
})
