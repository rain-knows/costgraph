import { useEffect, useState } from 'react'
import { Link, useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom'
import type { DataAdminError } from '../api/dataAdmin'
import { isForbidden, isNotFound, isUnauthorized } from '../api/http'
import { cancelImportJob, decideImportJob, getImportJob, listImportJobErrors, type ImportJobDetail } from '../api/importJobs'
import { hasPermission } from '../auth/permissions'
import type { AppOutletContext } from '../components/AppShell'
import { Badge, Button, PageHeader, StatePanel } from '../components/ui'
import { formatDateTime, formatNumber } from '../lib/utils'
import { ImportStatusBadge } from './ImportJobsPage'

type LoadState = 'loading' | 'ready' | 'error' | 'unavailable' | 'unauthorized' | 'not-found'

const DECISION_LABELS = { approved: '已批准', rejected: '已拒绝' } as const
const REASON_LIMIT = 500

export function ImportJobDetailPage() {
  const { period, me, meLoading, meError } = useOutletContext<AppOutletContext>()
  const { jobId = '' } = useParams()
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()
  const errorPage = Math.max(1, Number(search.get('error_page') || 1))
  const [state, setState] = useState<LoadState>('loading')
  const [message, setMessage] = useState('')
  const [job, setJob] = useState<ImportJobDetail | null>(null)
  const [errors, setErrors] = useState<DataAdminError[]>([])
  const [errorTotal, setErrorTotal] = useState(0)
  const [reload, setReload] = useState(0)
  const [reason, setReason] = useState('')
  const [confirming, setConfirming] = useState<'cancel' | null>(null)
  const [actionError, setActionError] = useState('')
  const [pending, setPending] = useState(false)
  const permitted = hasPermission(me?.permissions, 'data_management.read')
  const canApprove = hasPermission(me?.permissions, 'data_import.approve')
  const canSubmit = hasPermission(me?.permissions, 'data_import.submit')
  const status = job?.status
  const needsErrors = status === 'validation_failed'

  useEffect(() => {
    if (meLoading) { setState('loading'); return }
    if (meError) { setState('unavailable'); setMessage('无法读取当前主体的权限摘要。'); return }
    if (!permitted) { setState('unauthorized'); return }
    const controller = new AbortController()
    setState('loading')
    setMessage('')
    getImportJob(jobId, controller.signal).then(async (detail) => {
      setJob(detail)
      if (detail.status === 'validation_failed') {
        const list = await listImportJobErrors(jobId, { page: errorPage, pageSize: 20, signal: controller.signal })
        setErrors(list.items)
        setErrorTotal(list.total)
      } else {
        setErrors([])
        setErrorTotal(0)
      }
      setState('ready')
    }).catch((failure: unknown) => {
      if (controller.signal.aborted) return
      if (isNotFound(failure)) { setState('not-found'); setJob(null); return }
      if (isUnauthorized(failure) || isForbidden(failure)) { setState('unauthorized'); return }
      if (failure instanceof Error && 'status' in failure && (failure as { status: number }).status === 503) {
        setState('unavailable')
        setMessage(failure.message)
        return
      }
      setState('error')
      setMessage(failure instanceof Error ? failure.message : '读取导入任务详情失败')
    })
    return () => controller.abort()
  }, [errorPage, jobId, meError, meLoading, permitted, reload])

  function updateErrorPage(value: number) {
    const next = new URLSearchParams(search)
    next.set('error_page', String(value))
    next.set('period', period)
    setSearch(next)
  }

  async function run(action: () => Promise<ImportJobDetail>) {
    setPending(true)
    setActionError('')
    try {
      setJob(await action())
      setReason('')
      setConfirming(null)
      setReload((value) => value + 1)
    } catch (failure: unknown) {
      if (isForbidden(failure)) setActionError('当前主体没有执行该操作的权限。')
      else if (isNotFound(failure)) setActionError('任务不存在或不属于当前租户。')
      else if (failure instanceof Error && 'status' in failure && (failure as { status: number }).status === 503) setActionError(`服务暂不可用：${failure.message}`)
      else setActionError(failure instanceof Error ? failure.message : '操作未能完成')
    } finally {
      setPending(false)
    }
  }

  const retry = () => setReload((value) => value + 1)
  const reasonValid = reason.trim().length > 0 && reason.length <= REASON_LIMIT

  return <div>
    <PageHeader title="导入任务详情" actions={<>
      <Button variant="ghost" onClick={() => navigate(`/data-admin/imports?period=${period}`)}>返回列表</Button>
      <Button variant="ghost" onClick={retry}>刷新</Button>
    </>} />
    {state === 'loading' ? <StatePanel state="loading" detail="正在读取任务状态与明细。" /> : null}
    {state === 'unauthorized' ? <StatePanel state="unauthorized" title="无权访问该导入任务" detail="当前主体没有 data_management.read 权限。" /> : null}
    {state === 'unavailable' ? <StatePanel state="unavailable" detail={message || undefined} onRetry={retry} /> : null}
    {state === 'error' ? <StatePanel state="error" detail={message} onRetry={retry} /> : null}
    {state === 'not-found' ? <StatePanel state="empty" title="任务不存在" detail={`任务 ${jobId} 不存在或不属于当前租户。`} /> : null}
    {state === 'ready' && job ? <>
      <section className="border-b border-[var(--border-soft)] px-4 py-4" aria-label="任务概要">
        <div className="flex flex-wrap items-center gap-3">
          <ImportStatusBadge status={job.status} />
          <span className="num break-all text-sm">{job.job_id}</span>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm mobile-stack">
          <div><dt className="label">提交主体</dt><dd className="num">{job.principal_id}</dd></div>
          <div><dt className="label">提交时间</dt><dd className="num">{formatDateTime(job.created_at)}</dd></div>
          <div><dt className="label">规则版本</dt><dd className="num">{job.calculation_rule_version}</dd></div>
          <div><dt className="label">来源</dt><dd>{job.source_system}<span className="meta ml-2">{job.source_file_name ?? '—'}</span></dd></div>
        </dl>
      </section>

      <section className="grid grid-cols-3 border-b border-[var(--border-soft)] mobile-stack" aria-label="任务统计">
        <article className="border-r border-[var(--border-soft)] p-4">
          <p className="label">行数</p>
          <p className="mt-2 text-sm">总 {formatNumber(job.total_rows)} · 有效 {formatNumber(job.valid_rows)} · 错误 {formatNumber(job.error_rows)}</p>
          <p className="meta mt-1">尝试次数 {job.attempt_count}</p>
        </article>
        <article className="border-r border-[var(--border-soft)] p-4">
          <p className="label">对象</p>
          <p className="num mt-2 break-all text-sm" title={job.source_object_key}>{job.source_object_key}</p>
          <p className="meta mt-1">大小 {formatNumber(job.source_object_size)} 字节</p>
        </article>
        <article className="p-4">
          <p className="label">哈希</p>
          <p className="num mt-2 break-all text-sm">快照 {job.snapshot_hash}</p>
          <p className="num meta mt-1 break-all">对象 {job.source_object_sha256}</p>
        </article>
      </section>

      <section className="border-b border-[var(--border-soft)] px-4 py-4" aria-label="状态与操作">
        <div className="panel-head !px-0 !border-0"><div><h2 className="section-title">当前状态</h2><p className="meta mt-1">{statusDetail(job)}</p></div></div>
        {actionError ? <p className="mt-3 text-sm text-[var(--danger)]" role="alert">{actionError}</p> : null}

        {job.status === 'approval_pending' && canApprove ? <div className="mt-3 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="label">拒绝原因（拒绝时必填，最多 {REASON_LIMIT} 字）</span>
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} maxLength={REASON_LIMIT} className="w-full max-w-xl bg-[var(--surface)] px-3 py-2 text-sm" />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={pending} onClick={() => void run(() => decideImportJob(jobId, { decision: 'approve', reason: reason.trim() || undefined }))}>审批通过</Button>
            <Button variant="danger" disabled={pending || !reasonValid} onClick={() => void run(() => decideImportJob(jobId, { decision: 'reject', reason: reason.trim() }))}>{pending ? '正在提交' : '拒绝'}</Button>
          </div>
          {reasonValid ? null : <p className="meta">拒绝前必须填写原因且不超过 {REASON_LIMIT} 字。</p>}
        </div> : null}

        {['submitted', 'validating', 'approval_pending'].includes(job.status) && canSubmit ? <div className="mt-4">
          {confirming === 'cancel' ? <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm">确认取消任务 {job.job_id}？取消后不能再次提交同一任务。</span>
            <Button size="sm" variant="danger" disabled={pending} onClick={() => void run(() => cancelImportJob(jobId))}>确认取消</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>保留任务</Button>
          </div> : <Button size="sm" variant="ghost" onClick={() => setConfirming('cancel')}>取消任务</Button>}
        </div> : null}

        {job.status === 'publish_failed' ? <div className="mt-4 max-w-2xl">
          <Badge tone="danger">发布失败</Badge>
          <p className="mt-2 text-sm">发布失败，已发布快照保持不变；旧快照仍是对外的成本事实来源。</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" disabled>重试发布</Button>
            <span className="meta">由 Worker 在租约过期后自动重试（已尝试 {job.attempt_count} 次）。</span>
          </div>
        </div> : null}

        {job.status === 'published' && job.published_batch_id ? <p className="mt-3 text-sm">已发布批次：<Link className="underline" to={`/data-admin/${encodeURIComponent(job.published_batch_id)}?period=${period}`}>{job.published_batch_id}</Link></p> : null}
      </section>

      {needsErrors ? <section>
        <div className="panel-head"><div><h2 className="section-title">校验错误明细</h2><p className="meta mt-1">{job.job_id} · 不含原始载荷</p></div><Badge tone="warning">{formatNumber(errorTotal)} 条错误</Badge></div>
        <div className="flex flex-wrap gap-2 border-b border-[var(--border-soft)] px-4 py-3">
          {Object.entries(job.error_summary ?? {}).map(([code, count]) => <Badge key={code} tone="warning">{code} {count}</Badge>)}
          {job.error_codes.map((item) => <Badge key={`code-${item.error_code}`} tone="danger">{item.error_code} {item.count}</Badge>)}
        </div>
        <div className="table-scroll"><table className="data-table" aria-label="导入任务错误列表"><thead><tr><th>错误码</th><th>来源表</th><th>来源记录</th><th>错误说明</th><th>时间</th></tr></thead>
          <tbody>{errors.map((item) => <tr key={item.error_id}><td className="num">{item.error_code}</td><td>{item.source_table}</td><td className="num">{item.source_record_id ?? '—'}</td><td>{item.error_message}</td><td className="num">{formatDateTime(item.created_at)}</td></tr>)}</tbody></table></div>
        <div className="flex min-h-14 items-center justify-end gap-3 border-t border-[var(--border-soft)] px-4">
          <Button size="sm" disabled={errorPage <= 1} onClick={() => updateErrorPage(errorPage - 1)}>上一页</Button>
          <span className="meta">错误第 {errorPage} 页</span>
          <Button size="sm" disabled={errorPage * 20 >= errorTotal} onClick={() => updateErrorPage(errorPage + 1)}>下一页</Button>
        </div>
      </section> : null}

      {job.decision ? <section className="border-t border-[var(--border-soft)] px-4 py-4" aria-label="审批结论">
        <h2 className="section-title">审批结论</h2>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm mobile-stack">
          <div><dt className="label">结论</dt><dd>{DECISION_LABELS[job.decision]}</dd></div>
          <div><dt className="label">审批主体</dt><dd className="num">{job.decided_by ?? '—'}</dd></div>
          <div><dt className="label">审批时间</dt><dd className="num">{formatDateTime(job.decided_at)}</dd></div>
          {job.decision_reason ? <div><dt className="label">原因</dt><dd>{job.decision_reason}</dd></div> : null}
        </dl>
      </section> : null}
    </> : null}
  </div>
}

function statusDetail(job: ImportJobDetail): string {
  switch (job.status) {
    case 'submitted': return '任务已提交，等待 Worker 接管校验。'
    case 'validating': return 'Worker 正在校验载荷，校验完成后进入审批。'
    case 'validation_failed': return '校验未通过，下列错误明细可逐页查看。'
    case 'approval_pending': return '校验通过，等待具备 data_import.approve 权限的主体审批。'
    case 'approved': return '审批已通过，等待 Worker 发布。'
    case 'rejected': return '审批被拒绝，任务不会发布。'
    case 'publishing': return 'Worker 正在发布，发布完成后生成新的成本快照。'
    case 'published': return '已发布，新的成本快照已生效。'
    case 'publish_failed': return '发布失败，已发布快照保持不变。'
    case 'cancelled': return '任务已由提交主体或管理员取消。'
  }
}
