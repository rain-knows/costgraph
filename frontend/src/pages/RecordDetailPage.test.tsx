import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getFinishedBatchCost } from '../api/costData'
import { finishedBatchDetail } from '../test/costFixtures'
import { RecordDetailPage } from './RecordDetailPage'

vi.mock('../api/costData', () => ({ getFinishedBatchCost: vi.fn() }))
const getFinishedBatchCostMock = vi.mocked(getFinishedBatchCost)

function renderPage(initialEntry = '/cost-data/batch-finished-1?period=2026-06&tab=summary') {
  return render(<MemoryRouter initialEntries={[initialEntry]}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/cost-data/:finishedBatchId" element={<RecordDetailPage />} /></Route></Routes></MemoryRouter>)
}

describe('RecordDetailPage v2', () => {
  beforeEach(() => {
    getFinishedBatchCostMock.mockResolvedValue(finishedBatchDetail)
  })

  it('renders summary, trace and source tabs from the batch detail API', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('成品外壳总成')).toBeInTheDocument()
    expect(screen.getByText('六类制造成本')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '成本追溯' }))
    expect(screen.getAllByText('成本追溯').length).toBeGreaterThan(1)
    await user.click(screen.getAllByRole('treeitem', { name: /ABS 原料/ })[0])
    expect(screen.getByText('ERP')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '来源记录' }))
    expect(screen.getByText('全部来源记录')).toBeInTheDocument()
  })

  it('shows a server error state', async () => {
    getFinishedBatchCostMock.mockRejectedValueOnce(new Error('无权查看批次'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('无权查看批次')
  })
})
