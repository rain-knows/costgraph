import { useEffect, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { listAuditEvents, type AuditEvent } from '../api/audit'
import { isForbidden, isUnauthorized } from '../api/http'
import { hasPermission } from '../auth/permissions'
import type { AppOutletContext } from '../components/AppShell'
import { Badge, Button, PageHeader, StatePanel } from '../components/ui'
import { formatDateTime } from '../lib/utils'

type LoadState = 'loading' | 'ready' | 'empty' | 'error' | 'unavailable' | 'unauthorized'

const DETAIL_LIMIT = 6

export function AuditPage() {
  const { period, me, meLoading, meError } = useOutletContext<AppOutletContext>()
  const [search, setSearch] = useSearchParams()
  const page = Math.max(1, Number(search.get('page') || 1))
  const actor = search.get('actor_principal_id') ?? ''
  const action = search.get('action') ?? ''
  const [state, setState] = useState<LoadState>('loading')
  const [message, setMessage] = useState('')
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [total, setTotal] = useState(0)
  const [reload, setReload] = useState(0)
  const permitted = hasPermission(me?.permissions, 'audit.read')

  useEffect(() => {
    if (meLoading) { setState('loading'); return }
    if (meError) { setState('unavailable'); setMessage('无法读取当前主体的权限摘要。'); return }
    if (!permitted) { setState('unauthorized'); return }
    const controller = new AbortController()
    setState('loading')
    setMessage('')
    listAuditEvents({ page, pageSize: 20, actorPrincipalId: actor || undefined, action: action || undefined, signal: controller.signal }).then((list) => {
      setEvents(list.items)
      setTotal(list.total)
      setState(list.items.length ? 'ready' : 'empty')
    }).catch((failure: unknown) => {
      if (controller.signal.aborted) return
      if (isUnauthorized(failure) || isForbidden(failure)) { setState('unauthorized'); return }
      if (failure instanceof Error && 'status' in failure && (failure as { status: number }).status === 503) {
        setState('unavailable')
        setMessage(failure.message)
        return
      }
      setState('error')
      setMessage(failure instanceof Error ? failure.message : '读取审计日志失败')
    })
    return () => controller.abort()
  }, [action, actor, meError, meLoading, page, permitted, reload])

  function update(key: string, value: string) {
    const next = new URLSearchParams(search)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.set('page', '1')
    next.set('period', period)
    setSearch(next, { replace: key !== 'page' })
  }

  return <div>
    <PageHeader title="审计日志" actions={<Button variant="ghost" onClick={() => setReload((value) => value + 1)}>刷新</Button>} />
    <section className="flex min-h-14 flex-wrap items-center gap-2 border-b border-[var(--border-soft)] px-4 py-2">
      <label className="flex h-10 items-center gap-2 bg-[var(--surface)] px-3"><span className="label">操作者</span>
        <input value={actor} onChange={(event) => update('actor_principal_id', event.target.value)} placeholder="全部" className="w-40 bg-transparent font-mono text-sm" /></label>
      <label className="flex h-10 items-center gap-2 bg-[var(--surface)] px-3"><span className="label">动作</span>
        <input value={action} onChange={(event) => update('action', event.target.value)} placeholder="全部" className="w-32 bg-transparent font-mono text-sm" /></label>
      <span className="ml-auto meta">{total} 条事件 · {period}</span>
    </section>
    {state === 'unauthorized' ? <StatePanel state="unauthorized" title="无权访问审计日志" detail="当前主体没有 audit.read 权限。" /> : null}
    {state === 'unavailable' ? <StatePanel state="unavailable" detail={message || undefined} onRetry={() => setReload((value) => value + 1)} /> : null}
    {state === 'error' ? <StatePanel state="error" detail={message} onRetry={() => setReload((value) => value + 1)} /> : null}
    {state === 'loading' ? <StatePanel state="loading" detail="正在读取审计事件。" /> : null}
    {state === 'empty' ? <StatePanel state="empty" title={actor || action ? '当前筛选条件下没有事件' : '尚无审计事件'} detail="导入、审批、范围变更等操作会记录在这里。" /> : null}
    {state === 'ready' ? <section>
      <div className="table-scroll"><table className="data-table" aria-label="审计事件列表"><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>目标类型</th><th>目标 ID</th><th>结果</th><th>请求 ID</th><th>摘要</th></tr></thead>
        <tbody>{events.map((event) => <tr key={event.event_id}>
          <td className="num">{formatDateTime(event.created_at)}</td>
          <td className="num">{event.actor_principal_id}</td>
          <td className="num">{event.action}</td>
          <td>{event.target_type}</td>
          <td className="num">{event.target_id}</td>
          <td>{event.outcome === 'success' ? <Badge tone="success">成功</Badge> : <Badge tone="danger">失败</Badge>}</td>
          <td className="num">{event.request_id ?? '—'}</td>
          <td className="num">{detailSummary(event.detail)}</td>
        </tr>)}</tbody></table></div>
      <div className="flex min-h-14 items-center justify-end gap-3 border-t border-[var(--border-soft)] px-4">
        <Button size="sm" disabled={page <= 1} onClick={() => update('page', String(page - 1))}>上一页</Button>
        <span className="meta">第 {page} 页</span>
        <Button size="sm" disabled={page * 20 >= total} onClick={() => update('page', String(page + 1))}>下一页</Button>
      </div>
    </section> : null}
  </div>
}

/** 只渲染一层键值对；超过上限的键折叠成计数提示。 */
function detailSummary(detail: Record<string, unknown>): string {
  const entries = Object.entries(detail)
  if (!entries.length) return '—'
  const shown = entries.slice(0, DETAIL_LIMIT).map(([key, value]) => `${key}=${scalar(value)}`)
  const rest = entries.length - shown.length
  return rest > 0 ? `${shown.join(' ')} …另有 ${rest} 个键` : shown.join(' ')
}

function scalar(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return Array.isArray(value) ? `[${value.length} 项]` : '对象'
}
