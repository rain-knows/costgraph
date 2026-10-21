import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MeSummary } from '../api/me'
import { AppShell } from './AppShell'
import { Breadcrumbs } from './Breadcrumbs'
import { buildBreadcrumbs } from './breadcrumbItems'

const viewer: MeSummary = {
  principal_id: 'user-1',
  roles: ['viewer'],
  capabilities: [],
  permissions: ['cost_data.read'],
  data_scope: { source: 'postgresql_cost_data', allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null },
}

function mockBackend(me: MeSummary) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('/api/me')) return new Response(JSON.stringify(me), { headers: { 'Content-Type': 'application/json' } })
    if (url.includes('/api/health')) return new Response(JSON.stringify({ runtime_ready: true }), { headers: { 'Content-Type': 'application/json' } })
    return new Response('null', { headers: { 'Content-Type': 'application/json' } })
  })
}

function renderShell(pathname: string) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<p>总览内容</p>} />
          <Route path="/data-admin" element={<p>治理内容</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => { vi.restoreAllMocks() })

describe('AppShell permissions', () => {
  it('does not expose navigation while the permission summary is loading', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).includes('/api/me')) return new Promise(() => {})
      return Promise.resolve(new Response(JSON.stringify({ runtime_ready: true }), { headers: { 'Content-Type': 'application/json' } }))
    })
    renderShell('/')
    expect(screen.getByRole('navigation', { name: '一级导航' })).toBeEmptyDOMElement()
    expect(await screen.findByRole('status')).toHaveTextContent('正在读取当前主体权限')
  })

  it.each([
    [401, '登录状态已失效'],
    [403, '无法确认当前主体权限'],
    [503, '身份服务暂不可用'],
  ])('preserves /api/me HTTP %i semantics', async (status, message) => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => String(input).includes('/api/me')
      ? new Response(JSON.stringify({ detail: { code: 'request_failed', message: '请求失败' } }), { status, headers: { 'Content-Type': 'application/json' } })
      : new Response(JSON.stringify({ runtime_ready: true }), { headers: { 'Content-Type': 'application/json' } }))
    renderShell('/')
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
  })

  it('hides navigation entries the principal cannot access', async () => {
    mockBackend(viewer)
    renderShell('/')

    expect(await screen.findByText('总览内容')).toBeInTheDocument()
    const primaryNav = screen.getByRole('navigation', { name: '一级导航' })
    expect(primaryNav).toHaveTextContent('成本数据')
    expect(primaryNav).not.toHaveTextContent('数据治理')
    expect(await screen.findByText('user-1')).toBeInTheDocument()
  })

  it('renders the unauthorized panel instead of page content for a forbidden route', async () => {
    mockBackend(viewer)
    renderShell('/data-admin')

    expect(await screen.findByRole('alert')).toHaveTextContent('无权访问该页面')
    expect(screen.queryByText('治理内容')).not.toBeInTheDocument()
  })
})

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
