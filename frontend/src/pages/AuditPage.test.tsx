import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as audit from '../api/audit'
import type { AppOutletContext } from '../components/AppShell'
import { AuditPage } from './AuditPage'

vi.mock('../api/audit', () => ({
  listAuditEvents: vi.fn(),
}))

const context: AppOutletContext = {
  period: '2026-06',
  me: { principal_id: 'admin', roles: ['tenant_admin'], capabilities: [], permissions: ['audit.read'], data_scope: { source: 'postgresql_cost_data', allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null } },
  meLoading: false,
  meError: false,
}

const events: audit.AuditEvent[] = [
  {
    event_id: 2, job_id: 'job-1', batch_id: null, actor_principal_id: 'user-1', action: 'import_job.approve',
    target_type: 'import_job', target_id: 'job-1', outcome: 'success', request_id: 'req-2',
    detail: { decision: 'approved' }, created_at: '2026-06-01T02:00:00Z',
  },
  {
    event_id: 1, job_id: null, batch_id: null, actor_principal_id: 'user-2', action: 'iam.scope_update',
    target_type: 'principal', target_id: 'user-1', outcome: 'failure', request_id: null,
    detail: {}, created_at: '2026-06-01T01:00:00Z',
  },
]

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

function renderPage(outletContext = context) {
  return render(<MemoryRouter initialEntries={['/audit?period=2026-06']}><Routes><Route element={<Outlet context={outletContext} />}><Route path="/audit" element={<><AuditPage /><LocationProbe /></>} /></Route></Routes></MemoryRouter>)
}

beforeEach(() => vi.resetAllMocks())

describe('AuditPage', () => {
  it('distinguishes success and failure outcomes and reads no raw payload', async () => {
    vi.mocked(audit.listAuditEvents).mockResolvedValue({ items: events, page: 1, page_size: 20, total: 2 })
    renderPage()

    const table = await screen.findByRole('table', { name: '审计事件列表' })
    const rows = Array.from(table.querySelectorAll('tbody tr')).map((row) => row.textContent ?? '')
    expect(rows[0]).toContain('成功')
    expect(rows[1]).toContain('失败')
    expect(screen.getByText('decision=approved')).toBeInTheDocument()
    expect(screen.queryByText(/payload/)).not.toBeInTheDocument()
  })

  it('writes filters into the query string and resets paging', async () => {
    vi.mocked(audit.listAuditEvents).mockResolvedValue({ items: events, page: 1, page_size: 20, total: 2 })
    renderPage()

    await screen.findByRole('table', { name: '审计事件列表' })
    await userEvent.type(screen.getByLabelText('操作者'), 'user-1')
    await userEvent.type(screen.getByLabelText('动作'), 'import')

    const search = screen.getByTestId('location').textContent ?? ''
    expect(search).toContain('actor_principal_id=user-1')
    expect(search).toContain('action=import')
    expect(search).toContain('page=1')
    expect(search).toContain('period=2026-06')
    expect(audit.listAuditEvents).toHaveBeenLastCalledWith(expect.objectContaining({ actorPrincipalId: 'user-1', action: 'import', page: 1 }))
  })

  it('shows the empty state for a filter without matches', async () => {
    vi.mocked(audit.listAuditEvents).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    renderPage()
    expect(await screen.findByText('尚无审计事件')).toBeInTheDocument()
  })
})
