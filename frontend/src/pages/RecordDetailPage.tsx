import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Database } from 'lucide-react'
import { useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom'
import { getProductCost, type ProductPeriodDetail } from '../api/costData'
import type { AppOutletContext } from '../components/AppShell'
import { axis, EChart, tooltip } from '../components/Charts'
import { CostTree } from '../components/CostTree'
import { Badge, Button, PageHeader, RequestState, Segmented } from '../components/ui'
import { formatDecimal, formatPercent, formatPreciseMoney } from '../lib/utils'

type DetailTab = 'overview' | 'tree' | 'sources'

export function RecordDetailPage() {
  const { productId = '' } = useParams()
  const { period } = useOutletContext<AppOutletContext>()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const tab = (params.get('tab') ?? 'overview') as DetailTab
  const [detail, setDetail] = useState<ProductPeriodDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    void getProductCost(productId, period, controller.signal).then(setDetail).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取产品成本详情失败')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [period, productId, reload])

  const processChart = useCallback((palette: Parameters<typeof axis>[0]) => ({
    animationDuration: 180,
    grid: { left: 72, right: 24, top: 20, bottom: 34 },
    tooltip: tooltip(palette),
    xAxis: { type: 'category' as const, data: detail?.processes.map((item) => item.process_name) ?? [], ...axis(palette), splitLine: { show: false } },
    yAxis: { type: 'value' as const, ...axis(palette), axisLabel: { color: palette.muted, fontFamily: 'IBM Plex Mono', fontSize: 10 } },
    series: [{ type: 'bar' as const, data: detail?.processes.map((item) => Number(item.total_cost)) ?? [], itemStyle: { color: palette.accent }, barMaxWidth: 32 }],
  }), [detail])

  function setTab(nextTab: DetailTab) {
    const next = new URLSearchParams(params); next.set('tab', nextTab); setParams(next, { replace: true })
  }

  return <div>
    <PageHeader eyebrow={`成本数据 / ${productId}`} title={detail?.product.product_name ?? '产品成本详情'} description={detail ? `${detail.product.spec || '无规格'} · ${period} · 已发布数据` : `正在读取 ${period} 成本数据`} actions={<Button variant="ghost" onClick={() => navigate(`/cost-data?period=${period}`)}><ArrowLeft className="h-4 w-4" />返回列表</Button>} />
    <RequestState loading={loading} error={error} empty={!detail} onRetry={() => setReload((value) => value + 1)}>
      {detail ? <>
        <section className="grid grid-cols-4 border-b border-[var(--border-soft)] mobile-stack"><Metric label="总成本" value={formatPreciseMoney(detail.total_cost, 0)} /><Metric label="单位成本" value={formatPreciseMoney(detail.unit_cost)} /><Metric label="产量" value={formatDecimal(detail.output_qty)} /><Metric label="单位成本环比" value={detail.comparison ? formatPercent(detail.comparison.unit_cost_delta_rate) : '—'} /></section>
        <div className="flex min-h-14 items-center border-b border-[var(--border-soft)] px-4"><Segmented value={tab} onChange={setTab} label="详情视图" options={[{ value: 'overview', label: '工序成本' }, { value: 'tree', label: '关系树' }, { value: 'sources', label: '来源摘要' }]} /></div>
        {tab === 'overview' ? <section><div className="grid grid-cols-[minmax(0,1fr)_minmax(440px,.9fr)] border-b border-[var(--border-soft)] mobile-stack"><article className="min-w-0 border-r border-[var(--border-soft)]"><div className="panel-head"><div><h2 className="section-title">工序成本</h2><p className="meta mt-1">服务端归集结果</p></div></div><div className="p-4"><EChart build={processChart} ariaLabel={`${detail.product.product_name} 工序成本`} /></div></article><article className="min-w-0"><div className="panel-head"><h2 className="section-title">工序明细</h2><Badge tone="info">{detail.processes.length} 道工序</Badge></div><div className="table-scroll"><table className="data-table"><thead><tr><th>工序</th><th className="text-right">成本项</th><th className="text-right">来源记录</th><th className="text-right">总成本</th></tr></thead><tbody>{detail.processes.map((process) => <tr key={process.process_code}><td><strong>{process.process_name}</strong><span className="ml-2 meta">{process.process_code}</span></td><td className="num text-right">{process.items.length}</td><td className="num text-right">{process.items.reduce((total, item) => total + item.source_record_count, 0)}</td><td className="num text-right">{formatPreciseMoney(process.total_cost, 0)}</td></tr>)}</tbody></table></div></article></div></section> : null}
        {tab === 'tree' ? <CostTree detail={detail} /> : null}
        {tab === 'sources' ? <SourceSummary detail={detail} /> : null}
      </> : null}
    </RequestState>
  </div>
}

function Metric({ label, value }: { label: string; value: string }) { return <article className="min-h-28 border-r border-[var(--border-soft)] p-4 last:border-r-0"><p className="label">{label}</p><p className="num mt-4 text-2xl">{value}</p></article> }

function SourceSummary({ detail }: { detail: ProductPeriodDetail }) {
  const source = detail.source_summary
  return <section className="grid min-h-[460px] grid-cols-[minmax(0,1fr)_360px] mobile-stack"><div className="border-r border-[var(--border-soft)]"><div className="panel-head"><div><h2 className="section-title">成本项来源记录</h2><p className="meta mt-1">已发布记录汇总</p></div></div><div className="table-scroll"><table className="data-table"><thead><tr><th>工序</th><th>成本项</th><th className="text-right">金额</th><th className="text-right">来源记录</th></tr></thead><tbody>{detail.processes.flatMap((process) => process.items.map((item) => <tr key={`${process.process_code}-${item.cost_item}`}><td>{process.process_name}</td><td>{item.label}</td><td className="num text-right">{formatPreciseMoney(item.amount, 0)}</td><td className="num text-right">{item.source_record_count}</td></tr>))}</tbody></table></div></div><aside className="bg-[var(--surface)] p-4"><div className="flex items-center gap-2"><Database className="h-4 w-4" /><h2 className="font-semibold">来源摘要</h2></div><dl className="mt-5 grid grid-cols-[148px_1fr] gap-y-3 text-sm"><dt className="text-[var(--muted)]">覆盖日期</dt><dd className="font-mono">{source.start_date} 至 {source.end_date}</dd><dt className="text-[var(--muted)]">生产产量记录</dt><dd className="num">{source.production_output_count}</dd><dt className="text-[var(--muted)]">工序成本记录</dt><dd className="num">{source.process_cost_entry_count}</dd><dt className="text-[var(--muted)]">来源系统</dt><dd>{source.source_systems.join('、') || '—'}</dd></dl></aside></section>
}
