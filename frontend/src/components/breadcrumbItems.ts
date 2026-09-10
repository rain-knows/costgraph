export type BreadcrumbItem = { label: string; to?: string }

export function buildBreadcrumbs(pathname: string, period: string): BreadcrumbItem[] {
  const parts = pathname.split('/').filter(Boolean).map(safeDecodeURIComponent)
  const withPeriod = (path: string) => `${path}?period=${encodeURIComponent(period)}`
  const overview: BreadcrumbItem = { label: '成本总览', to: withPeriod('/') }

  if (!parts.length) return [{ label: '成本总览' }]

  const section = parts[0]
  const sectionMap: Record<string, { label: string; detailLabel: string }> = {
    'cost-data': { label: '成本数据', detailLabel: '批次' },
    ai: { label: 'AI 分析', detailLabel: '会话' },
    reports: { label: '报表中心', detailLabel: '报表' },
  }
  const matched = sectionMap[section]
  if (!matched) return [overview, { label: '当前页面' }]
  if (parts.length === 1) return [overview, { label: matched.label }]

  return [
    overview,
    { label: matched.label, to: withPeriod(`/${section}`) },
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
