import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as iam from '../api/iam'
import type { AppOutletContext } from '../components/AppShell'
import { IamPage } from './IamPage'

vi.mock('../api/iam', () => ({
  listIamPrincipals: vi.fn(),
  setPrincipalRoles: vi.fn(),
  setPrincipalScope: vi.fn(),
}))

const context: AppOutletContext = {
  period: '2026-06',
  me: { principal_id: 'admin', roles: ['tenant_admin'], capabilities: [], permissions: ['iam.manage'], data_scope: { source: 'postgresql_cost_data', allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null } },
  meLoading: false,
  meError: false,
}

const principal: iam.IamPrincipal = {
  principal_id: 'user-1',
  roles: ['viewer'],
  data_scope: { allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null },
  updated_by: 'admin',
  updated_at: '2026-06-01T00:00:00Z',
}

function renderPage(outletContext = context) {
  return render(<MemoryRouter initialEntries={['/iam?period=2026-06']}><Routes><Route element={<Outlet context={outletContext} />}><Route path="/iam" element={<IamPage />} /></Route></Routes></MemoryRouter>)
}

async function selectPrincipal() {
  await userEvent.click(await screen.findByRole('cell', { name: 'user-1' }))
}

beforeEach(() => vi.resetAllMocks())

describe('IamPage', () => {
  it('shows the empty state when the tenant has no principals', async () => {
    vi.mocked(iam.listIamPrincipals).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    renderPage()
    expect(await screen.findByText('尚无主体记录')).toBeInTheDocument()
  })

  it('labels an empty scope as not narrowed instead of leaving it blank', async () => {
    vi.mocked(iam.listIamPrincipals).mockResolvedValue({ items: [principal], page: 1, page_size: 20, total: 1 })
    renderPage()
    expect(await screen.findByText('未收窄（全部范围）')).toBeInTheDocument()
  })

  it('blocks an inverted period range without sending the PUT', async () => {
    vi.mocked(iam.listIamPrincipals).mockResolvedValue({ items: [principal], page: 1, page_size: 20, total: 1 })
    renderPage()
    await selectPrincipal()

    fireEvent.change(screen.getByLabelText('期间起点'), { target: { value: '2026-08' } })
    fireEvent.change(screen.getByLabelText('期间终点'), { target: { value: '2026-03' } })
    await userEvent.click(screen.getByRole('button', { name: '保存数据范围' }))

    expect(await screen.findByText('期间起点不能晚于终点，请先修正再保存。')).toBeInTheDocument()
    expect(iam.setPrincipalScope).not.toHaveBeenCalled()
  })

  it('saves the flattened scope after a valid range', async () => {
    vi.mocked(iam.listIamPrincipals).mockResolvedValue({ items: [principal], page: 1, page_size: 20, total: 1 })
    vi.mocked(iam.setPrincipalScope).mockResolvedValue(principal)
    renderPage()
    await selectPrincipal()

    await userEvent.type(screen.getByLabelText(/零件 ID/), 'P-1, P-2')
    fireEvent.change(screen.getByLabelText('期间起点'), { target: { value: '2026-01' } })
    fireEvent.change(screen.getByLabelText('期间终点'), { target: { value: '2026-06' } })
    await userEvent.click(screen.getByRole('button', { name: '保存数据范围' }))

    expect(await screen.findByRole('status')).toHaveTextContent('数据范围已保存')
    expect(iam.setPrincipalScope).toHaveBeenCalledWith('user-1', {
      allowed_part_ids: ['P-1', 'P-2'],
      allowed_period_start: '2026-01',
      allowed_period_end: '2026-06',
    })
  })
})
