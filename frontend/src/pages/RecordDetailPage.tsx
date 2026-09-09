import { useEffect, useState } from 'react'
import { ArrowLeft, Database, Factory, ShoppingCart } from 'lucide-react'
import { useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom'
import {
  getFinishedBatchCost,
  type CostTraceNode,
  type CostTraceRecord,
  type FinishedBatchCostDetail,
} from '../api/costData'
import type { AppOutletContext } from '../components/AppShell'
import { CostTree } from '../components/CostTree'
import { CostViewSummary } from '../components/CostViews'
import { Badge, Button, PageHeader, RequestState, Segmented } from '../components/ui'
import { formatDecimal, formatPreciseMoney } from '../lib/utils'

const detailTabs = ['summary', 'trace', 'sources'] as const
type DetailTab = (typeof detailTabs)[number]

const detailTabOptions: Array<{ value: DetailTab; label: string }> = [
  { value: 'summary', label: '成本汇总' },
  { value: 'trace', label: '成本追溯' },
  { value: 'sources', label: '来源记录' },
]

export function RecordDetailPage() {
  const { finishedBatchId = '' } = useParams()
  const { period } = useOutletContext<AppOutletContext>()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const tab = readTab(params.get('tab'))
  const listView = params.get('view') ?? 'manufacturing'
  const [detail, setDetail] = useState<FinishedBatchCostDetail | null>(null)
  const [selectedEventId, setSelectedEventId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    void getFinishedBatchCost(finishedBatchId, controller.signal).then((value) => {
      setDetail(value)
      setSelectedEventId(value.trace.root_event_id)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取批次成本详情失败')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [finishedBatchId, reload])

  function setTab(nextTab: DetailTab) {
    const next = new URLSearchParams(params)
    next.set('tab', nextTab)
    setParams(next, { replace: true })
  }

  const title = detail?.part.part_description ?? '批次成本详情'
  const description = detail
    ? `${detail.part.part_number} · 批号 ${detail.lot_number} · ${formatDateTime(detail.completion_time)}`
    : `正在读取批次 ${finishedBatchId}`

  return <div>
    <PageHeader
      eyebrow={`成本数据 / ${finishedBatchId}`}
      title={title}
      description={description}
      actions={<Button variant="ghost" onClick={() => navigate(`/cost-data?period=${period}&view=${encodeURIComponent(listView)}`)}><ArrowLeft className="h-4 w-4" />返回批次列表</Button>}
    />
    <RequestState loading={loading} error={error} empty={!detail} onRetry={() => setReload((value) => value + 1)}>
      {detail ? <>
        <section className="grid grid-cols-5 border-b border-[var(--border-soft)] mobile-stack" aria-label="批次成本指标">
          <Metric label="制造单位成本1" value={formatPreciseMoney(detail.manufacturing_view.total.unit_cost)} />
          <Metric label="合计单位成本2" value={formatPreciseMoney(detail.variable_fixed_view.total_cost_2.unit_cost)} />
          <Metric label="完工数量" value={`${formatDecimal(detail.completed_quantity, 4)} ${detail.unit}`} />
          <Metric label="合格 / 不良" value={`${formatDecimal(detail.qualified_quantity, 4)} / ${formatDecimal(detail.defective_quantity, 4)}`} />
          <Metric label="合格率" value={formatRate(detail.quality_rate)} />
        </section>
        <BatchIdentity detail={detail} />
        <div className="flex min-h-14 items-center overflow-x-auto border-b border-[var(--border-soft)] px-4">
          <Segmented value={tab} onChange={setTab} label="详情视图" options={detailTabOptions} />
        </div>
        {tab === 'summary' ? <CostViewSummary
          manufacturing={detail.manufacturing_view}
          materialLaborOverhead={detail.material_labor_overhead_view}
          variableFixed={detail.variable_fixed_view}
        /> : null}
        {tab === 'trace' ? <TraceWorkspace detail={detail} selectedEventId={selectedEventId} onSelect={setSelectedEventId} /> : null}
        {tab === 'sources' ? <SourceRecords records={detail.trace.records} title="全部来源记录" /> : null}
      </> : null}
    </RequestState>
  </div>
}

function BatchIdentity({ detail }: { detail: FinishedBatchCostDetail }) {
  const fields = [
    ['成本中心', [detail.cost_center_code, detail.cost_center_name].filter(Boolean).join(' · ') || '—'],
    ['车间工单号', detail.work_order_number ?? '—'],
    ['工艺步骤', detail.process_name ?? detail.process_code ?? '—'],
    ['机器 / 人工工时', `${formatDecimal(detail.machine_hours, 2)} / ${formatDecimal(detail.labor_hours, 2)}`],
    ['产品族', detail.part.product_family ?? '—'],
    ['事件 ID', detail.event_id],
  ]
  return <dl className="grid grid-cols-3 border-b border-[var(--border-soft)] bg-[var(--surface)] mobile-stack">
    {fields.map(([label, value]) => <div key={label} className="min-w-0 border-r border-b border-[var(--border-soft)] px-4 py-3">
      <dt className="label">{label}</dt><dd className="mt-1 break-all text-sm">{value}</dd>
    </div>)}
  </dl>
}

function TraceWorkspace({ detail, selectedEventId, onSelect }: {
  detail: FinishedBatchCostDetail
  selectedEventId: string
  onSelect: (eventId: string) => void
}) {
  const selectedNode = detail.trace.nodes.find((node) => node.event_id === selectedEventId)
    ?? detail.trace.nodes.find((node) => node.event_id === detail.trace.root_event_id)
    ?? null
  const records = selectedNode ? detail.trace.records.filter((record) => record.event_id === selectedNode.event_id) : []

  return <section className="grid min-h-[560px] grid-cols-[minmax(600px,1fr)_440px] mobile-stack">
    <div className="min-w-0 border-r border-[var(--border-soft)]">
      <CostTree graph={detail.trace} selectedEventId={selectedNode?.event_id ?? ''} onSelect={onSelect} />
    </div>
    <aside className="min-w-0">
      {selectedNode ? <NodeInspector node={selectedNode} records={records} /> : <div className="grid min-h-80 place-items-center text-sm text-[var(--muted)]">没有可检查的成本事件</div>}
    </aside>
  </section>
}

function NodeInspector({ node, records }: { node: CostTraceNode; records: CostTraceRecord[] }) {
  const Icon = node.event_type === 'purchase' ? ShoppingCart : Factory
  return <div>
    <div className="panel-head">
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 font-semibold"><Icon className="h-4 w-4" />{node.part.part_description}</h2>
        <p className="meta mt-1 truncate">{node.event_id}</p>
      </div>
      <Badge tone={node.event_type === 'purchase' ? 'neutral' : 'info'}>{node.event_type === 'purchase' ? '购置' : node.process_name ?? '工艺'}</Badge>
    </div>
    <dl className="grid grid-cols-2 border-b border-[var(--border-soft)]">
      <NodeMetric label="本次费用" value={node.direct_costs.total_amount} />
      <NodeMetric label="继承费用" value={node.inherited_costs.total_amount} />
      <NodeMetric label="累计费用" value={node.accumulated_costs.total_amount} />
      <NodeMetric label="展示单位成本" value={node.display_unit_cost} unit />
      <NodeMetric label="可转移单位成本" value={node.transfer_unit_cost ?? '0.00'} unit />
      <NodeMetric label="完工数量" value={node.completed_quantity} quantity={node.unit} />
    </dl>
    <CostVectorTable node={node} />
    <SourceRecords records={records} title="所选事件来源" compact />
  </div>
}

function NodeMetric({ label, value, unit = false, quantity }: { label: string; value: string; unit?: boolean; quantity?: string }) {
  return <div className="min-h-20 border-r border-b border-[var(--border-soft)] p-3">
    <dt className="label">{label}</dt>
    <dd className="num mt-2">{quantity ? `${formatDecimal(value, 4)} ${quantity}` : formatPreciseMoney(value, unit ? 2 : 0)}</dd>
  </div>
}

function CostVectorTable({ node }: { node: CostTraceNode }) {
  return <section className="border-b border-[var(--border-soft)]">
    <div className="panel-head"><h3 className="font-semibold">累计成本向量</h3><Badge>{node.accumulated_costs.items.length} 项</Badge></div>
    {node.accumulated_costs.items.length ? <div className="table-scroll max-h-64">
      <table className="data-table"><thead><tr><th>成本组 / 费用项</th><th className="text-right">金额</th></tr></thead><tbody>
        {node.accumulated_costs.items.map((item) => <tr key={item.cost_code}><td>{item.label}<span className="meta ml-2">{item.cost_group}</span></td><td className="num text-right">{formatPreciseMoney(item.amount)}</td></tr>)}
      </tbody></table>
    </div> : <p className="p-4 text-sm text-[var(--muted)]">该事件没有累计成本项。</p>}
  </section>
}

function SourceRecords({ records, title, compact = false }: { records: CostTraceRecord[]; title: string; compact?: boolean }) {
  return <section>
    <div className="panel-head">
      <div className="flex items-center gap-2"><Database className="h-4 w-4" /><h2 className={compact ? 'font-semibold' : 'section-title'}>{title}</h2></div>
      <Badge>{records.length} 条</Badge>
    </div>
    {records.length ? <div className={compact ? 'table-scroll max-h-80' : 'table-scroll min-h-[460px]'}>
      <table className="data-table" aria-label={title}>
        <thead><tr><th>事件</th><th>费用项</th><th className="text-right">金额</th><th>发生时间</th><th>来源系统</th><th>来源单据 / 行</th><th>来源记录 ID</th></tr></thead>
        <tbody>{records.map((record) => <tr key={record.cost_record_id}>
        <td className="font-mono text-xs">{record.event_id}</td>
          <td><span className="block">{record.cost_label}</span><span className="meta">{record.cost_code} · {record.cost_group}</span></td>
          <td className="num text-right">{formatPreciseMoney(record.amount)}</td>
          <td className="meta">{formatDateTime(record.incurred_at)}</td>
          <td>{record.source_system}</td>
          <td className="font-mono text-xs">{record.source_document_no}{record.source_document_line ? ` / ${record.source_document_line}` : ''}</td>
          <td className="font-mono text-xs">{record.source_record_id}</td>
        </tr>)}</tbody>
      </table>
    </div> : <p className="p-4 text-sm text-[var(--muted)]">所选范围没有直接费用来源记录。</p>}
  </section>
}

function Metric({ label, value }: { label: string; value: string }) {
  return <article className="min-h-28 border-r border-[var(--border-soft)] p-4 last:border-r-0"><p className="label">{label}</p><p className="num mt-4 text-2xl">{value}</p></article>
}

function readTab(value: string | null): DetailTab {
  return detailTabs.includes(value as DetailTab) ? value as DetailTab : 'summary'
}

function formatRate(value: string) {
  return `${formatDecimal(value, 2)}%`
}

function formatDateTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
