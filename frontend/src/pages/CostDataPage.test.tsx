import { render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listProductCosts } from '../api/costData'
import { CostDataPage } from './CostDataPage'

vi.mock('../api/costData', () => ({ listProductCosts: vi.fn() }))
const listProductCostsMock = vi.mocked(listProductCosts)

describe('CostDataPage', () => {
  beforeEach(() => listProductCostsMock.mockResolvedValue({
    period: '2026-06', page: 1, page_size: 20, total: 1,
    items: [{ product_id: 'P001', product_name: '产品A', spec: '黑色外壳', period: '2026-06', currency: 'CNY', output_qty: '10000.00', total_cost: '139000.00', unit_cost: '13.90', comparison: null }],
  }))

  it('renders server product summaries and sends the selected period', async () => {
    render(<MemoryRouter initialEntries={['/cost-data?period=2026-06']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/cost-data" element={<CostDataPage />} /></Route></Routes></MemoryRouter>)
    expect(await screen.findByText('产品A')).toBeInTheDocument()
    expect(screen.getByText(/139,000/)).toBeInTheDocument()
    expect(listProductCostsMock).toHaveBeenCalledWith(expect.objectContaining({ period: '2026-06', sort: 'product_id', page: 1, pageSize: 20 }))
  })
})
