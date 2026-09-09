import { useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import { Box, ChevronDown, ChevronRight, GitMerge, ShoppingCart } from 'lucide-react'
import type { CostTraceEdge, CostTraceGraph, CostTraceNode } from '../api/costData'
import { cn, formatDecimal, formatPreciseMoney } from '../lib/utils'
import { Badge } from './ui'

type TreeRow = {
  key: string
  node: CostTraceNode
  depth: number
  edge: CostTraceEdge | null
  hasChildren: boolean
  referenced: boolean
}

export function CostTree({ graph, selectedEventId, onSelect }: {
  graph: CostTraceGraph
  selectedEventId: string
  onSelect: (eventId: string) => void
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(graph.nodes.map((node) => node.event_id)))

  useEffect(() => {
    setExpanded(new Set(graph.nodes.map((node) => node.event_id)))
  }, [graph])

  const rows = useMemo(() => projectTrace(graph, expanded), [expanded, graph])

  function toggle(eventId: string) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(eventId)) next.delete(eventId)
      else next.add(eventId)
      return next
    })
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>, row: TreeRow) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect(row.node.event_id)
    } else if (event.key === 'ArrowRight' && row.hasChildren && !expanded.has(row.node.event_id)) {
      event.preventDefault()
      toggle(row.node.event_id)
    } else if (event.key === 'ArrowLeft' && row.hasChildren && expanded.has(row.node.event_id)) {
      event.preventDefault()
      toggle(row.node.event_id)
    }
  }

  return <section className="min-w-0">
    <div className="panel-head">
      <div><h2 className="section-title">成本追溯</h2><p className="meta mt-1">产成品向上展开至购置与工艺事件</p></div>
      <Badge tone="info">{graph.nodes.length} 个事件</Badge>
    </div>
    <div className="table-scroll">
      <div className="min-w-[720px]" role="tree" aria-label="批次成本追溯树">
        <div className="grid min-h-10 grid-cols-[minmax(340px,1fr)_150px_150px_140px] items-center border-b border-[var(--border)] bg-[var(--surface)] px-3 text-xs text-[var(--fg-2)]">
          <span>批次 / 零件</span><span>事件</span><span className="text-right">领用 / 产出数量</span><span className="text-right">累计成本</span>
        </div>
        {rows.map((row) => {
          const open = expanded.has(row.node.event_id)
          const Icon = row.node.event_type === 'purchase' ? ShoppingCart : Box
          return <div
            key={row.key}
            role="treeitem"
            aria-level={row.depth + 1}
            aria-expanded={row.hasChildren && !row.referenced ? open : undefined}
            aria-selected={selectedEventId === row.node.event_id}
            tabIndex={0}
            onClick={() => onSelect(row.node.event_id)}
            onKeyDown={(event) => handleKeyDown(event, row)}
            className={cn(
              'grid min-h-14 cursor-pointer grid-cols-[minmax(340px,1fr)_150px_150px_140px] items-center border-b border-[var(--border-soft)] px-3 text-sm',
              'hover:bg-[var(--surface)]',
              selectedEventId === row.node.event_id && 'bg-[var(--surface-warm)]',
            )}
          >
            <span className="flex min-w-0 items-center" style={{ paddingLeft: row.depth * 24 }}>
              {row.hasChildren && !row.referenced ? <button
                type="button"
                className="grid h-8 w-8 shrink-0 place-items-center"
                aria-label={`${open ? '折叠' : '展开'} ${row.node.part.part_description}`}
                onClick={(event) => {
                  event.stopPropagation()
                  toggle(row.node.event_id)
                }}
              >{open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</button> : <span className="w-8 shrink-0" />}
              <Icon className="mr-2 h-4 w-4 text-[var(--fg-2)]" />
              <span className="min-w-0">
                <span className="block truncate font-semibold">{row.node.part.part_description}</span>
                <span className="meta">{row.node.part.part_number} · {row.node.output_batch_id}</span>
              </span>
              {row.referenced ? <Badge className="ml-2 shrink-0"><GitMerge className="h-3 w-3" />引用</Badge> : null}
            </span>
            <span><span className="block">{eventLabel(row.node)}</span><span className="meta mt-1 block">{row.node.event_id}</span></span>
            <span className="num text-right">
              {row.edge
                ? `${formatDecimal(row.edge.consumed_quantity, 4)} ${row.edge.unit}`
                : `${formatDecimal(row.node.completed_quantity, 4)} ${row.node.unit}`}
              {row.edge ? <span className="meta mt-1 block">分配 {formatRate(row.edge.allocation_ratio)}</span> : null}
            </span>
            <span className="num text-right">
              {formatPreciseMoney(row.node.accumulated_costs.total_amount)}
              {row.edge ? <span className="meta mt-1 block">本边 {formatPreciseMoney(row.edge.allocated_costs.total_amount)}</span> : null}
            </span>
          </div>
        })}
      </div>
    </div>
  </section>
}

function projectTrace(graph: CostTraceGraph, expanded: Set<string>): TreeRow[] {
  const nodeById = new Map(graph.nodes.map((node) => [node.event_id, node]))
  const inputsByTarget = new Map<string, CostTraceEdge[]>()
  for (const edge of graph.edges) {
    const inputs = inputsByTarget.get(edge.target_event_id) ?? []
    inputs.push(edge)
    inputsByTarget.set(edge.target_event_id, inputs)
  }

  const root = nodeById.get(graph.root_event_id)
  if (!root) return []

  const seen = new Set<string>()
  const rows: TreeRow[] = []

  function visit(node: CostTraceNode, depth: number, edge: CostTraceEdge | null, key: string) {
    const referenced = seen.has(node.event_id)
    seen.add(node.event_id)
    const inputs = inputsByTarget.get(node.event_id) ?? []
    rows.push({ key, node, depth, edge, hasChildren: inputs.length > 0, referenced })
    if (referenced || !expanded.has(node.event_id)) return
    for (const input of inputs) {
      const source = nodeById.get(input.source_event_id)
      if (source) visit(source, depth + 1, input, `${key}/${input.input_id}`)
    }
  }

  visit(root, 0, null, root.event_id)
  return rows
}

function eventLabel(node: CostTraceNode) {
  if (node.event_type === 'purchase') return '购置'
  return node.process_name ?? node.process_code ?? '工艺步骤'
}

function formatRate(value: string) {
  return `${formatDecimal(Number(value) * 100, 2)}%`
}
