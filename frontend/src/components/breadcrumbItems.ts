export type BreadcrumbItem = { label: string; to?: string }

export function buildBreadcrumbs(pathname: string, period: string): BreadcrumbItem[] {
  const parts = pathname.split('/').filter(Boolean).map(safeDecodeURIComponent)
  const withPeriod = (path: string) => `${path}?period=${encodeURIComponent(period)}`
  const overview: BreadcrumbItem = { label: '成本总览', to: withPeriod('/') }

  if (!parts.length) return [{ label: '成本总览' }]

  const section = parts[0]
  if (section === 'iam') return [overview, { label: '身份与权限' }]
  if (section === 'audit') return [overview, { label: '审计日志' }]

  const sectionMap: Record<string, { label: string; detailLabel: string }> = {
    'cost-data': { label: '成本数据', detailLabel: '批次' },
    ai: { label: 'AI 分析', detailLabel: '会话' },
    reports: { label: '报表中心', detailLabel: '报表' },
    'data-admin': { label: '数据治理', detailLabel: '批次' },
  }
  const matched = sectionMap[section]
  if (!matched) return [overview, { label: '当前页面' }]
  if (parts.length === 1) return [overview, { label: matched.label }]

  const sectionCrumb: BreadcrumbItem = { label: matched.label, to: withPeriod(`/${section}`) }
  if (section === 'data-admin' && parts[1] === 'imports') {
    if (parts.length === 2) return [overview, sectionCrumb, { label: '导入任务' }]
    const importsCrumb: BreadcrumbItem = { label: '导入任务', to: withPeriod('/data-admin/imports') }
    if (parts[2] === 'new') return [overview, sectionCrumb, importsCrumb, { label: '提交导入任务' }]
    return [overview, sectionCrumb, importsCrumb, { label: `任务 ${parts[2]}` }]
  }

  return [
    overview,
    sectionCrumb,
    { label: `${matched.detailLabel} ${parts[1]}` },
  ]
}

function safeDecodeURIComponent(value: string) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}
