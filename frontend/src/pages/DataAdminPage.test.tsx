import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/http'
import * as dataAdmin from '../api/dataAdmin'
import type { AppOutletContext } from '../components/AppShell'
import { DataAdminPage } from './DataAdminPage'

vi.mock('../api/dataAdmin', () => ({
  getDataAdminBatch: vi.fn(),
  getDataQuality: vi.fn(),
  getPublishedSnapshot: vi.fn(),
  listDataAdminBatches: vi.fn(),
  listDataAdminErrors: vi.fn(),
}))

const batch = {
  batch_id: 'batch-1', status: 'published' as const, source_system: 'erp', source_file: 'snapshot.json',
  source_snapshot_hash: 'a'.repeat(64), calculation_rule_version: 'cost-v2', total_rows: 10,
  valid_rows: 9, error_rows: 1, started_at: '2026-06-01T00:00:00Z', finished_at: '2026-06-01T00:01:00Z',
  created_at: '2026-06-01T00:00:00Z',
}
const context: AppOutletContext = {
  period: '2026-06', me: { principal_id: 'admin', roles: ['tenant_admin'], capabilities: ['system_help'], permissions: ['data_management.read'], data_scope: { source: 'postgresql_cost_data', allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null } }, meLoading: false, meError: false,
}

function renderPage(path = '/data-admin', outletContext = context) {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route element={<Outlet context={outletContext} />}><Route path="/data-admin/:batchId?" element={<DataAdminPage />} /></Route></Routes></MemoryRouter>)
}

function setupSuccessfulApis() {
  vi.mocked(dataAdmin.getPublishedSnapshot).mockResolvedValue({
    batch, fact_rows: { parts: 2, cost_events: 3, cost_event_inputs: 1, cost_records: 4 },
    projection_rows: 1, part_count: 1, period_start: '2026-06', period_end: '2026-06', rule_version_matches: true,
  })
  vi.mocked(dataAdmin.getDataQuality).mockResolvedValue({
    published: true, error_rows: 1, fact_rows: { parts: 2, cost_events: 3, cost_event_inputs: 1, cost_records: 4 },
    projection_rows: 1, part_count: 1, period_start: '2026-06', period_end: '2026-06', rule_version_matches: true,
  })
  vi.mocked(dataAdmin.listDataAdminBatches).mockResolvedValue({ items: [batch], page: 1, page_size: 20, total: 1 })
  vi.mocked(dataAdmin.getDataAdminBatch).mockResolvedValue({ ...batch, error_codes: [{ error_code: 'invalid_unit', count: 1 }] })
  vi.mocked(dataAdmin.listDataAdminErrors).mockResolvedValue({
    items: [{ error_id: 1, source_table: 'parts', source_record_id: 'P-1', error_code: 'invalid_unit', error_message: '单位不受支持', created_at: '2026-06-01T00:01:00Z' }],
    page: 1, page_size: 20, total: 1,
  })
}

beforeEach(() => vi.resetAllMocks())

describe('DataAdminPage', () => {
  it('shows unauthorized state on direct URL without the permission', () => {
    renderPage('/data-admin', { ...context, me: { ...context.me!, roles: ['viewer'], permissions: [] } })
    expect(screen.getByRole('alert')).toHaveAttribute('data-state', 'unauthorized')
    expect(screen.getByText('当前主体没有 data_management.read 权限。')).toBeInTheDocument()
    expect(dataAdmin.listDataAdminBatches).not.toHaveBeenCalled()
  })

  it('renders published snapshot, quality, batches, and selected error details', async () => {
    setupSuccessfulApis()
    renderPage('/data-admin/batch-1?period=2026-06')
    expect(await screen.findByRole('table', { name: '导入批次列表' })).toBeInTheDocument()
    expect(screen.getByText('当前发布快照')).toBeInTheDocument()
    expect(screen.getByText('invalid_unit 1')).toBeInTheDocument()
    expect(screen.getByText('单位不受支持')).toBeInTheDocument()
    expect(screen.queryByText('raw_payload')).not.toBeInTheDocument()
    expect(dataAdmin.listDataAdminBatches).toHaveBeenCalledWith(expect.objectContaining({ page: 1, pageSize: 20 }))
  })

  it('treats API 403 as unauthorized rather than empty data', async () => {
    setupSuccessfulApis()
    vi.mocked(dataAdmin.getDataQuality).mockRejectedValue(new ApiError('无权限', 403, 'data_management_access_denied'))
    renderPage()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveAttribute('data-state', 'unauthorized'))
    expect(screen.queryByText('当前条件下没有数据')).not.toBeInTheDocument()
  })

  it('shows database 503 as unavailable and supports a no-data snapshot', async () => {
    setupSuccessfulApis()
    vi.mocked(dataAdmin.listDataAdminBatches).mockRejectedValue(new ApiError('数据库不可用', 503, 'database_unavailable'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveAttribute('data-state', 'unavailable')

    vi.resetAllMocks()
    vi.mocked(dataAdmin.getPublishedSnapshot).mockResolvedValue({ batch: null, fact_rows: { parts: 0, cost_events: 0, cost_event_inputs: 0, cost_records: 0 }, projection_rows: 0, part_count: 0, period_start: null, period_end: null, rule_version_matches: null })
    vi.mocked(dataAdmin.getDataQuality).mockResolvedValue({ published: false, error_rows: 0, fact_rows: { parts: 0 }, projection_rows: 0, part_count: 0, period_start: null, period_end: null, rule_version_matches: null })
    vi.mocked(dataAdmin.listDataAdminBatches).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    renderPage()
    expect(await screen.findByText('当前租户尚无导入批次或发布快照。')).toBeInTheDocument()
  })
})
