import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { listArtifacts } from '../api/artifacts'
import { AiWorkspacePage } from './AiWorkspacePage'

const api = vi.hoisted(() => ({
  listConversations: vi.fn(),
  listConversationMessages: vi.fn(),
  getActiveConversationRun: vi.fn(),
  getConversationRun: vi.fn(),
  streamConversationRun: vi.fn(),
  cancelConversationRun: vi.fn(),
  createConversation: vi.fn(),
  createConversationRun: vi.fn(),
  RunCancelledError: class RunCancelledError extends Error {},
}))

vi.mock('../api/conversations', () => api)
vi.mock('../api/artifacts', () => ({ listArtifacts: vi.fn() }))
const listArtifactsMock = vi.mocked(listArtifacts)

const artifact = {
  artifact_id: 'artifact-report-1',
  artifact_type: 'cost_report' as const,
  conversation_id: 'conv-1',
  turn_id: 'turn-1',
  message_id: 'message-1',
  run_id: 'run-1',
  conversation_title: '产品A成本分析',
  part_id: 'part-1',
  part_number: 'FG-001',
  part_description: '产品A',
  period: '2026-06',
  report_sha256: 'hash',
  data_snapshot_id: 'snapshot',
  report_schema_version: '3.0',
  rule_version: '1',
  prompt_version: '1',
  code_version: '1',
  created_at: '2026-09-09T00:00:01Z',
  deleted_at: null,
}

const historicalRun = {
  schema_version: '2.0' as const,
  api_version: '2.0' as const,
  runtime_version: 'runtime-harness-v2',
  workflow_version: 'cost-agent-graph-v2',
  trace_id: 'run-1',
  run_id: 'run-1',
  conversation_id: 'conv-1',
  turn_id: 'turn-1',
  message_id: 'message-1',
  status: 'succeeded' as const,
  attempt_count: 1,
  created_at: '2026-09-09T00:00:00Z',
  updated_at: '2026-09-09T00:00:01Z',
  request_id: 'request-1',
  budget: { limits: {}, usage: {} },
  result: {
    run_id: 'run-1',
    conversation_id: 'conv-1',
    final_message: '已完成产品A 2026-06 成本分析。',
    report_json: null,
    events: [],
    status_bar: null,
    outcome: 'completed' as const,
    clarification: null,
  },
}

describe('AiWorkspacePage historical runs', () => {
  it('renders a direct report detail link for a live report result', async () => {
    listArtifactsMock.mockResolvedValue({ items: [artifact], total: 1, limit: 1, offset: 0 })
    api.listConversations.mockResolvedValue([])
    api.listConversationMessages.mockResolvedValue({ items: [], next_cursor: null, has_more: false })
    api.getActiveConversationRun.mockResolvedValue({ ...historicalRun, result: { ...historicalRun.result, report_json: { report_schema_version: '3.0' } } })
    api.streamConversationRun.mockImplementation(async (_id, _runId, onMessage) => {
      const result = { ...historicalRun.result, report_json: { report_schema_version: '3.0' } }
      onMessage({ type: 'result', result }, 1)
      return result
    })

    render(<MemoryRouter initialEntries={['/ai/conv-1?period=2026-06']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/ai/:conversationId" element={<AiWorkspacePage />} /></Route></Routes></MemoryRouter>)

    const link = await screen.findByRole('link', { name: '打开报表' })
    expect(link).toHaveAttribute('href', '/reports/artifact-report-1?period=2026-06')
    expect(listArtifactsMock).toHaveBeenCalledWith(expect.objectContaining({ runId: 'run-1', state: 'active', limit: 1 }))
  })

  it('renders a direct report detail link for a historical report message', async () => {
    listArtifactsMock.mockResolvedValue({ items: [artifact], total: 1, limit: 1, offset: 0 })
    api.listConversations.mockResolvedValue([])
    api.listConversationMessages.mockResolvedValue({
      items: [{
        message_id: 'message-2',
        turn_id: 'turn-1',
        role: 'assistant',
        content: '已完成产品A 2026-06 成本分析。',
        run: { run_id: 'run-1', outcome: 'completed', event_count: 1, inherited_part: null, has_report: true },
        created_at: '2026-09-09T00:00:01Z',
      }],
      next_cursor: null,
      has_more: false,
    })
    api.getActiveConversationRun.mockResolvedValue(null)

    render(<MemoryRouter initialEntries={['/ai/conv-1?period=2026-06']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/ai/:conversationId" element={<AiWorkspacePage />} /></Route></Routes></MemoryRouter>)

    const link = await screen.findByRole('link', { name: '打开报表' })
    expect(link).toHaveAttribute('href', '/reports/artifact-report-1?period=2026-06')
    expect(listArtifactsMock).toHaveBeenCalledWith(expect.objectContaining({ runId: 'run-1', state: 'active', limit: 1 }))
  })

  it('does not render a stale link when the report is not active', async () => {
    listArtifactsMock.mockResolvedValue({ items: [], total: 0, limit: 1, offset: 0 })
    api.listConversations.mockResolvedValue([])
    api.listConversationMessages.mockResolvedValue({
      items: [{
        message_id: 'message-2',
        turn_id: 'turn-1',
        role: 'assistant',
        content: '已完成产品A 2026-06 成本分析。',
        run: { run_id: 'run-1', outcome: 'completed', event_count: 1, inherited_part: null, has_report: true },
        created_at: '2026-09-09T00:00:01Z',
      }],
      next_cursor: null,
      has_more: false,
    })
    api.getActiveConversationRun.mockResolvedValue(null)

    render(<MemoryRouter initialEntries={['/ai/conv-1?period=2026-06']}><Routes><Route element={<Outlet context={{ period: '2026-06' }} />}><Route path="/ai/:conversationId" element={<AiWorkspacePage />} /></Route></Routes></MemoryRouter>)

    expect(await screen.findByText('已完成产品A 2026-06 成本分析。')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '打开报表' })).not.toBeInTheDocument()
  })

  it('reloads the latest persisted run when no active run exists', async () => {
    api.listConversations.mockResolvedValue([
      {
        conversation_id: 'conv-1',
        title: '产品A历史会话',
        routing_mode: 'auto',
        enabled_capabilities: ['system_help', 'cost_calculation'],
        message_count: 2,
        created_at: '2026-09-09T00:00:00Z',
        updated_at: '2026-09-09T00:00:01Z',
      },
    ])
    listArtifactsMock.mockResolvedValue({ items: [], total: 0, limit: 1, offset: 0 })
    api.listConversationMessages.mockResolvedValue({
      items: [
        {
          message_id: 'message-1',
          turn_id: 'turn-1',
          role: 'user',
          content: '查询产品A 2026-06 成本',
          created_at: '2026-09-09T00:00:00Z',
        },
        {
          message_id: 'message-2',
          turn_id: 'turn-1',
          role: 'assistant',
          content: '已完成产品A 2026-06 成本分析。',
          run: {
            run_id: 'run-1',
            outcome: 'completed',
            event_count: 1,
            inherited_part: null,
            has_report: false,
          },
          created_at: '2026-09-09T00:00:01Z',
        },
      ],
      next_cursor: null,
      has_more: false,
    })
    api.getActiveConversationRun.mockResolvedValue(null)
    api.getConversationRun.mockResolvedValue(historicalRun)
    api.streamConversationRun.mockImplementation(async (_id, _runId, onMessage) => {
      onMessage({
        type: 'runtime_event',
        event: {
          node: 'final_answer',
          name: 'final_answer',
          kind: 'status',
          status: 'succeeded',
          summary: '历史事件已恢复',
          sequence: 1,
          status_bar: null,
          result: null,
          clarification: null,
          request_id: null,
        },
      }, 1)
      return historicalRun.result
    })

    render(
      <MemoryRouter initialEntries={['/ai/conv-1?period=2026-06']}>
        <Routes>
          <Route element={<Outlet context={{ period: '2026-06' }} />}>
            <Route path="/ai/:conversationId" element={<AiWorkspacePage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('已完成产品A 2026-06 成本分析。')).toBeInTheDocument()
    await waitFor(() => expect(api.getConversationRun).toHaveBeenCalledWith('conv-1', 'run-1'))
    expect(api.streamConversationRun).toHaveBeenCalledWith(
      'conv-1',
      'run-1',
      expect.any(Function),
      expect.any(AbortSignal),
    )
    expect(await screen.findByText('历史事件已恢复')).toBeInTheDocument()
  })
})
