import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, Database, RefreshCw } from 'lucide-react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { getCostOverview, listProductCosts, type CostOverview, type ProductPeriodSummary } from '../api/costData'
import type { AppOutletContext } from '../components/AppShell'
import { axis, EChart, tooltip } from '../components/Charts'
import { Badge, Button, PageHeader, RequestState } from '../components/ui'
import { formatDecimal, formatPercent, formatPreciseMoney } from '../lib/utils'

export function DashboardPage() {
  const { period } = useOutletContext<AppOutletContext>()
  const navigate = useNavigate()
  const [overview, setOverview] = useState<CostOverview | null>(null)
  const [products, setProducts] = useState<ProductPeriodSummary[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    Promise.all([
      getCostOverview(period, controller.signal),
      listProductCosts({ period, sort: 'total_cost_desc', pageSize: 8, signal: controller.signal }),
    ]).then(([summary, productList]) => {
      setOverview(summary); setProducts(productList.items)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取成本总览失败')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [period, reload])

  const ranking = useCallback((palette: Parameters<typeof axis>[0]) => ({
    animationDuration: 180,
    grid: { left: 90, right: 48, top: 12, bottom: 28 },
    tooltip: { ...tooltip(palette), formatter: '{b}<br/>总成本：¥ {c}' },
    xAxis: { type: 'value' as const, ...axis(palette), axisLabel: { color: palette.muted, fontFamily: 'IBM Plex Mono', fontSize: 10 } },
    yAxis: { type: 'category' as const, data: products.map((item) => item.product_name).reverse(), ...axis(palette), splitLine: { show: false } },
    series: [{ type: 'bar' as const, data: products.map((item) => Number(item.total_cost)).reverse(), barWidth: 18, itemStyle: { color: palette.accent } }],
  }), [products])

  const metrics = overview ? [
    { label: '期间总成本', value: formatPreciseMoney(overview.total_cost, 0), note: `${overview.period} · 已发布数据` },
    { label: '平均单位成本', value: overview.average_unit_cost === null ? '—' : formatPreciseMoney(overview.average_unit_cost), note: overview.comparison ? `较 ${overview.comparison.previous_period} ${formatPercent(overview.comparison.unit_cost_delta_rate)}` : '暂无上期可比数据' },
    { label: '总产量', value: formatDecimal(overview.total_output_qty), note: '服务端汇总数量' },
    { label: '产品数量', value: formatDecimal(overview.product_count), note: '当前授权范围' },
  ] : []

  return <div>
    <PageHeader eyebrow="成本总览 / 已发布数据" title={`${period} 成本运行全貌`} description="以服务端发布快照为准；所有金额和比较结果均由确定性成本服务计算。" actions={<><Button variant="ghost" onClick={() => setReload((value) => value + 1)}><RefreshCw className="h-4 w-4" />刷新</Button><Button variant="primary" onClick={() => navigate(`/ai?period=${period}`)}>发起 AI 分析<ArrowRight className="h-4 w-4" /></Button></>} />
    <RequestState loading={loading} error={error} empty={!overview} onRetry={() => setReload((value) => value + 1)}>
      {overview ? <>
        <section className="grid grid-cols-4 border-b border-[var(--border-soft)] mobile-stack" aria-label="成本指标">{metrics.map((metric) => <article key={metric.label} className="min-h-32 border-r border-[var(--border-soft)] p-4 last:border-r-0"><p className="label">{metric.label}</p><p className="num mt-4 text-3xl">{metric.value}</p><p className="mt-2 text-xs text-[var(--muted)]">{metric.note}</p></article>)}</section>
        {overview.comparison ? <section className="grid grid-cols-4 bg-[var(--surface)] mobile-stack" aria-label="上期比较">
          <Comparison label="总成本变化" value={formatPreciseMoney(overview.comparison.total_cost_delta, 0)} rate={formatPercent(overview.comparison.total_cost_delta_rate)} />
          <Comparison label="单位成本变化" value={formatPreciseMoney(overview.comparison.unit_cost_delta)} rate={formatPercent(overview.comparison.unit_cost_delta_rate)} />
          <div className="col-span-2 min-h-24 p-4"><p className="label">比较期间</p><p className="num mt-3 text-xl">{overview.comparison.previous_period} → {overview.period}</p></div>
        </section> : null}
        <section className="grid grid-cols-[minmax(0,1fr)_minmax(420px,.8fr)] border-b border-[var(--border-soft)] mobile-stack">
          <article className="min-w-0 border-r border-[var(--border-soft)]"><div className="panel-head"><div><h2 className="section-title">产品总成本排名</h2><p className="meta mt-1">{period} · 当前授权范围</p></div><Badge tone="info">{products.length} 个产品</Badge></div><div className="p-4"><EChart build={ranking} ariaLabel={`${period} 产品总成本排名`} /></div></article>
          <article className="min-w-0"><div className="panel-head"><div><h2 className="section-title">产品成本摘要</h2><p className="meta mt-1">按总成本降序</p></div></div><div className="divide-y divide-[var(--border-soft)]">{products.slice(0, 6).map((item) => <button key={item.product_id} className="grid min-h-14 w-full grid-cols-[1fr_120px_100px] items-center gap-3 px-4 text-left hover:bg-[var(--surface)]" onClick={() => navigate(`/cost-data/${encodeURIComponent(item.product_id)}?period=${period}`)}><span className="min-w-0"><strong className="block truncate font-semibold">{item.product_name}</strong><span className="meta">{item.product_id}</span></span><span className="num text-right">{formatPreciseMoney(item.total_cost, 0)}</span><span className="num text-right text-[var(--fg-2)]">{formatPreciseMoney(item.unit_cost)}</span></button>)}</div></article>
        </section>
        <footer className="flex min-h-14 items-center gap-2 px-4 text-xs text-[var(--fg-2)]"><Database className="h-4 w-4" />数据来自已发布 PostgreSQL 成本快照</footer>
      </> : null}
    </RequestState>
  </div>
}

function Comparison({ label, value, rate }: { label: string; value: string; rate: string }) {
  const increase = rate.startsWith('+')
  return <article className="min-h-24 border-r border-b border-[var(--border-soft)] p-4"><p className="label">{label}</p><div className="mt-3 flex items-baseline gap-3"><span className="num text-xl">{value}</span><span className={`num text-xs ${increase ? 'text-[var(--danger)]' : 'text-[var(--success)]'}`}>{rate}</span></div></article>
}
