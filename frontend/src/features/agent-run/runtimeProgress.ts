import type { AgentRunResponse, AgentStatusBar, RuntimeSemanticEvent } from '../../api/agent'
import type { RuntimeRun, RuntimeRunStatus } from '../../api/conversations'

export type RuntimeProgressLifecycle = 'idle' | 'creating' | RuntimeRunStatus
export type RuntimeProgressState = {
  lifecycle: RuntimeProgressLifecycle
  runId: string | null
  lastSequence: number
  statusBar: AgentStatusBar | null
  events: RuntimeSemanticEvent[]
  result: AgentRunResponse | null
  error: string | null
}
export type RuntimeProgressAction =
  | { type: 'reset' }
  | { type: 'creating' }
  | { type: 'resume'; run: RuntimeRun }
  | { type: 'created'; runId: string }
  | { type: 'event'; event: RuntimeSemanticEvent }
  | { type: 'result'; result: AgentRunResponse }
  | { type: 'failed'; message: string }
  | { type: 'cancelled' }

export const INITIAL_RUNTIME_PROGRESS: RuntimeProgressState = { lifecycle: 'idle', runId: null, lastSequence: 0, statusBar: null, events: [], result: null, error: null }
const active = new Set<RuntimeProgressLifecycle>(['creating', 'queued', 'running', 'retry_wait', 'finalizing'])

export function runtimeProgressReducer(state: RuntimeProgressState, action: RuntimeProgressAction): RuntimeProgressState {
  if (action.type === 'reset') return INITIAL_RUNTIME_PROGRESS
  if (action.type === 'creating') return { ...INITIAL_RUNTIME_PROGRESS, lifecycle: 'creating' }
  if (action.type === 'created') return { ...state, lifecycle: 'queued', runId: action.runId, error: null }
  if (action.type === 'resume') return { ...INITIAL_RUNTIME_PROGRESS, lifecycle: action.run.status, runId: action.run.run_id, result: action.run.result ?? null, error: action.run.error?.message ?? null, statusBar: terminalStatusBar(action.run.result ?? null) }
  if (action.type === 'result') return { ...state, lifecycle: 'succeeded', result: action.result, statusBar: terminalStatusBar(action.result), error: null }
  if (action.type === 'failed') return { ...state, lifecycle: 'failed', statusBar: state.statusBar ? { ...state.statusBar, run_state: 'failed' } : null, error: action.message }
  if (action.type === 'cancelled') return { ...state, lifecycle: 'cancelled', statusBar: state.statusBar ? { ...state.statusBar, run_state: 'cancelled' } : null, error: null }

  const sequence = action.event.sequence ?? 0
  if (sequence > 0 && sequence <= state.lastSequence) return state
  const lifecycle = lifecycleFromEvent(action.event, state.lifecycle)
  const result = action.event.result ?? state.result
  return {
    ...state,
    lifecycle,
    result,
    statusBar: action.event.status_bar ?? (result ? terminalStatusBar(result) : state.statusBar),
    lastSequence: Math.max(state.lastSequence, sequence),
    events: [...state.events, action.event],
    error: action.event.kind === 'error' ? action.event.summary : state.error,
  }
}

export const isRuntimeProcessing = (state: RuntimeProgressState) => active.has(state.lifecycle)

function lifecycleFromEvent(event: RuntimeSemanticEvent, current: RuntimeProgressLifecycle): RuntimeProgressLifecycle {
  if (event.kind === 'result') return 'succeeded'
  if (event.kind === 'error') return 'failed'
  if (event.kind === 'status' && ['queued', 'running', 'finalizing', 'retry_wait', 'succeeded', 'failed', 'cancelled'].includes(event.status)) return event.status as RuntimeRunStatus
  return current === 'creating' || current === 'queued' ? 'running' : current
}

function terminalStatusBar(result: AgentRunResponse | null): AgentStatusBar | null {
  if (!result?.status_bar) return null
  const runState: AgentStatusBar['run_state'] = result.outcome === 'needs_clarification' ? 'waiting_for_user' : result.outcome === 'blocked' ? 'blocked' : result.outcome === 'failed' ? 'failed' : 'completed'
  return { ...result.status_bar, run_state: runState, outcome: result.outcome }
}
