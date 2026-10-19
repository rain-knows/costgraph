import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, ForbiddenError, NotFoundError } from '../api/http'
import * as importJobs from '../api/importJobs'
import type { AppOutletContext } from '../components/AppShell'
import { ImportJobDetailPage } from './ImportJobDetailPage'

vi.mock('../api/importJobs', () => ({
  cancelImportJob: vi.fn(),
  decideImportJob: vi.fn(),
  getImportJob: vi.fn(),
  listImportJobErrors: vi.fn(),
}))

const job: importJobs.ImportJobDetail = {
  job_id: 'job-1', tenant_id: 'tenant-1', principal_id: 'user-1', status: 'approval_pending',
  source_system: 'erp', source_file_name: 'snapshot.json', source_object_key: 'imports/tenant-1/job-1.json',
  source_object_size: 1024, source_object_sha256: 'b'.repeat(64), snapshot_hash: 'a'.repeat(64),
  calculation_rule_version: 'cost-v1', total_rows: 10, valid_rows: 10, error_rows: 0, error_summary: null,
  decision: null, decision_reason: null, decided_by: null, decided_at: null, published_batch_id: null,
  attempt_count: 0, created_at: '2026-06-01T00:00:00Z', started_at: null, finished_at: null, duplicate: false,
  error_codes: [],
}

function contextWith(permissions: AppOutletContext['me'] extends null ? never : NonNullable<AppOutletContext['me']>['permissions']): AppOutletContext {
  return {
    period: '2026-06',
    me: { principal_id: 'admin', roles: ['tenant_admin'], capabilities: [], permissions, data_scope: { source: 'postgresql_cost_data', allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null } },
    meLoading: false,
    meError: false,
  }
}

function renderPage(outletContext = contextWith(['data_management.read', 'data_import.approve', 'data_import.submit']), path = '/data-admin/imports/job-1') {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route element={<Outlet context={outletContext} />}><Route path="/data-admin/imports/:jobId" element={<ImportJobDetailPage />} /></Route></Routes></MemoryRouter>)
}

beforeEach(() => vi.resetAllMocks())

describe('ImportJobDetailPage', () => {
  it('shows approval actions for approval_pending when the principal may approve', async () => {
    vi.mocked(importJobs.getImportJob).mockResolvedValue(job)
    renderPage()
    expect(await screen.findByText('待审批')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '审批通过' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '拒绝' })).toBeDisabled()
  })

  it('hides approval actions without data_import.approve while keeping the task visible', async () => {
    vi.mocked(importJobs.getImportJob).mockResolvedValue(job)
    renderPage(contextWith(['data_management.read']))
    expect(await screen.findByText('待审批')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '审批通过' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '拒绝' })).not.toBeInTheDocument()
    expect(screen.queryByText('当前条件下没有数据')).not.toBeInTheDocument()
  })

  it('sends the rejection reason once it is filled in', async () => {
    vi.mocked(importJobs.getImportJob).mockResolvedValue(job)
    vi.mocked(importJobs.decideImportJob).mockResolvedValue({ ...job, status: 'rejected', decision: 'rejected' })
    renderPage()
    await screen.findByText('待审批')
    await userEvent.type(screen.getByRole('textbox'), '行数不匹配')
    await userEvent.click(screen.getByRole('button', { name: '拒绝' }))
    expect(importJobs.decideImportJob).toHaveBeenCalledWith('job-1', { decision: 'reject', reason: '行数不匹配' })
  })

  it('renders validation error details for validation_failed', async () => {
    vi.mocked(importJobs.getImportJob).mockResolvedValue({ ...job, status: 'validation_failed', error_rows: 2, error_summary: { invalid_unit: 2 }, error_codes: [{ error_code: 'invalid_unit', count: 2 }] })
    vi.mocked(importJobs.listImportJobErrors).mockResolvedValue({
      items: [{ error_id: 1, source_table: 'parts', source_record_id: 'P-1', error_code: 'invalid_unit', error_message: '单位不受支持', created_at: '2026-06-01T00:00:00Z' }],
      page: 1, page_size: 20, total: 1,
    })
    renderPage()
    expect(await screen.findByRole('table', { name: '导入任务错误列表' })).toBeInTheDocument()
    expect(screen.getByText('单位不受支持')).toBeInTheDocument()
    expect(importJobs.listImportJobErrors).toHaveBeenCalledWith('job-1', expect.objectContaining({ page: 1 }))
  })

  it('links a published job to its batch', async () => {
    vi.mocked(importJobs.getImportJob).mockResolvedValue({ ...job, status: 'published', published_batch_id: 'batch-9', decision: 'approved', decided_by: 'admin', decided_at: '2026-06-01T01:00:00Z' })
    renderPage()
    const link = await screen.findByRole('link', { name: 'batch-9' })
    expect(link).toHaveAttribute('href', '/data-admin/batch-9?period=2026-06')
    expect(screen.getByText('审批结论')).toBeInTheDocument()
  })

  it('disables publish retry for publish_failed and explains the worker retry', async () => {
    vi.mocked(importJobs.getImportJob).mockResolvedValue({ ...job, status: 'publish_failed', attempt_count: 2 })
    renderPage()
    expect(await screen.findByRole('button', { name: '重试发布' })).toBeDisabled()
    expect(screen.getByText(/由 Worker 在租约过期后自动重试（已尝试 2 次）/)).toBeInTheDocument()
  })

  it('treats a 403 response as unauthorized instead of an empty task', async () => {
    vi.mocked(importJobs.getImportJob).mockRejectedValue(new ForbiddenError('无权限', 'data_management_access_denied'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveAttribute('data-state', 'unauthorized')
    expect(screen.queryByText('任务 job-1 不存在或不属于当前租户。')).not.toBeInTheDocument()
  })

  it('shows a 503 as unavailable rather than a missing task', async () => {
    vi.mocked(importJobs.getImportJob).mockRejectedValue(new ApiError('数据库不可用', 503, 'database_unavailable'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveAttribute('data-state', 'unavailable')
  })

  it('states that the task does not exist on 404', async () => {
    vi.mocked(importJobs.getImportJob).mockRejectedValue(new NotFoundError('任务不存在'))
    renderPage()
    expect(await screen.findByText('任务不存在')).toBeInTheDocument()
    expect(screen.getByText('任务 job-1 不存在或不属于当前租户。')).toBeInTheDocument()
  })
})
