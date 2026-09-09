import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentRunResponse } from './agent'
import { getActiveConversationRun, streamConversationRun } from './conversations'

const result: AgentRunResponse = { run_id: 'run-1', final_message: 'done', report_json: null, events: [], status_bar: null, outcome: 'completed', clarification: null }

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('durable run stream', () => {
  it('reconnects from the last persisted event id', async () => {
    vi.useFakeTimers()
    const first = new Response('id: 4\nevent: status\ndata: {"schema_version":"2.0","sequence":4,"status":"running","summary":"running"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    const running = new Response(JSON.stringify({ schema_version: '2.0', api_version: '2.0', runtime_version: '1', workflow_version: '1', trace_id: 'trace-1', run_id: 'run-1', conversation_id: 'conv-1', turn_id: 'turn-1', message_id: 'message-1', status: 'running', attempt_count: 1, created_at: '2026-09-07T00:00:00Z', request_id: 'request-1', budget: { limits: {}, usage: {} } }), { headers: { 'Content-Type': 'application/json' } })
    const final = new Response(`id: 5\nevent: result\ndata: ${JSON.stringify({ result })}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(first).mockResolvedValueOnce(running).mockResolvedValueOnce(final)
    const pending = streamConversationRun('conv-1', 'run-1', vi.fn())
    await vi.runAllTimersAsync()
    await expect(pending).resolves.toEqual(result)
    expect(String(fetchMock.mock.calls[2][0])).toContain('after_event_id=4')
    expect((fetchMock.mock.calls[2][1]?.headers as Record<string, string>)['Last-Event-ID']).toBe('4')
  })

  it('normalizes v2 semantic events', async () => {
    const response = new Response(['id: 8', 'event: tool', `data: ${JSON.stringify({ schema_version: '2.0', sequence: 8, kind: 'tool', name: 'load_finished_batches', status: 'success', summary: 'done' })}`, '', 'id: 9', 'event: result', `data: ${JSON.stringify({ schema_version: '2.0', result })}`, ''].join('\n'), { headers: { 'Content-Type': 'text/event-stream' } })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)
    const onMessage = vi.fn()
    await streamConversationRun('conv-1', 'run-1', onMessage)
    expect(onMessage.mock.calls[0][0]).toEqual({ type: 'runtime_event', event: expect.objectContaining({ sequence: 8, kind: 'tool', name: 'load_finished_batches' }) })
  })

  it('returns null when no active v2 run exists', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('null', { headers: { 'Content-Type': 'application/json' } }))
    await expect(getActiveConversationRun('conv-1')).resolves.toBeNull()
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v2/conversations/')
  })
})
