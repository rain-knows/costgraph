import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, FileBarChart2, GitBranch, RotateCcw, Search, Trash2, X } from 'lucide-react'
import { useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom'
import { getArtifact, listArtifacts, restoreArtifact, trashArtifact, type ArtifactDetail, type ArtifactSummary } from '../api/artifacts'
import type { AppOutletContext } from '../components/AppShell'
import { axis, EChart, tooltip } from '../components/Charts'
import { CostViewSummary } from '../components/CostViews'
import { Badge, Button, IconButton, PageHeader, RequestState, Segmented } from '../components/ui'
import { formatDecimal, formatPreciseMoney } from '../lib/utils'

export function ReportsPage() {
  const { artifactId } = useParams()
  return artifactId ? <ReportDetail artifactId={artifactId} /> : <ReportList />
}

function ReportList() {
  const { period } = useOutletContext<AppOutletContext>()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const scope = (params.get('scope') ?? 'active') as 'active' | 'trashed'
  const query = params.get('q') ?? ''
  const runId = params.get('run') ?? undefined
  const page = readPage(params.get('page'))
  const [items, setItems] = useState<ArtifactSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<ArtifactSummary | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const pageSize = 20

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setLoading(true)
      setError('')
      void listArtifacts({
        state: scope,
        query,
        period,
        runId,
        limit: pageSize,
        offset: (page - 1) * pageSize,
        signal: controller.signal,
      }).then((response) => {
        setItems(response.items)
        setTotal(response.total)
      }).catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取报表失败')
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    }, 180)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [page, period, query, reload, runId, scope])

  function update(key: string, value?: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.set('page', '1')
    setParams(next, { replace: key !== 'page' })
  }

  async function confirmTrash() {
    if (!deleteTarget) return
    setActionLoading(true)
    setError('')
    try {
      await trashArtifact(deleteTarget.artifact_id)
      setDeleteTarget(null)
      setNotice('报表已移入回收站。')
      setReload((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '移除报表失败')
    } finally {
      setActionLoading(false)
    }
  }

  async function restore(item: ArtifactSummary) {
    setActionLoading(true)
    setError('')
    try {
      await restoreArtifact(item.artifact_id)
      setNotice('报表已恢复。')
      setReload((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '恢复报表失败')
    } finally {
      setActionLoading(false)
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  return <div>
    <PageHeader eyebrow="报表中心 / 结构化产出" title="成本报表中心" description="管理由成本 Agent 生成的批次卷积 Artifact 2.0。" />
    <section className="flex min-h-14 flex-wrap items-center gap-2 border-b border-[var(--border-soft)] px-4 py-2">
      <Segmented value={scope} onChange={(value) => update('scope', value === 'active' ? undefined : value)} label="报表范围" options={[{ value: 'active', label: '当前报表' }, { value: 'trashed', label: '回收站' }]} />
      <label className="flex h-10 min-w-56 flex-1 items-center gap-2 bg-[var(--surface)] px-3 md:max-w-sm">
        <Search className="h-4 w-4" /><span className="sr-only">搜索报表</span>
        <input value={query} onChange={(event) => update('q', event.target.value)} placeholder="搜索零件或会话" className="min-w-0 flex-1 bg-transparent" />
      </label>
      {runId ? <button className="flex h-8 items-center gap-1 bg-[var(--surface-warm)] px-2 text-xs" onClick={() => update('run')}><span>Run: {runId.slice(0, 12)}</span><X className="h-3.5 w-3.5" /></button> : null}
      <span className="ml-auto meta">{total} 份报表 · {period}</span>
    </section>
    {notice ? <div className="border-b border-[var(--success)] px-4 py-2 text-xs text-[var(--success)]" role="status">{notice}</div> : null}
    <RequestState loading={loading && !items.length} error={error} empty={!loading && !error && !items.length} onRetry={() => setReload((value) => value + 1)}>
      <section className="table-scroll min-h-[520px]">
        <table className="data-table"><thead><tr><th>报表</th><th>期间</th><th>来源会话</th><th>数据快照</th><th>创建时间</th><th aria-label="操作" /></tr></thead>
          <tbody>{items.map((item) => <tr key={item.artifact_id}>
            <td><button disabled={scope === 'trashed'} onClick={() => navigate(`/reports/${encodeURIComponent(item.artifact_id)}?period=${period}`)} className="text-left disabled:opacity-100">
              <span className="flex items-center gap-2 font-semibold hover:text-[var(--accent)]"><FileBarChart2 className="h-4 w-4" />{item.part_description} 成本报表</span>
              <span className="meta mt-1 block">{item.part_number} · {item.artifact_id}</span>
            </button></td>
            <td className="font-mono">{item.period}</td><td>{item.conversation_title}</td>
            <td className="max-w-44 truncate font-mono text-xs" title={item.data_snapshot_id}>{item.data_snapshot_id}</td>
            <td className="meta">{formatDate(item.deleted_at ?? item.created_at)}</td>
            <td>{scope === 'trashed'
              ? <IconButton label={`恢复 ${item.part_description} 报表`} className="h-8 w-8" disabled={actionLoading} onClick={() => void restore(item)}><RotateCcw className="h-4 w-4" /></IconButton>
              : <IconButton label={`将 ${item.part_description} 报表移入回收站`} className="h-8 w-8" disabled={actionLoading} onClick={() => setDeleteTarget(item)}><Trash2 className="h-4 w-4" /></IconButton>}
            </td>
          </tr>)}</tbody>
        </table>
      </section>
    </RequestState>
    <footer className="flex min-h-14 items-center justify-between border-t border-[var(--border)] px-4">
      <span className="text-sm text-[var(--fg-2)]">第 {page} / {pageCount} 页</span>
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" disabled={page <= 1 || loading} onClick={() => update('page', String(page - 1))}>上一页</Button>
        <Button size="sm" variant="ghost" disabled={page >= pageCount || loading} onClick={() => update('page', String(page + 1))}>下一页</Button>
      </div>
    </footer>
    {deleteTarget ? <ConfirmDialog busy={actionLoading} item={deleteTarget} onCancel={() => setDeleteTarget(null)} onConfirm={() => void confirmTrash()} /> : null}
  </div>
}

function ReportDetail({ artifactId }: { artifactId: string }) {
  const { period } = useOutletContext<AppOutletContext>()
  const navigate = useNavigate()
  const [artifact, setArtifact] = useState<ArtifactDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    void getArtifact(artifactId).then((value) => {
      if (!controller.signal.aborted) setArtifact(value)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取报表失败')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [artifactId, reload])

  const report = artifact?.report_json
  const batchChart = useCallback((palette: Parameters<typeof axis>[0]) => ({
    animationDuration: 180,
    color: [palette.accent, palette.fg, palette.success],
    grid: { left: 60, right: 20, top: 36, bottom: 42 },
    tooltip: tooltip(palette),
    legend: { top: 0, right: 4, textStyle: { color: palette.muted, fontSize: 10 }, data: ['料', '工', '费'] },
    xAxis: {
      type: 'category' as const,
      data: report?.finished_batches.map((item) => item.lot_number ?? '—') ?? [],
      ...axis(palette),
      splitLine: { show: false },
      axisLabel: { color: palette.muted, fontFamily: 'IBM Plex Mono', fontSize: 10, rotate: 20 },
    },
    yAxis: { type: 'value' as const, ...axis(palette), axisLabel: { color: palette.muted, fontFamily: 'IBM Plex Mono', fontSize: 10 } },
    series: [
      { name: '料', key: 'material' as const },
      { name: '工', key: 'labor' as const },
      { name: '费', key: 'overhead' as const },
    ].map(({ name, key }) => ({
      name,
      type: 'bar' as const,
      stack: 'cost',
      barMaxWidth: 36,
      data: report?.finished_batches.map((item) => Number(item.material_labor_overhead_view[key].unit_cost)) ?? [],
    })),
  }), [report])

  async function remove() {
    setActionLoading(true)
    try {
      await trashArtifact(artifactId)
      navigate(`/reports?period=${period}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '移除报表失败')
      setConfirmOpen(false)
    } finally {
      setActionLoading(false)
    }
  }

  return <div>
    <PageHeader
      eyebrow={`报表中心 / ${artifactId}`}
      title={report ? `${report.part.part_description} 成本报表` : '成本报表'}
      description={report ? `${report.part.part_number} · ${report.period} · 快照 ${report.data_snapshot_id.slice(0, 12)}` : '正在读取结构化 Artifact'}
      actions={<>
        <Button variant="ghost" onClick={() => navigate(`/reports?period=${period}`)}><ArrowLeft className="h-4 w-4" />返回报表中心</Button>
        {artifact && !artifact.deleted_at ? <Button variant="danger" disabled={actionLoading} onClick={() => setConfirmOpen(true)}><Trash2 className="h-4 w-4" />移入回收站</Button> : null}
      </>}
    />
    <RequestState loading={loading} error={error} empty={!artifact} onRetry={() => setReload((value) => value + 1)}>
      {report ? <>
        <section className="grid grid-cols-5 border-b border-[var(--border-soft)] mobile-stack" aria-label="报表批次指标">
          <ReportMetric label="批次数" value={String(report.batch_summary.batch_count)} />
          <ReportMetric label="完工数量" value={formatDecimal(report.batch_summary.completed_quantity, 4)} />
          <ReportMetric label="合格数量" value={formatDecimal(report.batch_summary.qualified_quantity, 4)} />
          <ReportMetric label="不良数量" value={formatDecimal(report.batch_summary.defective_quantity, 4)} />
          <ReportMetric label="合格率" value={formatRate(report.batch_summary.quality_rate)} />
        </section>
        <section className="grid grid-cols-[minmax(0,1fr)_minmax(360px,.7fr)] border-b border-[var(--border-soft)] mobile-stack">
          <article className="min-w-0 border-r border-[var(--border-soft)]">
            <div className="panel-head"><div><h2 className="section-title">批次料工费构成</h2><p className="meta mt-1">各批次服务端单位成本</p></div></div>
            <div className="p-4"><EChart build={batchChart} ariaLabel={`${report.part.part_description} 批次料工费构成`} /></div>
          </article>
          <article className="p-5">
            <div className="flex items-center gap-2"><GitBranch className="h-4 w-4" /><h2 className="section-title">分析结论</h2></div>
            <p className="mt-4 whitespace-pre-wrap text-sm leading-7">{report.analysis_text}</p>
            {report.insight_cards?.map((insight) => <div key={insight.label} className="mt-4 border-l-2 border-[var(--accent)] pl-3">
              <div className="flex items-center justify-between gap-3"><strong>{insight.label}</strong><span className="num">{insight.value}</span></div>
              <p className="mt-1 text-xs leading-5 text-[var(--fg-2)]">{insight.description}</p>
            </div>)}
          </article>
        </section>
        <CostViewSummary
          manufacturing={report.manufacturing_view}
          materialLaborOverhead={report.material_labor_overhead_view}
          variableFixed={report.variable_fixed_view}
        />
        <section className="grid grid-cols-[minmax(0,1fr)_360px] border-t border-[var(--border-soft)] mobile-stack">
          <article className="min-w-0 border-r border-[var(--border-soft)]">
            <div className="panel-head"><div><h2 className="section-title">产成品批次</h2><p className="meta mt-1">进入批次可查看完整成本 DAG</p></div><Badge>{report.finished_batches.length} 个</Badge></div>
            <div className="table-scroll"><table className="data-table"><thead><tr><th>批号</th><th>完工时间</th><th>成本中心</th><th className="text-right">合格数量</th><th className="text-right">合格率</th><th className="text-right">单位成本1</th><th className="text-right">单位成本2</th></tr></thead>
              <tbody>{report.finished_batches.map((item) => <tr key={item.finished_batch_id}>
                <td><button className="font-mono text-[var(--accent)] hover:underline" onClick={() => navigate(`/cost-data/${encodeURIComponent(item.finished_batch_id)}?period=${report.period}&tab=summary`)}>{item.lot_number}</button></td>
                <td className="meta">{formatDate(item.completion_time)}</td>
                <td>{item.cost_center_name ?? item.cost_center_code ?? '—'}</td>
                <td className="num text-right">{formatDecimal(item.qualified_quantity, 4)}</td>
                <td className="num text-right">{formatRate(item.quality_rate)}</td>
                <td className="num text-right">{formatPreciseMoney(item.manufacturing_view.total.unit_cost)}</td>
                <td className="num text-right">{formatPreciseMoney(item.variable_fixed_view.total_cost_2.unit_cost)}</td>
              </tr>)}</tbody>
            </table></div>
          </article>
          <aside className="bg-[var(--surface)] p-5">
            <p className="label">可审计元数据</p>
            <dl className="mt-4 grid grid-cols-[112px_1fr] gap-y-3 text-xs">
              <dt className="text-[var(--muted)]">Schema</dt><dd className="font-mono">{report.report_schema_version}</dd>
              <dt className="text-[var(--muted)]">来源</dt><dd>{report.source_summary}</dd>
              <dt className="text-[var(--muted)]">来源记录</dt><dd className="num">{report.lineage.tables.reduce((sum, item) => sum + item.record_count, 0)}</dd>
              <dt className="text-[var(--muted)]">规则版本</dt><dd className="break-all font-mono">{report.rule_version}</dd>
              <dt className="text-[var(--muted)]">Prompt 版本</dt><dd className="break-all font-mono">{report.prompt_version}</dd>
              <dt className="text-[var(--muted)]">代码版本</dt><dd className="break-all font-mono">{report.code_version}</dd>
              <dt className="text-[var(--muted)]">Run ID</dt><dd className="break-all font-mono">{report.run_id}</dd>
            </dl>
          </aside>
        </section>
      </> : null}
    </RequestState>
    {confirmOpen && artifact ? <ConfirmDialog busy={actionLoading} item={artifact} onCancel={() => setConfirmOpen(false)} onConfirm={() => void remove()} /> : null}
  </div>
}

function ReportMetric({ label, value }: { label: string; value: string }) {
  return <article className="min-h-28 border-r border-[var(--border-soft)] p-4 last:border-r-0"><p className="label">{label}</p><p className="num mt-4 text-2xl">{value}</p></article>
}

function ConfirmDialog({ item, busy, onCancel, onConfirm }: { item: ArtifactSummary; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  return <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 px-4" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
    <section className="w-full max-w-md bg-[var(--bg)] shadow-[var(--elev-raised)]">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-3"><h2 id="confirm-title" className="font-semibold">将报表移入回收站？</h2><IconButton label="关闭" className="h-10 w-10" onClick={onCancel}><X className="h-4 w-4" /></IconButton></div>
      <p className="p-4 text-sm leading-6 text-[var(--fg-2)]">{item.part_description} {item.period} 报表将从当前列表隐藏，但原会话和运行证据不受影响，可从回收站恢复。</p>
      <div className="flex justify-end gap-2 border-t border-[var(--border)] p-3"><Button variant="ghost" disabled={busy} onClick={onCancel}>取消</Button><Button variant="danger" disabled={busy} onClick={onConfirm}><Trash2 className="h-4 w-4" />{busy ? '正在移除' : '移入回收站'}</Button></div>
    </section>
  </div>
}

function readPage(value: string | null) {
  const page = Number(value)
  return Number.isInteger(page) && page > 0 ? page : 1
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function formatRate(value: string) {
  return `${formatDecimal(value, 2)}%`
}
