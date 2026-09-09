import { describe, expect, it } from 'vitest'
import type { AgentRunResponse, RuntimeSemanticEvent } from '../../api/agent'
import { INITIAL_RUNTIME_PROGRESS, runtimeProgressReducer } from './runtimeProgress'

const event = (sequence: number, status: string, kind = 'status'): RuntimeSemanticEvent => ({ node: 'run', sequence, status, kind, summary: status })
const result = (outcome: AgentRunResponse['outcome']): AgentRunResponse => ({ run_id: 'run-1', final_message: 'done', report_json: null, events: [], status_bar: null, outcome })

describe('runtimeProgressReducer', () => {
  it('tracks one lifecycle through finalization', () => {
    let state = runtimeProgressReducer(INITIAL_RUNTIME_PROGRESS, { type: 'creating' })
    state = runtimeProgressReducer(state, { type: 'created', runId: 'run-1' })
    state = runtimeProgressReducer(state, { type: 'event', event: event(1, 'running') })
    state = runtimeProgressReducer(state, { type: 'event', event: event(2, 'finalizing') })
    state = runtimeProgressReducer(state, { type: 'result', result: result('completed') })
    expect(state.lifecycle).toBe('succeeded')
    expect(state.events).toHaveLength(2)
  })

  it('ignores duplicate and out-of-order events', () => {
    const state = runtimeProgressReducer(INITIAL_RUNTIME_PROGRESS, { type: 'event', event: event(4, 'running') })
    expect(runtimeProgressReducer(state, { type: 'event', event: event(3, 'failed', 'error') })).toBe(state)
    expect(runtimeProgressReducer(state, { type: 'event', event: event(4, 'failed', 'error') })).toBe(state)
  })
})
