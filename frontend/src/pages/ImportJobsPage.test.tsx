import { render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ForbiddenError } from '../api/http'
import * as importJobs from '../api/importJobs'
import type { AppOutletContext } from '../components/AppShell'
import { ImportJobsPage } from './ImportJobsPage'

vi.mock('../api/importJobs', () => ({
  listImportJobs: vi.fn(),
}))

const context: AppOutletContext = {
  period: '2026-06',
  me: { principal_id: 'admin', roles: ['tenant_admin'], capabilities: [], permissions: ['data_management.read'], data_scope: { source: 'postgresql_cost_data', allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null } },
  meLoading: false,
  meError: false,
}

function renderPage(outletContext = context, path = '/data-admin/imports?period=2026-06') {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route element={<Outlet context={outletContext} />}><Route path="/data-admin/imports" element={<ImportJobsPage />} /></Route></Routes></MemoryRouter>)
}

beforeEach(() => vi.resetAllMocks())

describe('ImportJobsPage', () => {
  it('shows the empty state for an empty job list', async () => {
    vi.mocked(importJobs.listImportJobs).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    renderPage()
    expect(await screen.findByText('尚无导入任务')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveAttribute('data-state', 'empty')
  })

  it('renders the job table and keeps the status filter in the query string', async () => {
    vi.mocked(importJobs.listImportJobs).mockResolvedValue({
      items: [{
        job_id: 'job-1', tenant_id: 'tenant-1', principal_id: 'user-1', status: 'approval_pending',
        source_system: 'erp', source_file_name: 'snapshot.json', source_object_key: 'imports/tenant-1/job-1.json',
        source_object_size: 1024, source_object_sha256: 'b'.repeat(64), snapshot_hash: 'a'.repeat(64),
        calculation_rule_version: 'cost-v1', total_rows: 10, valid_rows: 10, error_rows: 0, error_summary: null,
        decision: null, decision_reason: null, decided_by: null, decided_at: null, published_batch_id: null,
        attempt_count: 0, created_at: '2026-06-01T00:00:00Z', started_at: null, finished_at: null, duplicate: false,
      }],
      page: 1, page_size: 20, total: 1,
    })
    renderPage()
    expect(await screen.findByRole('table', { name: '导入任务列表' })).toBeInTheDocument()
    expect(importJobs.listImportJobs).toHaveBeenCalledWith(expect.objectContaining({ page: 1, pageSize: 20, status: undefined }))
  })

  it('treats a 403 response as unauthorized rather than an empty list', async () => {
    vi.mocked(importJobs.listImportJobs).mockRejectedValue(new ForbiddenError('无权限', 'data_management_access_denied'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveAttribute('data-state', 'unauthorized')
    expect(screen.queryByText('尚无导入任务')).not.toBeInTheDocument()
  })
})
