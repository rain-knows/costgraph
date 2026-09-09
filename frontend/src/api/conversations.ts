import type { AgentRunResponse, AgentStreamMessage, CapabilityId, RoutingMode, RuntimeSemanticEvent } from './agent'
import { apiError, apiRequest } from './http'

export type ConversationRunSummary = {
  run_id: string
  outcome: AgentRunResponse['outcome']
  clarification?: AgentRunResponse['clarification'] | null
  event_count: number
  inherited_product?: string | null
  has_report: boolean
}

export type ConversationMessage = {
  message_id: string
  turn_id?: string | null
  role: 'user' | 'assistant'
  content: string
  run?: ConversationRunSummary | AgentRunResponse | null
  created_at: string
}

export type ConversationSummary = {
  conversation_id: string
  title: string
  routing_mode: RoutingMode
  enabled_capabilities: CapabilityId[]
  message_count: number
  created_at: string
  updated_at: string
}

export type Conversation = ConversationSummary & {
  context: Record<string, unknown>
  messages: ConversationMessage[]
}

export type ConversationMessagesResponse = {
  items: ConversationMessage[]
  next_cursor: string | null
  has_more: boolean
}

export type RuntimeRunStatus = 'queued' | 'running' | 'finalizing' | 'retry_wait' | 'succeeded' | 'failed' | 'cancelled'

export type RuntimeRun = {
  schema_version: '2.0'
  api_version: '2.0'
  runtime_version: string
  workflow_version: string
  trace_id: string
  run_id: string
  conversation_id: string
  turn_id: string
  message_id: string
  status: RuntimeRunStatus
  attempt_count: number
  created_at: string
  updated_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  cancel_requested_at?: string | null
  last_event_id?: number | null
  request_id: string
  events_url?: string | null
  result?: AgentRunResponse | null
  error?: { code: string; message: string; request_id: string; details?: Record<string, unknown> } | null
  budget: { limits: Record<string, unknown>; usage: Record<string, unknown> }
}

export class RunCancelledError extends Error {}

const runtimePrefix = '/api/v2/conversations'

export const createConversation = (title = '新建成本分析') => apiRequest<Conversation>('/api/conversations', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ title, routing_mode: 'auto', enabled_capabilities: ['system_help', 'cost_calculation'] }),
})

export const listConversations = () => apiRequest<ConversationSummary[]>('/api/conversations')

export const getConversation = (conversationId: string) =>
  apiRequest<Conversation>(`/api/conversations/${encodeURIComponent(conversationId)}`)

export const updateConversationTitle = (conversationId: string, title: string) =>
  apiRequest<Conversation>(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })

export const deleteConversation = (conversationId: string) =>
  apiRequest<void>(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })

export function listConversationMessages(conversationId: string, limit = 100) {
  return apiRequest<ConversationMessagesResponse>(`/api/conversations/${encodeURIComponent(conversationId)}/messages?limit=${limit}`)
}

export function createConversationRun(
  conversationId: string,
  content: string,
  options: { messageId: string; replyToClarificationId?: string | null },
) {
  return apiRequest<RuntimeRun>(`${runtimePrefix}/${encodeURIComponent(conversationId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message_id: options.messageId,
      content,
      routing_mode: 'auto',
      enabled_capabilities: ['system_help', 'cost_calculation'],
      reply_to_clarification_id: options.replyToClarificationId,
    }),
  })
}

export const getActiveConversationRun = (conversationId: string) =>
  apiRequest<RuntimeRun | null>(`${runtimePrefix}/${encodeURIComponent(conversationId)}/runs/active`)

export const getConversationRun = (conversationId: string, runId: string) =>
  apiRequest<RuntimeRun>(`${runtimePrefix}/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}`)

export const cancelConversationRun = (conversationId: string, runId: string) =>
  apiRequest<RuntimeRun>(`${runtimePrefix}/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })

export async function streamConversationRun(
  conversationId: string,
  runId: string,
  onMessage: (message: AgentStreamMessage) => void,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  const retryDelays = [1000, 2000, 5000]
  let retryIndex = 0
  let lastEventId = 0

  while (true) {
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (lastEventId) headers['Last-Event-ID'] = String(lastEventId)
    try {
      const response = await fetch(`${runtimePrefix}/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}/events?after_event_id=${lastEventId}`, { headers, signal })
      if (!response.ok) throw await apiError(response, '订阅 Run 事件失败')
      if (!response.body) throw new Error('Run 事件流没有可读数据。')
      const result = await consumeEventStream(response, (message, eventId) => {
        lastEventId = Math.max(lastEventId, eventId)
        onMessage(message)
      }, signal)
      if (result) return result
      retryIndex = 0
    } catch (error) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      if (error instanceof RunCancelledError) throw error
    }

    const run = await getConversationRun(conversationId, runId)
    if (run.status === 'succeeded' && run.result) return run.result
    if (run.status === 'failed') throw new Error(`${run.error?.message || '运行失败。'}（请求 ${run.error?.request_id || run.request_id}）`)
    if (run.status === 'cancelled') throw new RunCancelledError('运行已取消')
    await abortableDelay(retryDelays[Math.min(retryIndex, retryDelays.length - 1)], signal)
    retryIndex += 1
  }
}

async function consumeEventStream(
  response: Response,
  onMessage: (message: AgentStreamMessage, eventId: number) => void,
  signal?: AbortSignal,
): Promise<AgentRunResponse | null> {
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: AgentRunResponse | null = null
  let streamError: Error | null = null

  const consumeBlock = (block: string) => {
    const lines = block.replaceAll('\r', '').split('\n')
    if (lines.every((line) => !line || line.startsWith(':'))) return
    const eventType = lines.find((line) => line.startsWith('event: '))?.slice(7) ?? 'message'
    const eventId = Number(lines.find((line) => line.startsWith('id: '))?.slice(4) ?? 0)
    const data = lines.filter((line) => line.startsWith('data: ')).map((line) => line.slice(6)).join('\n')
    if (!data) return
    const payload = JSON.parse(data) as Record<string, unknown>

    if (eventType === 'result' && typeof payload.result === 'object' && payload.result) {
      result = payload.result as AgentRunResponse
      onMessage({ type: 'result', result }, eventId)
      return
    }
    if (payload.schema_version === '2.0') {
      const event: RuntimeSemanticEvent = {
        node: String(payload.name ?? eventType),
        name: typeof payload.name === 'string' ? payload.name : null,
        kind: eventType,
        status: String(payload.status ?? 'success'),
        summary: String(payload.summary ?? ''),
        sequence: typeof payload.sequence === 'number' ? payload.sequence : eventId,
        status_bar: typeof payload.status_bar === 'object' ? payload.status_bar as RuntimeSemanticEvent['status_bar'] : null,
        result: typeof payload.result === 'object' ? payload.result as AgentRunResponse : null,
        clarification: typeof payload.clarification === 'object' ? payload.clarification as RuntimeSemanticEvent['clarification'] : null,
        request_id: typeof payload.request_id === 'string' ? payload.request_id : null,
      }
      onMessage({ type: 'runtime_event', event }, eventId)
      if (event.result) result = event.result
      if (eventType === 'error') streamError = new Error(`${event.summary || '运行失败。'}${event.request_id ? `（请求 ${event.request_id}）` : ''}`)
      return
    }
    if (eventType === 'error') {
      const message = String(payload.message ?? '运行失败。')
      streamError = new Error(message)
      onMessage({ type: 'error', message, request_id: typeof payload.request_id === 'string' ? payload.request_id : undefined }, eventId)
    }
  }

  while (true) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    blocks.forEach(consumeBlock)
    if (streamError) throw streamError
    if (done) break
  }
  if (buffer.trim()) consumeBlock(buffer)
  if (streamError) throw streamError
  return result
}

function abortableDelay(milliseconds: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, milliseconds)
    signal?.addEventListener('abort', () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}
