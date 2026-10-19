import { useEffect, useState } from 'react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { isForbidden, isUnauthorized } from '../api/http'
import { listImportJobs, type ImportJob, type ImportJobStatus } from '../api/importJobs'
import type { AppOutletContext } from '../components/AppShell'
import { Badge, Button, PageHeader, Segmented, StatePanel } from '../components/ui'
import { hasPermission } from '../auth/permissions'
import { formatDateTime } from '../lib/utils'

type LoadState = 'loading' | 'ready' | 'empty' | 'error' | 'unavailable' | 'unauthorized'

export const IMPORT_STATUS_LABELS: Record<ImportJobStatus, string> = {
  submitted: '已提交',
  validating: '校验中',
  validation_failed: '校验失败',
  approval_pending: '待审批',
  approved: '已批准',
  rejected: '已拒绝',
  publishing: '发布中',
  published: '已发布',
  publish_failed: '发布失败',
  cancelled: '已取消',
}

export function importStatusTone(status: ImportJobStatus): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (status === 'validation_failed' || status === 'publish_failed' || status === 'rejected') return 'danger'
  if (status === 'published') return 'success'
  if (status === 'approval_pending') return 'warning'
  if (status === 'validating' || status === 'publishing' || status === 'approved') return 'info'
  return 'neutral'
}

export function ImportStatusBadge({ status }: { status: ImportJobStatus }) {
  return <Badge tone={importStatusTone(status)}>{IMPORT_STATUS_LABELS[status]}</Badge>
}

const statusOptions: Array<{ value: '' | ImportJobStatus; label: string }> = [
  { value: '', label: '全部' },
  { value: 'approval_pending', label: '待审批' },
  { value: 'published', label: '已发布' },
  { value: 'publish_failed', label: '发布失败' },
]

export function ImportJobsPage() {
  const { period, me, meLoading, meError } = useOutletContext<AppOutletContext>()
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()
  const page = Math.max(1, Number(search.get('page') || 1))
  const rawStatus = search.get('status') ?? ''
  const status = (statusOptions.some((option) => option.value === rawStatus) ? rawStatus : '') as '' | ImportJobStatus
  const [state, setState] = useState<LoadState>('loading')
  const [message, setMessage] = useState('')
  const [items, setItems] = useState<ImportJob[]>([])
  const [total, setTotal] = useState(0)
  const [reload, setReload] = useState(0)
  const permitted = hasPermission(me?.permissions, 'data_management.read')
  const canSubmit = hasPermission(me?.permissions, 'data_import.submit')

  useEffect(() => {
    if (meLoading) { setState('loading'); return }
    if (meError) { setState('unavailable'); setMessage('无法读取当前主体的权限摘要。'); return }
    if (!permitted) { setState('unauthorized'); return }
    const controller = new AbortController()
    setState('loading')
    setMessage('')
    listImportJobs({ status: status || undefined, page, pageSize: 20, signal: controller.signal }).then((list) => {
      setItems(list.items)
      setTotal(list.total)
      setState(list.items.length ? 'ready' : 'empty')
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return
      if (isUnauthorized(reason) || isForbidden(reason)) { setState('unauthorized'); return }
      if (reason instanceof Error && 'status' in reason && (reason as { status: number }).status === 503) {
        setState('unavailable')
        setMessage(reason.message)
        return
      }
      setState('error')
      setMessage(reason instanceof Error ? reason.message : '读取导入任务失败')
    })
    return () => controller.abort()
  }, [meError, meLoading, page, permitted, reload, status])

  function updateQuery(key: string, value: string) {
    const next = new URLSearchParams(search)
    if (value) next.set(key, value)
    else next.delete(key)
    next.set('period', period)
    setSearch(next)
  }

  const openJob = (jobId: string) => navigate(`/data-admin/imports/${encodeURIComponent(jobId)}?period=${period}`)
  const retry = () => setReload((value) => value + 1)

  return <div>
    <PageHeader title="导入任务" actions={<>
      {canSubmit ? <Button variant="primary" onClick={() => navigate(`/data-admin/imports/new?period=${period}`)}>提交导入</Button> : null}
      <Button variant="ghost" onClick={retry}>刷新</Button>
    </>} />
    <section className="flex min-h-14 flex-wrap items-center gap-2 border-b border-[var(--border-soft)] px-4 py-2">
      <Segmented value={status} onChange={(nextStatus) => updateQuery('status', nextStatus)} label="导入任务状态筛选" options={statusOptions} />
      <span className="ml-auto meta">{total} 个任务 · {period}</span>
    </section>
    {state === 'unauthorized' ? <StatePanel state="unauthorized" title="无权访问导入任务" detail="当前主体没有 data_management.read 权限。" /> : null}
    {state === 'unavailable' ? <StatePanel state="unavailable" detail={message || undefined} onRetry={retry} /> : null}
    {state === 'error' ? <StatePanel state="error" detail={message} onRetry={retry} /> : null}
    {state === 'loading' ? <StatePanel state="loading" detail="正在读取当前租户的导入任务。" /> : null}
    {state === 'empty' ? <StatePanel state="empty" title={status ? '当前筛选条件下没有导入任务' : '尚无导入任务'} detail="提交导入任务后，任务状态与审批记录会出现在这里。" /> : null}
    {state === 'ready' ? <section>
      <div className="table-scroll"><table className="data-table" aria-label="导入任务列表"><thead><tr><th>状态</th><th>任务 ID</th><th>来源</th><th>快照哈希</th><th>有效 / 错误 / 总行数</th><th>提交主体</th><th>创建时间</th></tr></thead>
        <tbody>{items.map((item) => <tr key={item.job_id} tabIndex={0} onClick={() => openJob(item.job_id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openJob(item.job_id) } }}>
          <td><ImportStatusBadge status={item.status} /></td>
          <td className="num">{item.job_id}</td>
          <td>{item.source_system}<span className="meta ml-2">{item.source_file_name ?? ''}</span></td>
          <td className="num" title={item.snapshot_hash}>{truncateHash(item.snapshot_hash)}</td>
          <td className="num">{item.valid_rows} / {item.error_rows} / {item.total_rows}</td>
          <td className="num">{item.principal_id}</td>
          <td className="num">{formatDateTime(item.created_at)}</td>
        </tr>)}</tbody></table></div>
      <div className="flex min-h-14 items-center justify-end gap-3 border-t border-[var(--border-soft)] px-4">
        <Button size="sm" disabled={page <= 1} onClick={() => updateQuery('page', String(page - 1))}>上一页</Button>
        <span className="meta">第 {page} 页</span>
        <Button size="sm" disabled={page * 20 >= total} onClick={() => updateQuery('page', String(page + 1))}>下一页</Button>
      </div>
    </section> : null}
  </div>
}

function truncateHash(value: string) {
  return value.length <= 12 ? value : `${value.slice(0, 12)}…`
}
