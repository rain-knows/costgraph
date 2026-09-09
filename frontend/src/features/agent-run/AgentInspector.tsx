import { Database, GitBranch, ShieldCheck } from 'lucide-react'
import type { RuntimeProgressState } from './runtimeProgress'
import { Badge } from '../../components/ui'

export function AgentInspector({ progress }: { progress: RuntimeProgressState }) {
  const status = progress.statusBar
  return (
    <aside className="agent-inspector border-l border-[var(--border-soft)] bg-[var(--surface)]" aria-label="Runtime 检查器">
      <div className="flex h-12 items-center justify-between border-b border-[var(--border)] px-4">
        <span className="flex items-center gap-2"><GitBranch className="h-4 w-4" /><strong className="font-semibold">Runtime 检查器</strong></span>
        <Badge tone="info">只读</Badge>
      </div>
      <div className="space-y-5 p-4 text-xs">
        <section>
          <p className="label">能力路由</p>
          <dl className="mt-3 grid grid-cols-[104px_1fr] gap-y-2">
            <dt className="text-[var(--muted)]">请求路由</dt><dd className="break-all font-mono">{status?.requested_route ?? '—'}</dd>
            <dt className="text-[var(--muted)]">生效路由</dt><dd className="break-all font-mono">{status?.effective_route ?? '—'}</dd>
            <dt className="text-[var(--muted)]">当前阶段</dt><dd>{status?.phase ?? progress.lifecycle}</dd>
          </dl>
        </section>
        <section className="border-t border-[var(--border)] pt-4">
          <p className="label">服务端授权</p>
          <p className="mt-3 flex gap-2"><ShieldCheck className="h-4 w-4 text-[var(--success)]" />仅开放系统帮助与成本计算</p>
          <p className="mt-2 flex gap-2"><Database className="h-4 w-4 text-[var(--accent)]" />{status?.authorization.data_scope.source ?? '等待范围校验'}</p>
          {status?.authorization.data_scope.allowed_product_ids.length ? <p className="mt-2 break-words text-[var(--fg-2)]">产品范围：{status.authorization.data_scope.allowed_product_ids.join('、')}</p> : null}
        </section>
        <section className="border-t border-[var(--border)] pt-4">
          <div className="flex items-center justify-between"><p className="label">持久化事件</p><span className="num text-[var(--muted)]">{progress.events.length}</span></div>
          <ol className="mt-3 max-h-[340px] space-y-3 overflow-y-auto">
            {!progress.events.length ? <li className="text-[var(--muted)]">本轮尚无事件。</li> : progress.events.map((event, index) => <li key={`${event.sequence ?? index}-${event.kind ?? event.node}`} className="border-l-2 border-[var(--border)] pl-3">
              <div className="flex items-center justify-between gap-2"><strong className="font-medium">{event.name || event.node}</strong><span className="font-mono text-[10px] text-[var(--muted)]">#{event.sequence ?? index + 1}</span></div>
              <p className="mt-1 leading-5 text-[var(--fg-2)]">{event.summary || event.status}</p>
            </li>)}
          </ol>
        </section>
        {progress.error ? <p className="border-l-2 border-[var(--danger)] pl-3 leading-5 text-[var(--danger)]" role="alert">{progress.error}</p> : null}
      </div>
    </aside>
  )
}
