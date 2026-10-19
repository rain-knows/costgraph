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
    expect(requiredPermissionFor('/data-admin/imports')).toBe('data_management.read')
    expect(requiredPermissionFor('/data-admin/imports/job-1')).toBe('data_management.read')
    expect(requiredPermissionFor('/iam')).toBe('iam.manage')
    expect(requiredPermissionFor('/audit')).toBe('audit.read')
  })

  it('requires data_import.submit for the submission wizard instead of the list permission', () => {
    expect(requiredPermissionFor('/data-admin/imports/new')).toBe('data_import.submit')
    expect(canAccessRoute(['data_management.read'], '/data-admin/imports/new')).toBe(false)
    expect(canAccessRoute(['data_management.read', 'data_import.submit'], '/data-admin/imports/new')).toBe(true)
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
    expect(canAccessRoute(['cost_data.read'], '/iam')).toBe(false)
    expect(canAccessRoute(['iam.manage'], '/iam')).toBe(true)
    expect(canAccessRoute(['audit.read'], '/audit/entry-1')).toBe(true)
  })
})
