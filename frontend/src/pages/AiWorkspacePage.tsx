import { type FormEvent, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Bot, FileBarChart2, MessageSquarePlus, Search, Send, User } from 'lucide-react'
import { useNavigate, useOutletContext, useParams } from 'react-router-dom'
import type { AgentRunResponse, AgentStreamMessage, Clarification } from '../api/agent'
import {
  cancelConversationRun,
  createConversation,
  createConversationRun,
  getActiveConversationRun,
  getConversationRun,
  listConversationMessages,
  listConversations,
  RunCancelledError,
  streamConversationRun,
  type ConversationMessage,
  type ConversationSummary,
  type RuntimeRun,
} from '../api/conversations'
import type { AppOutletContext } from '../components/AppShell'
import { AgentStatusBar } from '../components/AgentStatusBar'
import { Badge, Button, PageHeader } from '../components/ui'
import { AgentInspector } from '../features/agent-run/AgentInspector'
import { INITIAL_RUNTIME_PROGRESS, isRuntimeProcessing, runtimeProgressReducer } from '../features/agent-run/runtimeProgress'
import { ClarificationCard } from '../features/chat/ClarificationCard'
import { cn } from '../lib/utils'

export function AiWorkspacePage() {
  const { conversationId } = useParams()
  const { period } = useOutletContext<AppOutletContext>()
  const navigate = useNavigate()
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [search, setSearch] = useState('')
  const [input, setInput] = useState('')
  const [replyToClarificationId, setReplyToClarificationId] = useState<string | null>(null)
  const [loadingConversation, setLoadingConversation] = useState(false)
  const [creatingConversation, setCreatingConversation] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [progress, dispatch] = useReducer(runtimeProgressReducer, INITIAL_RUNTIME_PROGRESS)
  const streamController = useRef<AbortController | null>(null)

  const refreshConversations = useCallback(async () => {
    const items = await listConversations()
    setConversations(items)
  }, [])

  const refreshMessages = useCallback(async (id: string) => {
    const response = await listConversationMessages(id)
    setMessages(response.items)
    const clarification = findActiveClarification(response.items)
    setReplyToClarificationId(clarification?.id ?? null)
  }, [])

  const followRun = useCallback(async (id: string, run: RuntimeRun) => {
    streamController.current?.abort()
    const controller = new AbortController()
    streamController.current = controller
    try {
      const result = await streamConversationRun(id, run.run_id, (message: AgentStreamMessage) => {
        if (message.type === 'runtime_event') dispatch({ type: 'event', event: message.event })
        if (message.type === 'result') dispatch({ type: 'result', result: message.result })
        if (message.type === 'error') dispatch({ type: 'failed', message: message.message })
      }, controller.signal)
      dispatch({ type: 'result', result })
      setReplyToClarificationId(result.clarification?.id ?? null)
      await Promise.all([refreshMessages(id), refreshConversations()])
    } catch (reason) {
      if (controller.signal.aborted) return
      if (reason instanceof RunCancelledError) dispatch({ type: 'cancelled' })
      else dispatch({ type: 'failed', message: reason instanceof Error ? reason.message : '运行失败' })
      await refreshMessages(id).catch(() => undefined)
    }
  }, [refreshConversations, refreshMessages])

  useEffect(() => {
    void refreshConversations().catch((reason: unknown) => setRequestError(reason instanceof Error ? reason.message : '读取会话失败'))
  }, [refreshConversations])

  useEffect(() => {
    if (!conversationId && conversations[0]) navigate(`/ai/${encodeURIComponent(conversations[0].conversation_id)}?period=${period}`, { replace: true })
  }, [conversationId, conversations, navigate, period])

  useEffect(() => {
    streamController.current?.abort()
    dispatch({ type: 'reset' })
    setMessages([]); setRequestError(''); setReplyToClarificationId(null)
    if (!conversationId) return
    let disposed = false
    setLoadingConversation(true)
    void (async () => {
      const [messageResponse, activeRun] = await Promise.all([
        listConversationMessages(conversationId),
        getActiveConversationRun(conversationId),
      ])
      if (disposed) return
      setMessages(messageResponse.items)
      setReplyToClarificationId(findActiveClarification(messageResponse.items)?.id ?? null)

      const latestRunId = findLatestRunId(messageResponse.items)
      const run = activeRun ?? (latestRunId ? await getConversationRun(conversationId, latestRunId) : null)
      if (disposed || !run) return
      dispatch({ type: 'resume', run })
      void followRun(conversationId, run)
    })().catch((reason: unknown) => { if (!disposed) setRequestError(reason instanceof Error ? reason.message : '读取会话失败') }).finally(() => { if (!disposed) setLoadingConversation(false) })
    return () => { disposed = true; streamController.current?.abort() }
  }, [conversationId, followRun])

  async function newConversation() {
    if (creatingConversation) return
    setCreatingConversation(true); setRequestError('')
    try {
      const conversation = await createConversation()
      await refreshConversations()
      navigate(`/ai/${encodeURIComponent(conversation.conversation_id)}?period=${period}`)
    } catch (reason) {
      setRequestError(reason instanceof Error ? reason.message : '创建会话失败')
    } finally {
      setCreatingConversation(false)
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault()
    const content = input.trim()
    if (!content || !conversationId || isRuntimeProcessing(progress)) return
    const messageId = crypto.randomUUID()
    setInput(''); setRequestError('')
    setMessages((current) => [...current, { message_id: messageId, role: 'user', content, created_at: new Date().toISOString() }])
    dispatch({ type: 'creating' })
    try {
      const run = await createConversationRun(conversationId, content, { messageId, replyToClarificationId })
      setReplyToClarificationId(null)
      dispatch({ type: 'created', runId: run.run_id })
      await followRun(conversationId, run)
    } catch (reason) {
      dispatch({ type: 'failed', message: reason instanceof Error ? reason.message : '创建运行失败' })
    }
  }

  async function cancel() {
    if (!conversationId || !progress.runId) return
    try {
      await cancelConversationRun(conversationId, progress.runId)
      streamController.current?.abort()
      dispatch({ type: 'cancelled' })
      await refreshMessages(conversationId)
    } catch (reason) {
      dispatch({ type: 'failed', message: reason instanceof Error ? reason.message : '取消运行失败' })
    }
  }

  function chooseClarification(value: string, clarificationId: string) {
    setInput(value)
    setReplyToClarificationId(clarificationId)
  }

  const filtered = useMemo(() => conversations.filter((item) => !search || item.title.toLowerCase().includes(search.toLowerCase())), [conversations, search])
  const liveResult = progress.result
  const liveClarification = liveResult?.clarification ?? null

  return <div>
    <PageHeader title="AI 分析工作台" />
    <div className="agent-workspace-grid grid min-h-[calc(100vh-112px)] grid-cols-[248px_minmax(420px,1fr)_320px] mobile-stack">
      <aside className="agent-conversation-list border-r border-[var(--border-soft)] bg-[var(--surface)]">
        <div className="p-3"><Button variant="primary" className="w-full" disabled={creatingConversation} onClick={() => void newConversation()}><MessageSquarePlus className="h-4 w-4" />{creatingConversation ? '正在创建' : '新建会话'}</Button><label className="mt-3 flex h-10 items-center gap-2 bg-[var(--bg)] px-3"><Search className="h-4 w-4" /><span className="sr-only">搜索会话</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索会话" className="min-w-0 flex-1 bg-transparent" /></label></div>
        <nav className="border-t border-[var(--border-soft)]" aria-label="会话历史">{filtered.map((item) => <button key={item.conversation_id} className={cn('w-full border-b border-[var(--border-soft)] p-3 text-left hover:bg-[var(--bg)]', item.conversation_id === conversationId && 'bg-[var(--surface-warm)]')} onClick={() => navigate(`/ai/${encodeURIComponent(item.conversation_id)}?period=${period}`)}><span className="line-clamp-2 text-sm">{item.title}</span><span className="meta mt-2 block">{item.message_count} 条消息 · {formatTime(item.updated_at)}</span></button>)}</nav>
      </aside>

      <section className="flex min-h-[620px] min-w-0 flex-col">
        <AgentStatusBar lifecycle={progress.lifecycle} status={progress.statusBar} onCancel={() => void cancel()} />
        <div className="min-h-[500px] flex-1 space-y-4 overflow-y-auto p-5" aria-live="polite">
          {!conversationId ? <EmptyConversation onCreate={() => void newConversation()} /> : null}
          {loadingConversation ? <p className="text-center text-sm text-[var(--muted)]">正在读取会话…</p> : null}
          {messages.map((message) => <Message key={message.message_id} message={message} activeClarificationId={replyToClarificationId} onClarification={chooseClarification} />)}
          {liveResult?.final_message && !messages.some((message) => message.role === 'assistant' && message.content === liveResult.final_message) ? <article className="flex gap-3"><Avatar role="assistant" /><div className="max-w-[84%] bg-[var(--surface)] p-3 text-sm leading-6"><p className="whitespace-pre-wrap">{liveResult.final_message}</p>{liveClarification ? <div className="mt-3"><ClarificationCard clarification={liveClarification} onSelect={chooseClarification} /></div> : null}{liveResult.report_json ? <Button className="mt-3" size="sm" variant="ghost" onClick={() => navigate(`/reports?period=${period}&run=${encodeURIComponent(liveResult.run_id)}`)}><FileBarChart2 className="h-4 w-4" />查看结构化报表</Button> : null}</div></article> : null}
          {requestError ? <p className="border-l-2 border-[var(--danger)] px-3 py-2 text-sm text-[var(--danger)]" role="alert">{requestError}</p> : null}
        </div>
        <form onSubmit={send} className="border-t border-[var(--border)] bg-[var(--bg)] p-3"><div className="flex min-h-12 items-end bg-[var(--surface)]"><textarea value={input} onChange={(event) => setInput(event.target.value)} disabled={!conversationId || isRuntimeProcessing(progress)} rows={1} placeholder={conversationId ? '产品A 2026-06 成本' : '先新建或选择会话'} className="max-h-32 min-h-12 flex-1 resize-y bg-transparent px-4 py-3 text-sm" /><button type="submit" disabled={!input.trim() || !conversationId || isRuntimeProcessing(progress)} className="grid h-12 w-12 place-items-center bg-[var(--accent)] text-[var(--accent-on)] disabled:opacity-50" aria-label="发送消息"><Send className="h-4 w-4" /></button></div><p className="mt-2 text-xs text-[var(--muted)]">仅支持系统帮助和成本计算；缺少产品或期间时会先请求补充。</p></form>
      </section>
      <AgentInspector progress={progress} />
    </div>
  </div>
}

function Message({ message, activeClarificationId, onClarification }: { message: ConversationMessage; activeClarificationId: string | null; onClarification: (value: string, clarificationId: string) => void }) {
  const clarification = message.run?.clarification as Clarification | null | undefined
  const hasReport = 'has_report' in (message.run ?? {}) ? Boolean((message.run as { has_report?: boolean }).has_report) : Boolean((message.run as AgentRunResponse | null)?.report_json)
  return <article className={cn('flex gap-3', message.role === 'user' && 'flex-row-reverse')}><Avatar role={message.role} /><div className={cn('max-w-[84%] p-3 text-sm leading-6', message.role === 'assistant' ? 'bg-[var(--surface)]' : 'border border-[var(--border)]')}><p className="whitespace-pre-wrap">{message.content}</p>{clarification ? <div className="mt-3"><ClarificationCard clarification={clarification} disabled={clarification.id !== activeClarificationId} onSelect={onClarification} /></div> : null}{hasReport ? <Badge tone="success" className="mt-3">已生成 Artifact</Badge> : null}</div></article>
}

function Avatar({ role }: { role: 'user' | 'assistant' }) { return <span className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-full', role === 'assistant' ? 'bg-[var(--fg)] text-[var(--bg)]' : 'bg-[var(--surface)]')} aria-hidden="true">{role === 'assistant' ? <Bot className="h-4 w-4" /> : <User className="h-4 w-4" />}</span> }
function EmptyConversation({ onCreate }: { onCreate: () => void }) { return <div className="grid min-h-[420px] place-items-center"><div className="max-w-sm text-center"><Bot className="mx-auto h-8 w-8 text-[var(--muted)]" /><h2 className="mt-4 text-xl font-normal">开始成本分析</h2><p className="mt-2 text-sm leading-6 text-[var(--fg-2)]">新建会话后，可查询产品期间成本或了解系统能力。</p><Button className="mt-5" variant="primary" onClick={onCreate}><MessageSquarePlus className="h-4 w-4" />新建会话</Button></div></div> }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date) }
function findActiveClarification(messages: ConversationMessage[]): Clarification | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') return null
    const clarification = messages[index].run?.clarification as Clarification | null | undefined
    if (clarification) return clarification
  }
  return null
}

function findLatestRunId(messages: ConversationMessage[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const runId = messages[index].run?.run_id
    if (runId) return runId
  }
  return null
}
