import { Check, CircleDashed, CircleX, OctagonX, Square } from 'lucide-react'
import type { AgentStatusBar as AgentStatus, RunOutcome } from '../api/agent'
import type { RuntimeProgressLifecycle } from '../features/agent-run/runtimeProgress'
import { Badge, Button } from './ui'

const lifecycleLabels: Record<RuntimeProgressLifecycle, string> = {
  idle: '等待任务',
  creating: '正在创建',
  queued: '等待 Worker',
  running: '执行中',
  retry_wait: '等待重试',
  finalizing: '正在提交结果',
  succeeded: '本轮结束',
  failed: '执行失败',
  cancelled: '已取消',
}

const outcomeLabels: Record<RunOutcome, string> = {
  completed: '已完成',
  needs_clarification: '等待补充',
  blocked: '策略阻止',
  failed: '执行失败',
}

export function AgentStatusBar({ lifecycle, status, onCancel }: { lifecycle: RuntimeProgressLifecycle; status: AgentStatus | null; onCancel?: () => void }) {
  const processing = ['creating', 'queued', 'running', 'retry_wait', 'finalizing'].includes(lifecycle)
  const tone = lifecycle === 'failed' || lifecycle === 'cancelled' || status?.run_state === 'blocked' ? 'danger' : status?.run_state === 'waiting_for_user' || lifecycle === 'retry_wait' ? 'warning' : lifecycle === 'succeeded' ? 'success' : 'info'
  const todos = status?.todo ?? []
  return (
    <section className="border-b border-[var(--border-soft)] bg-[var(--surface)]" aria-label="Agent 运行状态">
      <div className="flex min-h-12 flex-wrap items-center gap-3 px-4 py-2">
        <span className="label">LangGraph Runtime</span>
        <Badge tone={tone}>{status?.outcome ? outcomeLabels[status.outcome] : lifecycleLabels[lifecycle]}</Badge>
        <span className="min-w-0 flex-1 truncate text-xs text-[var(--fg-2)]">{status?.current_step || (processing ? '正在建立本轮执行上下文' : '金额和公式由服务端确定性计算')}</span>
        {processing && onCancel ? <Button size="sm" variant="ghost" onClick={onCancel}><Square className="h-3.5 w-3.5 fill-current" />取消</Button> : null}
      </div>
      {todos.length > 0 ? <div className="flex overflow-x-auto border-t border-[var(--border-soft)] px-4 py-3">
        {todos.map((todo, index) => <div key={todo.id} className="flex min-w-[132px] flex-1 items-start">
          <div className="flex flex-col items-center">
            <span className={`grid h-6 w-6 place-items-center rounded-full border ${todo.status === 'done' ? 'border-[var(--success)] bg-[var(--success)] text-white' : todo.status === 'current' ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-[var(--border)] text-[var(--muted)]'}`}>
              {todo.status === 'done' ? <Check className="h-3.5 w-3.5" /> : todo.status === 'current' ? <CircleDashed className="h-3.5 w-3.5 animate-spin" /> : index + 1}
            </span>
            <span className="mt-1.5 max-w-28 text-center text-[10px] leading-4 text-[var(--fg-2)]">{todo.label}</span>
          </div>
          {index < todos.length - 1 ? <span className={`mt-3 h-px flex-1 ${todo.status === 'done' ? 'bg-[var(--success)]' : 'bg-[var(--border)]'}`} /> : null}
        </div>)}
      </div> : null}
      {lifecycle === 'failed' ? <div className="flex items-center gap-2 border-t border-[var(--danger)] px-4 py-2 text-xs text-[var(--danger)]"><CircleX className="h-4 w-4" />运行未能完成，请检查右侧事件。</div> : null}
      {status?.run_state === 'blocked' ? <div className="flex items-center gap-2 border-t border-[var(--danger)] px-4 py-2 text-xs text-[var(--danger)]"><OctagonX className="h-4 w-4" />请求已被服务端策略阻止。</div> : null}
    </section>
  )
}
