import { useMemo, useState } from 'react'
import { Box, ChevronDown, ChevronRight, CircleDollarSign, Database, Factory } from 'lucide-react'
import type { ProductPeriodDetail } from '../api/costData'
import { formatPreciseMoney } from '../lib/utils'
import { Badge } from './ui'

type TreeNode = { id: string; label: string; type: 'product' | 'process' | 'cost' | 'source'; amount?: string; records?: number; children?: TreeNode[] }
const icons = { product: Box, process: Factory, cost: CircleDollarSign, source: Database }

export function CostTree({ detail }: { detail: ProductPeriodDetail }) {
  const tree = useMemo<TreeNode[]>(() => [{
    id: detail.product.product_id,
    label: detail.product.product_name,
    type: 'product',
    amount: detail.total_cost,
    children: detail.processes.map((process) => ({
      id: process.process_code,
      label: process.process_name,
      type: 'process',
      amount: process.total_cost,
      children: process.items.map((item) => ({
        id: `${process.process_code}-${item.cost_item}`,
        label: item.label,
        type: 'cost',
        amount: item.amount,
        children: [{ id: `${process.process_code}-${item.cost_item}-source`, label: `${item.source_record_count} 条来源记录`, type: 'source', records: item.source_record_count }],
      })),
    })),
  }], [detail])
  const [expanded, setExpanded] = useState(() => new Set<string>([detail.product.product_id, ...detail.processes.map((item) => item.process_code)]))
  const rows = flatten(tree, expanded)
  return <section className="min-h-[520px]">
    <div className="panel-head"><div><h2 className="section-title">成本关系树</h2><p className="meta mt-1">产品 → 工序 → 成本项 → 来源摘要</p></div><Badge tone="info">已发布</Badge></div>
    <div className="table-scroll"><div className="min-w-[620px]" role="tree" aria-label={`${detail.product.product_name} 成本关系树`}>{rows.map(({ node, depth }) => {
      const Icon = icons[node.type]
      const open = expanded.has(node.id)
      return <div key={node.id} role="treeitem" aria-expanded={node.children ? open : undefined} className="grid min-h-12 grid-cols-[minmax(300px,1fr)_160px_120px] items-center border-b border-[var(--border-soft)] px-3 text-sm hover:bg-[var(--surface)]">
        <span className="flex min-w-0 items-center" style={{ paddingLeft: depth * 28 }}>{node.children ? <button className="grid h-8 w-8 place-items-center" aria-label={`${open ? '折叠' : '展开'} ${node.label}`} onClick={() => setExpanded((current) => { const next = new Set(current); if (open) next.delete(node.id); else next.add(node.id); return next })}>{open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</button> : <span className="w-8" />}<Icon className="mr-2 h-4 w-4 text-[var(--fg-2)]" /><span className="truncate">{node.label}</span></span>
        <span className="font-mono text-xs text-[var(--muted)]">{node.type === 'product' ? '产品' : node.type === 'process' ? '工序' : node.type === 'cost' ? '成本项' : '来源摘要'}</span>
        <span className="num text-right">{node.amount ? formatPreciseMoney(node.amount, 0) : node.records ? `${node.records} 条` : '—'}</span>
      </div>
    })}</div></div>
  </section>
}

function flatten(nodes: TreeNode[], expanded: Set<string>, depth = 0): Array<{ node: TreeNode; depth: number }> {
  return nodes.flatMap((node) => [{ node, depth }, ...(node.children && expanded.has(node.id) ? flatten(node.children, expanded, depth + 1) : [])])
}
