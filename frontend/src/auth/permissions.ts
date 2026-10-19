import type { PermissionId } from '../api/me'

export type RoutePermission = { path: string; permission: PermissionId }

/**
 * 前端路由权限映射；后端仍是权威边界，这里的映射只决定导航可见性与页面可达性。
 * 匹配按最长前缀优先，因此 ``/data-admin/imports/new`` 会覆盖 ``/data-admin``。
 */
export const ROUTE_PERMISSIONS: readonly RoutePermission[] = [
  { path: '/', permission: 'cost_data.read' },
  { path: '/cost-data', permission: 'cost_data.read' },
  { path: '/ai', permission: 'cost_data.read' },
  { path: '/reports', permission: 'cost_data.read' },
  { path: '/data-admin', permission: 'data_management.read' },
  { path: '/data-admin/imports', permission: 'data_management.read' },
  { path: '/data-admin/imports/new', permission: 'data_import.submit' },
  { path: '/iam', permission: 'iam.manage' },
  { path: '/audit', permission: 'audit.read' },
]

export function requiredPermissionFor(pathname: string): PermissionId | undefined {
  const normalized = pathname.replace(/\/+$/, '') || '/'
  let matched: RoutePermission | undefined
  for (const route of ROUTE_PERMISSIONS) {
    if (normalized === route.path || normalized.startsWith(`${route.path}/`)) {
      if (!matched || route.path.length > matched.path.length) matched = route
    }
  }
  return matched?.permission
}

export function hasPermission(permissions: readonly PermissionId[] | undefined, permission: PermissionId): boolean {
  return permissions?.includes(permission) === true
}

export function canAccessRoute(permissions: readonly PermissionId[] | undefined, pathname: string): boolean {
  const required = requiredPermissionFor(pathname)
  return required === undefined || hasPermission(permissions, required)
}
