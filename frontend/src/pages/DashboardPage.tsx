import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, Database, RefreshCw } from 'lucide-react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  getCostOverview,
  listFinishedBatchCosts,
  type CostOverview,
  type FinishedBatchCostSummary,
} from '../api/costData'
import type { AppOutletContext } from '../components/AppShell'
import { axis, EChart, tooltip } from '../components/Charts'
import { Badge, Button, PageHeader, RequestState } from '../components/ui'
import { formatDecimal, formatPreciseMoney } from '../lib/utils'

export function DashboardPage() {
  const { period } = useOutletContext<AppOutletContext>()
  const navigate = useNavigate()
  const [overview, setOverview] = useState<CostOverview | null>(null)
  const [batches, setBatches] = useState<FinishedBatchCostSummary[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    Promise.all([
      getCostOverview(period, controller.signal),
      listFinishedBatchCosts({ period, sort: 'unit_cost_desc', pageSize: 8, signal: controller.signal }),
    ]).then(([summary, batchList]) => {
      setOverview(summary)
      setBatches(batchList.items)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取成本总览失败')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [period, reload])

  const ranking = useCallback((palette: Parameters<typeof axis>[0]) => ({
    animationDuration: 180,
    grid: { left: 112, right: 48, top: 12, bottom: 28 },
    tooltip: { ...tooltip(palette), formatter: '{b}<br/>单位成本2：¥ {c}' },
    xAxis: { type: 'value' as const, ...axis(palette), axisLabel: { color: palette.muted, fontFamily: 'IBM Plex Mono', fontSize: 10 } },
    yAxis: {
      type: 'category' as const,
      data: batches.map((item) => item.lot_number ?? '—').reverse(),
      ...axis(palette),
      splitLine: { show: false },
    },
    series: [{
      type: 'bar' as const,
      data: batches.map((item) => Number(item.variable_fixed_view.total_cost_2.unit_cost)).reverse(),
      barWidth: 18,
      itemStyle: { color: palette.accent },
    }],
  }), [batches])

  const metrics = overview ? [
    { label: '制造成本', value: formatPreciseMoney(overview.manufacturing_cost, 0), note: `${overview.batch_count} 个完工批次` },
    { label: '制造后费用', value: formatPreciseMoney(overview.post_manufacturing_cost, 0), note: '售后、运输与仓储' },
    { label: '含制造后总成本', value: formatPreciseMoney(overview.total_cost, 0), note: `${overview.part_count} 个产成品零件` },
    { label: '完工数量', value: formatDecimal(overview.completed_quantity, 4), note: `合格 ${formatDecimal(overview.qualified_quantity, 4)}` },
    { label: '不良品数量', value: formatDecimal(overview.defective_quantity, 4), note: '成本由合格品承接' },
    { label: '合格率', value: formatRate(overview.quality_rate), note: `${overview.period} · 已发布数据` },
  ] : []

  return <div>
    <PageHeader
      title={`${period} 成本运行全貌`}
      actions={<>
        <Button variant="ghost" onClick={() => setReload((value) => value + 1)}><RefreshCw className="h-4 w-4" />刷新</Button>
        <Button variant="primary" onClick={() => navigate(`/ai?period=${period}`)}>发起 AI 分析<ArrowRight className="h-4 w-4" /></Button>
      </>}
    />
    <RequestState loading={loading} error={error} empty={!overview} onRetry={() => setReload((value) => value + 1)}>
      {overview ? <>
        <section className="grid grid-cols-3 border-b border-[var(--border-soft)] mobile-stack" aria-label="成本与质量指标">
          {metrics.map((metric) => <article key={metric.label} className="min-h-28 border-r border-b border-[var(--border-soft)] p-4">
            <p className="label">{metric.label}</p>
            <p className="num mt-3 text-2xl">{metric.value}</p>
            <p className="mt-2 text-xs text-[var(--muted)]">{metric.note}</p>
          </article>)}
        </section>
        <section className="grid grid-cols-[minmax(0,1fr)_minmax(440px,.8fr)] border-b border-[var(--border-soft)] mobile-stack">
          <article className="min-w-0 border-r border-[var(--border-soft)]">
            <div className="panel-head">
              <div><h2 className="section-title">高单位成本批次</h2><p className="meta mt-1">按合计单位成本2降序</p></div>
              <Badge tone="info">{batches.length} 个批次</Badge>
            </div>
            <div className="p-4"><EChart build={ranking} ariaLabel={`${period} 高单位成本批次`} /></div>
          </article>
          <article className="min-w-0">
            <div className="panel-head"><div><h2 className="section-title">批次成本摘要</h2><p className="meta mt-1">服务端汇总结果</p></div></div>
            <div className="divide-y divide-[var(--border-soft)]">
              {batches.slice(0, 6).map((item) => <button
                key={item.finished_batch_id}
                className="grid min-h-16 w-full grid-cols-[1fr_120px_100px] items-center gap-3 px-4 text-left hover:bg-[var(--surface)]"
                onClick={() => navigate(`/cost-data/${encodeURIComponent(item.finished_batch_id)}?period=${period}&tab=summary`)}
              >
                <span className="min-w-0">
                  <strong className="block truncate font-semibold">{item.part.part_description}</strong>
                  <span className="meta">{item.part.part_number} · {item.lot_number}</span>
                </span>
                <span className="num text-right">{formatPreciseMoney(item.variable_fixed_view.total_cost_2.amount, 0)}</span>
                <span className="num text-right text-[var(--fg-2)]">{formatPreciseMoney(item.variable_fixed_view.total_cost_2.unit_cost)}</span>
              </button>)}
            </div>
          </article>
        </section>
        <footer className="flex min-h-14 items-center gap-2 px-4 text-xs text-[var(--fg-2)]"><Database className="h-4 w-4" />数据来自当前已发布 PostgreSQL 成本快照</footer>
      </> : null}
    </RequestState>
  </div>
}

function formatRate(value: string) {
  return `${formatDecimal(value, 2)}%`
}
