import { useEffect, useState } from 'react'
import { useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom'
import { ApiError } from '../api/http'
import { getDataAdminBatch, getDataQuality, getPublishedSnapshot, listDataAdminBatches, listDataAdminErrors, type DataAdminBatchDetail, type DataAdminError, type DataAdminBatch, type DataQualitySummary, type PublishedSnapshot } from '../api/dataAdmin'
import type { AppOutletContext } from '../components/AppShell'
import { Badge, Button, PageHeader } from '../components/ui'
import { formatDateTime } from '../lib/utils'

type LoadState = 'loading' | 'ready' | 'empty' | 'error' | 'unavailable' | 'unauthorized' | 'not_found'

export function DataAdminPage() {
  const { period, me, meLoading, meError } = useOutletContext<AppOutletContext>()
  const { batchId } = useParams()
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()
  const page = Math.max(1, Number(search.get('page') || 1))
  const errorPage = Math.max(1, Number(search.get('error_page') || 1))
  const [state, setState] = useState<LoadState>('loading')
  const [message, setMessage] = useState('')
  const [snapshot, setSnapshot] = useState<PublishedSnapshot | null>(null)
  const [quality, setQuality] = useState<DataQualitySummary | null>(null)
  const [batches, setBatches] = useState<DataAdminBatch[]>([])
  const [batch, setBatch] = useState<DataAdminBatchDetail | null>(null)
  const [errors, setErrors] = useState<DataAdminError[]>([])
  const [total, setTotal] = useState(0)
  const [reload, setReload] = useState(0)
  const permitted = me?.permissions.includes('data_management.read') === true

  useEffect(() => {
    if (meLoading) { setState('loading'); return }
    if (meError) { setState('unavailable'); setMessage('无法读取当前主体的权限摘要。'); return }
    if (!permitted) { setState('unauthorized'); return }
    const controller = new AbortController()
    setState('loading')
    setMessage('')
    Promise.all([
      getPublishedSnapshot(controller.signal),
      getDataQuality(controller.signal),
      listDataAdminBatches({ page, pageSize: 20, signal: controller.signal }),
      ...(batchId ? [getDataAdminBatch(batchId, controller.signal), listDataAdminErrors(batchId, { page: errorPage, pageSize: 20, signal: controller.signal })] : []),
    ]).then((values) => {
      setSnapshot(values[0] as PublishedSnapshot)
      setQuality(values[1] as DataQualitySummary)
      const list = values[2] as Awaited<ReturnType<typeof listDataAdminBatches>>
      setBatches(list.items)
      setTotal(list.total)
      if (batchId) {
        setBatch(values[3] as DataAdminBatchDetail)
        setErrors((values[4] as Awaited<ReturnType<typeof listDataAdminErrors>>).items)
      } else { setBatch(null); setErrors([]) }
      setState(list.items.length ? 'ready' : 'empty')
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return
      if (reason instanceof ApiError && (reason.status === 403 || reason.code === 'data_management_access_denied')) setState('unauthorized')
      else if (reason instanceof ApiError && reason.status === 404 && batchId) { setState('not_found'); setMessage('指定批次不存在，或不属于当前租户。') }
      else if (reason instanceof ApiError && reason.status === 503) { setState('unavailable'); setMessage(reason.message) }
      else { setState('error'); setMessage(reason instanceof Error ? reason.message : '读取数据治理信息失败') }
    })
    return () => controller.abort()
  }, [batchId, errorPage, meError, meLoading, page, permitted, reload])

  function updatePage(key: string, value: number) {
    const next = new URLSearchParams(search)
    next.set(key, String(value))
    next.set('period', period)
    setSearch(next)
  }

  const shownState = state === 'ready' && !batches.length ? 'empty' : state
  return <div>
    <PageHeader title="数据治理" actions={<Button variant="ghost" onClick={() => setReload((value) => value + 1)}>刷新</Button>} />
    {shownState === 'unauthorized' ? <StateNotice state="unauthorized" text="当前主体没有 data_management.read 权限。" /> : null}
    {shownState === 'not_found' ? <StateNotice state="not_found" text={message} onRetry={() => navigate(`/data-admin?period=${period}&page=${page}`)} /> : null}
    {shownState === 'unavailable' ? <StateNotice state="unavailable" text={message || '数据治理服务暂不可用。'} onRetry={() => setReload((value) => value + 1)} /> : null}
    {shownState === 'error' ? <StateNotice state="error" text={message} onRetry={() => setReload((value) => value + 1)} /> : null}
    {shownState === 'loading' ? <StateNotice state="loading" text="正在读取当前租户的导入批次和发布快照。" /> : null}
    {shownState === 'empty' ? <StateNotice state="empty" text="当前租户尚无导入批次或发布快照。" /> : null}
    {(shownState === 'ready' || (shownState === 'empty' && snapshot)) ? <>
      <section className="grid grid-cols-2 border-b border-[var(--border-soft)] mobile-stack" aria-label="发布与质量摘要">
        <article className="border-r border-[var(--border-soft)] p-4">
          <p className="label">当前发布快照</p>
          {snapshot?.batch ? <><p className="num mt-2 break-all text-sm">{snapshot.batch.batch_id}</p><p className="mt-2 text-sm">规则 {snapshot.batch.calculation_rule_version} · 发布时间 {formatDateTime(snapshot.batch.finished_at ?? snapshot.batch.created_at)}</p><p className="meta mt-2">{snapshot.period_start ?? '—'} – {snapshot.period_end ?? '—'} · 投影 {snapshot.projection_rows} 行</p></> : <p className="mt-3 text-sm text-[var(--fg-2)]">尚无已发布快照</p>}
        </article>
        <article className="p-4">
          <p className="label">质量摘要</p>
          <div className="mt-3 flex flex-wrap gap-2">{Object.entries(quality?.fact_rows ?? {}).map(([name, count]) => <Badge key={name}>{name} {count}</Badge>)}</div>
          <p className="mt-3 text-sm">错误行 {quality?.error_rows ?? 0} · 零件 {quality?.part_count ?? 0} · 投影 {quality?.projection_rows ?? 0}</p>
          <p className="meta mt-1">规则一致性：{quality?.rule_version_matches == null ? '无发布快照' : quality.rule_version_matches ? '一致' : '不一致'}</p>
        </article>
      </section>
      <section>
        <div className="panel-head"><div><h2 className="section-title">导入批次</h2><p className="meta mt-1">只读批次元数据；原始载荷不在此展示</p></div><Badge tone="info">{total} 个批次</Badge></div>
        <div className="table-scroll"><table className="data-table" aria-label="导入批次列表"><thead><tr><th>状态</th><th>批次 ID</th><th>来源</th><th>快照哈希</th><th>有效 / 错误 / 总行数</th><th>创建时间</th></tr></thead><tbody>{batches.map((item) => <tr key={item.batch_id} tabIndex={0} onClick={() => navigate(`/data-admin/${encodeURIComponent(item.batch_id)}?period=${period}`)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); navigate(`/data-admin/${encodeURIComponent(item.batch_id)}?period=${period}`) } }}><td>{item.status}</td><td className="num">{item.batch_id}</td><td>{item.source_system}<span className="meta ml-2">{item.source_file ?? ''}</span></td><td className="num">{item.source_snapshot_hash}</td><td className="num">{item.valid_rows} / {item.error_rows} / {item.total_rows}</td><td className="num">{formatDateTime(item.created_at)}</td></tr>)}</tbody></table></div>
        <div className="flex min-h-14 items-center justify-end gap-3 border-t border-[var(--border-soft)] px-4"><Button size="sm" disabled={page <= 1} onClick={() => updatePage('page', page - 1)}>上一页</Button><span className="meta">第 {page} 页</span><Button size="sm" disabled={page * 20 >= total} onClick={() => updatePage('page', page + 1)}>下一页</Button></div>
      </section>
      {batchId ? <section className="border-t border-[var(--border)]">
        <div className="panel-head"><div><h2 className="section-title">批次错误明细</h2><p className="meta mt-1">{batch?.batch_id ?? batchId} · 不含原始载荷</p></div><Button size="sm" onClick={() => navigate(`/data-admin?period=${period}&page=${page}`)}>关闭详情</Button></div>
        <div className="flex flex-wrap gap-2 border-b border-[var(--border-soft)] px-4 py-3">{batch?.error_codes.map((item) => <Badge key={item.error_code} tone="warning">{item.error_code} {item.count}</Badge>)}</div>
        <div className="table-scroll"><table className="data-table" aria-label="批次错误列表"><thead><tr><th>错误码</th><th>来源表</th><th>来源记录</th><th>错误说明</th><th>时间</th></tr></thead><tbody>{errors.map((item) => <tr key={item.error_id}><td className="num">{item.error_code}</td><td>{item.source_table}</td><td className="num">{item.source_record_id ?? '—'}</td><td>{item.error_message}</td><td className="num">{formatDateTime(item.created_at)}</td></tr>)}</tbody></table></div>
        <div className="flex min-h-14 items-center justify-end gap-3 border-t border-[var(--border-soft)] px-4"><Button size="sm" disabled={errorPage <= 1} onClick={() => updatePage('error_page', errorPage - 1)}>上一页</Button><span className="meta">错误第 {errorPage} 页</span><Button size="sm" disabled={!batch || errorPage * 20 >= batch.error_rows} onClick={() => updatePage('error_page', errorPage + 1)}>下一页</Button></div>
      </section> : null}
    </> : null}
  </div>
}

function StateNotice({ state, text, onRetry }: { state: LoadState; text: string; onRetry?: () => void }) {
  const role = state === 'error' || state === 'unauthorized' || state === 'unavailable' || state === 'not_found' ? 'alert' : 'status'
  return <section className="grid min-h-64 place-items-center border-b border-[var(--border-soft)] px-6" data-state={state} role={role}><div className="max-w-lg text-center"><h2 className="text-xl">{state === 'unauthorized' ? '无权访问数据治理' : state === 'not_found' ? '批次不存在' : state === 'unavailable' ? '数据治理服务暂不可用' : state === 'error' ? '数据读取失败' : state === 'loading' ? '正在读取数据治理信息' : '暂无导入数据'}</h2><p className="mt-2 text-sm text-[var(--fg-2)]">{text}</p>{onRetry ? <Button className="mt-4" onClick={onRetry}>{state === 'not_found' ? '返回批次列表' : '重新读取'}</Button> : null}</div></section>
}
