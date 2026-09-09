import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listArtifacts, restoreArtifact } from '../api/artifacts'
import { ReportsPage } from './ReportsPage'

vi.mock('../api/artifacts', () => ({ getArtifact: vi.fn(), listArtifacts: vi.fn(), restoreArtifact: vi.fn(), trashArtifact: vi.fn() }))
const listArtifactsMock = vi.mocked(listArtifacts)
const restoreArtifactMock = vi.mocked(restoreArtifact)
const artifact = { artifact_id: 'artifact-1', artifact_type: 'cost_report' as const, conversation_id: 'conv-1', turn_id: 'turn-1', message_id: 'message-1', run_id: 'run-1', conversation_title: '产品A成本', product_id: 'P001', product_name: '产品A', period: '2026-06', report_sha256: 'hash', data_snapshot_id: 'snapshot', report_schema_version: '1.0', rule_version: '1', prompt_version: '1', code_version: '1', created_at: '2026-07-01T00:00:00Z', deleted_at: '2026-07-02T00:00:00Z' }

describe('ReportsPage', () => {
  beforeEach(() => {
    listArtifactsMock.mockResolvedValue({ items: [artifact], total: 1, limit: 20, offset: 0 })
    restoreArtifactMock.mockResolvedValue({ ...artifact, deleted_at: null })
  })

  it('restores an artifact from the real trash view', async () => {
    render(<MemoryRouter initialEntries={['/reports?period=2026-06&scope=trashed']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/reports" element={<ReportsPage />} /></Route></Routes></MemoryRouter>)
    await userEvent.click(await screen.findByRole('button', { name: '恢复 产品A 报表' }))
    await waitFor(() => expect(restoreArtifactMock).toHaveBeenCalledWith('artifact-1'))
    expect(listArtifactsMock).toHaveBeenCalledWith(expect.objectContaining({ state: 'trashed', period: '2026-06' }))
  })
})
