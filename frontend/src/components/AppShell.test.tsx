import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Breadcrumbs } from './Breadcrumbs'
import { buildBreadcrumbs } from './breadcrumbItems'

describe('buildBreadcrumbs', () => {
  it('marks the overview as the current page at the root', () => {
    expect(buildBreadcrumbs('/', '2026-06')).toEqual([
      { label: '成本总览' },
    ])
  })

  it('builds a navigable hierarchy for a list page', () => {
    expect(buildBreadcrumbs('/cost-data', '2026-06')).toEqual([
      { label: '成本总览', to: '/?period=2026-06' },
      { label: '成本数据' },
    ])
  })

  it('decodes a dynamic detail identifier and links both parents', () => {
    expect(buildBreadcrumbs('/cost-data/batch%2F2026-06-01', '2026-06')).toEqual([
      { label: '成本总览', to: '/?period=2026-06' },
      { label: '成本数据', to: '/cost-data?period=2026-06' },
      { label: '批次 batch/2026-06-01' },
    ])
  })

  it('renders parent routes as links and the leaf as the current page', () => {
    render(<MemoryRouter><Breadcrumbs pathname="/reports/artifact-1" period="2026-06" /></MemoryRouter>)

    expect(screen.getByRole('navigation', { name: '面包屑导航' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '成本总览' })).toHaveAttribute('href', '/?period=2026-06')
    expect(screen.getByRole('link', { name: '报表中心' })).toHaveAttribute('href', '/reports?period=2026-06')
    expect(screen.getByText('报表 artifact-1')).toHaveAttribute('aria-current', 'page')
  })
})
