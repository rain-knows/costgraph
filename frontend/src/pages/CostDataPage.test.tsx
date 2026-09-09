import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listFinishedBatchCosts } from '../api/costData'
import { finishedBatch } from '../test/costFixtures'
import { CostDataPage } from './CostDataPage'

vi.mock('../api/costData', () => ({ listFinishedBatchCosts: vi.fn() }))
const listFinishedBatchCostsMock = vi.mocked(listFinishedBatchCosts)

function renderPage(initialEntry = '/cost-data?period=2026-06') {
  return render(<MemoryRouter initialEntries={[initialEntry]}><Routes>
    <Route element={<Outlet context={{ period: '2026-06' }} />}>
      <Route path="/cost-data" element={<CostDataPage />} />
      <Route path="/cost-data/:finishedBatchId" element={<div data-testid="detail-route" />} />
    </Route>
  </Routes></MemoryRouter>)
}

describe('CostDataPage v2', () => {
  beforeEach(() => {
    listFinishedBatchCostsMock.mockResolvedValue({ period: '2026-06', page: 1, page_size: 20, total: 1, items: [finishedBatch] })
  })

  it('renders the manufacturing view and requests finished batches', async () => {
    renderPage()
    expect(await screen.findByText('成品外壳总成')).toBeInTheDocument()
    expect(screen.getByText('六类制造成本')).toBeInTheDocument()
    expect(screen.getAllByText(/直接材料/).length).toBeGreaterThan(0)
    expect(screen.getByText('制造单位成本')).toBeInTheDocument()
    expect(listFinishedBatchCostsMock).toHaveBeenCalledWith(expect.objectContaining({
      period: '2026-06',
      sort: 'completion_time_desc',
      page: 1,
      pageSize: 20,
    }))
  })

  it('switches persisted views and opens a batch with the selected view', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('成品外壳总成')
    await user.click(screen.getByRole('button', { name: '料工费' }))
    expect(screen.getByText('料')).toBeInTheDocument()
    expect(screen.getByText('单位成本1')).toBeInTheDocument()
    await user.click(screen.getByRole('row', { name: /打开批次 LOT-FG-001/ }))
    await waitFor(() => expect(screen.getByTestId('detail-route')).toBeInTheDocument())
  })

  it('supports keyboard activation on a batch row', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /打开批次 LOT-FG-001/ })
    row.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByTestId('detail-route')).toBeInTheDocument()
  })
})
