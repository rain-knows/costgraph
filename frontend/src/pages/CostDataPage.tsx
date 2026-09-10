import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import {
  listFinishedBatchCosts,
  type FinishedBatchCostList,
  type FinishedBatchCostSummary,
  type FinishedBatchSort,
  type ManufacturingCostGroup,
} from '../api/costData'
import type { AppOutletContext } from '../components/AppShell'
import { IconButton, PageHeader, RequestState, Segmented } from '../components/ui'
import { cn, formatDecimal } from '../lib/utils'

const pageSize = 20
const views = ['manufacturing', 'material-labor-expense', 'variable-fixed'] as const
type CostView = (typeof views)[number]

const viewOptions: Array<{ value: CostView; label: string }> = [
  { value: 'manufacturing', label: '六类制造成本' },
  { value: 'material-labor-expense', label: '料工费' },
  { value: 'variable-fixed', label: '变动 / 固定' },
]

const sortOptions: Array<{ value: FinishedBatchSort; label: string }> = [
  { value: 'completion_time_desc', label: '完工时间从新到旧' },
  { value: 'completion_time_asc', label: '完工时间从旧到新' },
  { value: 'unit_cost_desc', label: '单位成本从高到低' },
  { value: 'unit_cost_asc', label: '单位成本从低到高' },
]

export function CostDataPage() {
  const { period } = useOutletContext<AppOutletContext>()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const query = params.get('q') ?? ''
  const costCenterCode = params.get('center') ?? ''
  const sort = readSort(params.get('sort'))
  const view = readView(params.get('view'))
  const page = readPage(params.get('page'))
  const [result, setResult] = useState<FinishedBatchCostList | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setLoading(true)
      setError('')
      void listFinishedBatchCosts({
        period,
        query,
        costCenterCode,
        sort,
        page,
        pageSize,
        signal: controller.signal,
      }).then(setResult).catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取产成品批次成本失败')
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    }, 180)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [costCenterCode, page, period, query, reload, sort])

  function update(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page' && key !== 'view') next.set('page', '1')
    setParams(next, { replace: key !== 'page' })
  }

  function openBatch(batchId: string) {
    navigate(`/cost-data/${encodeURIComponent(batchId)}?period=${period}&view=${view}&tab=summary`)
  }

  const pageCount = Math.max(1, Math.ceil((result?.total ?? 0) / pageSize))
  return <div>
    <PageHeader title="批次成本数据库" />
    <section className="flex min-h-14 flex-wrap items-center gap-2 border-b border-[var(--border-soft)] px-4 py-2">
      <label className="flex h-10 min-w-64 flex-1 items-center gap-2 bg-[var(--surface)] px-3 md:max-w-sm">
        <Search className="h-4 w-4" />
        <span className="sr-only">搜索批次</span>
        <input
          value={query}
          onChange={(event) => update('q', event.target.value)}
          placeholder="零件、工单或批号"
          className="min-w-0 flex-1 bg-transparent"
        />
      </label>
      <label className="flex h-10 items-center gap-2 bg-[var(--surface)] px-3">
        <span className="label">成本中心</span>
        <input
          value={costCenterCode}
          onChange={(event) => update('center', event.target.value)}
          placeholder="全部"
          className="w-24 bg-transparent font-mono text-sm"
        />
      </label>
      <label className="flex h-10 items-center gap-2">
        <span className="label">排序</span>
        <select className="toolbar-select" value={sort} onChange={(event) => update('sort', event.target.value)}>
          {sortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>
      <span className="ml-auto meta">{result?.total ?? 0} 个批次 · {period}</span>
    </section>
    <div className="flex min-h-14 items-center overflow-x-auto border-b border-[var(--border-soft)] px-4">
      <Segmented value={view} onChange={(nextView) => update('view', nextView)} label="成本核算视图" options={viewOptions} />
    </div>
    <RequestState
      loading={loading && !result}
      error={error}
      empty={!loading && !error && result?.items.length === 0}
      onRetry={() => setReload((value) => value + 1)}
    >
      {result ? <BatchCostTable items={result.items} page={page} view={view} onOpen={openBatch} /> : null}
    </RequestState>
    <footer className="flex min-h-14 items-center justify-between gap-3 border-t border-[var(--border)] px-4">
      <span className="text-sm text-[var(--fg-2)]">第 {page} / {pageCount} 页</span>
      <div className="flex gap-1">
        <IconButton label="上一页" disabled={page <= 1 || loading} onClick={() => update('page', String(page - 1))}><ChevronLeft className="h-4 w-4" /></IconButton>
        <IconButton label="下一页" disabled={page >= pageCount || loading} onClick={() => update('page', String(page + 1))}><ChevronRight className="h-4 w-4" /></IconButton>
      </div>
    </footer>
  </div>
}

function BatchCostTable({ items, page, view, onOpen }: {
  items: FinishedBatchCostSummary[]
  page: number
  view: CostView
  onOpen: (batchId: string) => void
}) {
  const groups = items[0]?.manufacturing_view.groups ?? []
  return <section className="table-scroll min-h-[520px]" aria-busy="false">
    <table className="data-table cost-table" aria-label={viewOptions.find((option) => option.value === view)?.label}>
      <thead>
        <tr>
          <IdentityHeaders />
          <ViewGroupHeaders view={view} groups={groups} />
        </tr>
        <tr className="cost-subhead">
          <ViewColumnHeaders view={view} groups={groups} />
        </tr>
      </thead>
      <tbody>
        {items.map((item, index) => (
          <tr
            key={item.finished_batch_id}
            tabIndex={0}
            aria-label={`打开批次 ${item.lot_number} 成本详情`}
            onClick={() => onOpen(item.finished_batch_id)}
            onKeyDown={(event) => activateRow(event, () => onOpen(item.finished_batch_id))}
            className="cursor-pointer focus-visible:shadow-[var(--focus-ring)]"
          >
            <IdentityCells item={item} sequence={(page - 1) * pageSize + index + 1} />
            <ViewCells item={item} view={view} />
          </tr>
        ))}
      </tbody>
    </table>
  </section>
}

function IdentityHeaders() {
  return <>
    <StickyHeader rowSpan={2} className="cost-sticky-sequence text-right">序号</StickyHeader>
    <StickyHeader rowSpan={2} className="cost-sticky-center">成本中心</StickyHeader>
    <StickyHeader rowSpan={2} className="cost-sticky-center-name">车间名称</StickyHeader>
    <StickyHeader rowSpan={2} className="cost-sticky-part">零件号</StickyHeader>
    <th rowSpan={2}>零件描述</th>
    <th rowSpan={2}>车间工单号</th>
    <th rowSpan={2}>批号（完工时间）</th>
    <th rowSpan={2}>完工数量（合格）</th>
    <th rowSpan={2}>不良品（数量）</th>
    <th rowSpan={2}>合格率</th>
    <th rowSpan={2}>生产工时（机器）</th>
    <th rowSpan={2}>生产工时（人工）</th>
    <th rowSpan={2}>工艺步骤</th>
    <th rowSpan={2}>产品族</th>
  </>
}

function StickyHeader({ className, children, ...props }: { className: string; children: ReactNode; rowSpan: number }) {
  return <th {...props} className={cn('cost-sticky', className)}>{children}</th>
}

function ViewGroupHeaders({ view, groups }: { view: CostView; groups: ManufacturingCostGroup[] }) {
  if (view === 'manufacturing') {
    return <>{groups.map((group) => (
      <th key={group.group_code} colSpan={group.leaves.length + 2} className="cost-group-head text-center">{group.group_label}</th>
    ))}<th className="cost-group-head text-center">制造单位成本</th></>
  }
  if (view === 'material-labor-expense') return <th colSpan={4} className="cost-group-head text-center">制造单位成本</th>
  return <>
    <th colSpan={3} className="cost-group-head text-center">制造单位成本</th>
    <th colSpan={3} className="cost-group-head text-center">制造后费用</th>
    <th colSpan={3} className="cost-group-head text-center">合计单位成本</th>
  </>
}

function ViewColumnHeaders({ view, groups }: { view: CostView; groups: ManufacturingCostGroup[] }) {
  if (view === 'manufacturing') {
    return <>{groups.flatMap((group) => [
      ...group.leaves.map((leaf) => <th key={`${group.group_code}-${leaf.cost_code}`} title={leaf.label}>{leaf.label}</th>),
      <th key={`${group.group_code}-subtotal`}>小计</th>,
      <th key={`${group.group_code}-share`}>占比</th>,
    ])}<th>单位成本1</th></>
  }
  if (view === 'material-labor-expense') return <><th>料</th><th>工</th><th>费</th><th>单位成本1</th></>
  return <>
    <th>变动成本1</th><th>固定成本1</th><th>单位成本1</th>
    <th>售后赔偿费</th><th>运输费</th><th>仓储保管费</th>
    <th>变动成本2</th><th>固定成本2</th><th>单位成本2</th>
  </>
}

function IdentityCells({ item, sequence }: { item: FinishedBatchCostSummary; sequence: number }) {
  return <>
    <td className="cost-sticky cost-sticky-sequence num text-right">{sequence}</td>
    <td className="cost-sticky cost-sticky-center font-mono">{item.cost_center_code ?? '—'}</td>
    <td className="cost-sticky cost-sticky-center-name">{item.cost_center_name ?? '—'}</td>
    <td className="cost-sticky cost-sticky-part font-mono font-semibold text-[var(--accent)]">{item.part.part_number}</td>
    <td>{item.part.part_description}</td>
    <td className="font-mono">{item.work_order_number ?? '—'}</td>
    <td><span className="block font-mono">{item.lot_number}</span><span className="meta mt-1 block">{formatDateTime(item.completion_time)}</span></td>
    <td className="num text-right">{formatQuantity(item.qualified_quantity)} {item.unit}</td>
    <td className="num text-right">{formatQuantity(item.defective_quantity)} {item.unit}</td>
    <td className="num text-right">{formatRate(item.quality_rate)}</td>
    <td className="num text-right">{formatHours(item.machine_hours)}</td>
    <td className="num text-right">{formatHours(item.labor_hours)}</td>
    <td>{item.process_name ?? item.process_code ?? '—'}</td>
    <td>{item.part.product_family ?? '—'}</td>
  </>
}

function ViewCells({ item, view }: { item: FinishedBatchCostSummary; view: CostView }) {
  if (view === 'manufacturing') {
    return <>{item.manufacturing_view.groups.flatMap((group) => [
      ...group.leaves.map((leaf) => <MoneyCell key={`${group.group_code}-${leaf.cost_code}`} value={leaf.metric.unit_cost} />),
      <MoneyCell key={`${group.group_code}-subtotal`} value={group.metric.unit_cost} strong />,
      <td key={`${group.group_code}-share`} className="num text-right">{formatRate(group.share)}</td>,
    ])}<MoneyCell value={item.manufacturing_view.total.unit_cost} strong /></>
  }
  if (view === 'material-labor-expense') {
    const costs = item.material_labor_overhead_view
    return <><MoneyCell value={costs.material.unit_cost} /><MoneyCell value={costs.labor.unit_cost} /><MoneyCell value={costs.overhead.unit_cost} /><MoneyCell value={costs.total.unit_cost} strong /></>
  }
  const costs = item.variable_fixed_view
  return <>
    <MoneyCell value={costs.variable_cost_1.unit_cost} />
    <MoneyCell value={costs.fixed_cost_1.unit_cost} />
    <MoneyCell value={costs.manufacturing_total.unit_cost} strong />
    <MoneyCell value={costs.after_sales_compensation.unit_cost} />
    <MoneyCell value={costs.transportation.unit_cost} />
    <MoneyCell value={costs.storage_fee.unit_cost} />
    <MoneyCell value={costs.variable_cost_2.unit_cost} />
    <MoneyCell value={costs.fixed_cost_2.unit_cost} />
    <MoneyCell value={costs.total_cost_2.unit_cost} strong />
  </>
}

function MoneyCell({ value, strong = false }: { value: string; strong?: boolean }) {
  return <td className={cn('num text-right', strong && 'font-semibold')}>{formatDecimal(value, 2)}</td>
}

function activateRow(event: KeyboardEvent<HTMLTableRowElement>, action: () => void) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  action()
}

function readView(value: string | null): CostView {
  return views.includes(value as CostView) ? value as CostView : 'manufacturing'
}

function readSort(value: string | null): FinishedBatchSort {
  return sortOptions.some((option) => option.value === value) ? value as FinishedBatchSort : 'completion_time_desc'
}

function readPage(value: string | null) {
  const page = Number(value)
  return Number.isInteger(page) && page > 0 ? page : 1
}

function formatDateTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(date)
}

function formatQuantity(value: string) {
  return formatDecimal(value, 4)
}

function formatHours(value: string) {
  return formatDecimal(value, 2)
}

function formatRate(value: string) {
  return `${formatDecimal(value, 2)}%`
}
