import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as importJobs from '../api/importJobs'
import type { AppOutletContext } from '../components/AppShell'
import { GovernanceSubmitPage } from './GovernanceSubmitPage'

vi.mock('../api/importJobs', () => ({
  submitImportJob: vi.fn(),
}))

const context: AppOutletContext = {
  period: '2026-06',
  me: { principal_id: 'admin', roles: ['tenant_admin'], capabilities: [], permissions: ['data_import.submit'], data_scope: { source: 'postgresql_cost_data', allowed_part_ids: [], allowed_period_start: null, allowed_period_end: null } },
  meLoading: false,
  meError: false,
}

function renderPage(outletContext = context) {
  return render(<MemoryRouter initialEntries={['/data-admin/imports/new?period=2026-06']}><Routes><Route element={<Outlet context={outletContext} />}><Route path="/data-admin/imports/new" element={<GovernanceSubmitPage />} /></Route></Routes></MemoryRouter>)
}

const submission = {
  job_id: 'job-1', status: 'submitted' as const, duplicate: false, source_system: 'erp', source_file_name: 'snapshot.json',
  source_object_key: 'imports/tenant-1/job-1.json', source_object_size: 18, source_object_sha256: 'c'.repeat(64),
  snapshot_hash: 'd'.repeat(64), calculation_rule_version: 'cost-v1', total_rows: 1, created_at: '2026-06-01T00:00:00Z',
}

function uploadFile(text: string, name = 'snapshot.json') {
  const input = screen.getByLabelText(/载荷文件/) as HTMLInputElement
  return userEvent.upload(input, jsonFile(text, name))
}

/** jsdom 的 ``File`` 没有 ``arrayBuffer``，测试里补上以便页面读取字节并计算哈希。 */
function jsonFile(text: string, name = 'snapshot.json') {
  const file = new File([text], name, { type: 'application/json' })
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode(text).buffer })
  return file
}

async function advanceToPayloadStep() {
  await userEvent.type(screen.getByLabelText('来源系统'), 'erp')
  await userEvent.type(screen.getByLabelText('源文件名'), 'snapshot.json')
  await userEvent.click(screen.getByRole('button', { name: '下一步' }))
}

/** 文件读取是异步的，预览按钮只有在载荷解析完成后才可用。 */
async function uploadAndPreview(text: string) {
  await uploadFile(text)
  await waitFor(() => expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: '下一步' }))
  return screen.findByRole('table', { name: '载荷表预览' })
}

beforeEach(() => vi.resetAllMocks())

describe('GovernanceSubmitPage', () => {
  it('reports invalid JSON without calling the API', async () => {
    renderPage()
    await advanceToPayloadStep()
    await uploadFile('{ not json')
    expect(await screen.findByText('文件不是合法的 JSON，请检查格式。')).toBeInTheDocument()
    expect(importJobs.submitImportJob).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '下一步' })).toBeDisabled()
  })

  it('rejects a payload whose table value is not an array', async () => {
    renderPage()
    await advanceToPayloadStep()
    await uploadFile(JSON.stringify({ parts: { 'P-1': 1 } }))
    expect(await screen.findByText('表 parts 的取值不是数组。')).toBeInTheDocument()
    expect(importJobs.submitImportJob).not.toHaveBeenCalled()
  })

  it('shows the new-job result after a successful submission', async () => {
    vi.mocked(importJobs.submitImportJob).mockResolvedValue(submission)
    renderPage()
    await advanceToPayloadStep()
    await uploadAndPreview(JSON.stringify({ parts: [{ part_id: 'P-1' }] }))
    await userEvent.click(screen.getByRole('button', { name: '提交' }))
    expect(await screen.findByText('已创建新任务')).toBeInTheDocument()
    expect(screen.getByText('job-1')).toBeInTheDocument()
    expect(screen.queryByText(/相同快照已存在/)).not.toBeInTheDocument()
    const [, key] = vi.mocked(importJobs.submitImportJob).mock.calls[0]
    expect(typeof key).toBe('string')
    expect((key as string).length).toBeGreaterThan(0)
  })

  it('explains the duplicate branch when the server returns an existing job', async () => {
    vi.mocked(importJobs.submitImportJob).mockResolvedValue({ ...submission, job_id: 'job-0', duplicate: true })
    renderPage()
    await advanceToPayloadStep()
    await uploadAndPreview(JSON.stringify({ parts: [{ part_id: 'P-1' }] }))
    await userEvent.click(screen.getByRole('button', { name: '提交' }))
    expect(await screen.findByText('已存在相同快照的任务')).toBeInTheDocument()
    expect(screen.getByText('已存在相同快照的任务，返回既有任务；本次未重复落盘。')).toBeInTheDocument()
  })

  it('blocks the wizard without data_import.submit', () => {
    renderPage({ ...context, me: { ...context.me!, permissions: ['data_management.read'] } })
    expect(screen.getByRole('alert')).toHaveAttribute('data-state', 'unauthorized')
    expect(screen.queryByLabelText('来源系统')).not.toBeInTheDocument()
  })
})
