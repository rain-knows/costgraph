import type {
  ManufacturingCostView,
  MaterialLaborOverheadView,
  VariableFixedCostView,
} from '../api/costData'
import { formatDecimal, formatPreciseMoney } from '../lib/utils'

export function CostViewSummary({ manufacturing, materialLaborOverhead, variableFixed }: {
  manufacturing: ManufacturingCostView
  materialLaborOverhead: MaterialLaborOverheadView
  variableFixed: VariableFixedCostView
}) {
  return <div>
    <ManufacturingSummary view={manufacturing} />
    <MaterialLaborOverheadSummary view={materialLaborOverhead} />
    <VariableFixedSummary view={variableFixed} />
  </div>
}

export function ManufacturingSummary({ view }: { view: ManufacturingCostView }) {
  return <section className="border-b border-[var(--border-soft)]">
    <div className="panel-head">
      <div><h2 className="section-title">六类制造成本</h2><p className="meta mt-1">本批金额、单位成本与制造成本占比</p></div>
      <div className="text-right"><span className="label block">制造单位成本1</span><strong className="num mt-1 block text-lg">{formatPreciseMoney(view.total.unit_cost)}</strong></div>
    </div>
    <div className="table-scroll">
      <table className="data-table" aria-label="六类制造成本汇总">
        <thead><tr><th>成本组 / 费用项</th><th className="text-right">本批金额</th><th className="text-right">单位成本</th><th className="text-right">占比</th></tr></thead>
        <tbody>
          {view.groups.flatMap((group) => [
            <tr key={group.group_code} className="bg-[var(--surface)]">
              <td className="font-semibold">{group.group_label}</td>
              <td className="num text-right font-semibold">{formatPreciseMoney(group.metric.amount)}</td>
              <td className="num text-right font-semibold">{formatPreciseMoney(group.metric.unit_cost)}</td>
              <td className="num text-right">{formatRate(group.share)}</td>
            </tr>,
            ...group.leaves.map((leaf) => <tr key={`${group.group_code}-${leaf.cost_code}`}>
              <td><span className="pl-5">{leaf.label}</span><span className="meta ml-2">{leaf.cost_code}</span></td>
              <td className="num text-right">{formatPreciseMoney(leaf.metric.amount)}</td>
              <td className="num text-right">{formatPreciseMoney(leaf.metric.unit_cost)}</td>
              <td className="num text-right">{formatRate(leaf.share)}</td>
            </tr>),
          ])}
          <tr>
            <td className="font-semibold">制造成本合计</td>
            <td className="num text-right font-semibold">{formatPreciseMoney(view.total.amount)}</td>
            <td className="num text-right font-semibold">{formatPreciseMoney(view.total.unit_cost)}</td>
            <td className="num text-right">100.00%</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
}

export function MaterialLaborOverheadSummary({ view }: { view: MaterialLaborOverheadView }) {
  return <section className="border-b border-[var(--border-soft)]">
    <div className="panel-head"><div><h2 className="section-title">料工费</h2><p className="meta mt-1">制造成本管理视图</p></div></div>
    <MetricTable
      ariaLabel="料工费成本汇总"
      columns={[
        { label: '料', metric: view.material },
        { label: '工', metric: view.labor },
        { label: '费', metric: view.overhead },
        { label: '单位成本1', metric: view.total },
      ]}
    />
  </section>
}

export function VariableFixedSummary({ view }: { view: VariableFixedCostView }) {
  return <section>
    <div className="panel-head"><div><h2 className="section-title">变动 / 固定成本</h2><p className="meta mt-1">制造成本与制造后费用</p></div></div>
    <MetricTable
      ariaLabel="变动固定成本汇总"
      columns={[
        { label: '变动成本1', metric: view.variable_cost_1 },
        { label: '固定成本1', metric: view.fixed_cost_1 },
        { label: '单位成本1', metric: view.manufacturing_total },
        { label: '售后赔偿费', metric: view.after_sales_compensation },
        { label: '运输费', metric: view.transportation },
        { label: '仓储保管费', metric: view.storage_fee },
        { label: '变动成本2', metric: view.variable_cost_2 },
        { label: '固定成本2', metric: view.fixed_cost_2 },
        { label: '单位成本2', metric: view.total_cost_2 },
      ]}
    />
  </section>
}

function MetricTable({ ariaLabel, columns }: {
  ariaLabel: string
  columns: Array<{ label: string; metric: { amount: string; unit_cost: string } }>
}) {
  return <div className="table-scroll">
    <table className="data-table" aria-label={ariaLabel}>
      <thead><tr><th>口径</th>{columns.map((column) => <th key={column.label} className="text-right">{column.label}</th>)}</tr></thead>
      <tbody>
        <tr><td>本批金额</td>{columns.map((column) => <td key={column.label} className="num text-right">{formatPreciseMoney(column.metric.amount)}</td>)}</tr>
        <tr><td>单位成本</td>{columns.map((column) => <td key={column.label} className="num text-right">{formatPreciseMoney(column.metric.unit_cost)}</td>)}</tr>
      </tbody>
    </table>
  </div>
}

function formatRate(value: string) {
  return `${formatDecimal(value, 2)}%`
}
