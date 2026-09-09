import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { listProductCosts, type ProductListResponse } from '../api/costData'
import type { AppOutletContext } from '../components/AppShell'
import { IconButton, PageHeader, RequestState } from '../components/ui'
import { formatDecimal, formatPercent, formatPreciseMoney } from '../lib/utils'

const pageSize = 20

export function CostDataPage() {
  const { period } = useOutletContext<AppOutletContext>()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const query = params.get('q') ?? ''
  const sort = params.get('sort') ?? 'product_id'
  const page = Math.max(1, Number(params.get('page') ?? 1))
  const [result, setResult] = useState<ProductListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setLoading(true); setError('')
      void listProductCosts({ period, query, sort, page, pageSize, signal: controller.signal }).then(setResult).catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取产品成本失败')
      }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 180)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [page, period, query, reload, sort])

  function update(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    if (key !== 'page') next.set('page', '1')
    setParams(next, { replace: key !== 'page' })
  }

  const pageCount = Math.max(1, Math.ceil((result?.total ?? 0) / pageSize))
  return <div>
    <PageHeader eyebrow="成本数据 / 产品期间汇总" title="产品成本数据库" description="查看当前期间内已发布的产品产量、总成本、单位成本和期间比较。" />
    <section className="flex min-h-14 flex-wrap items-center gap-2 border-b border-[var(--border-soft)] px-4 py-2">
      <label className="flex h-10 min-w-60 flex-1 items-center gap-2 bg-[var(--surface)] px-3 md:max-w-sm"><Search className="h-4 w-4" /><span className="sr-only">搜索产品</span><input value={query} onChange={(event) => update('q', event.target.value)} placeholder="搜索产品编码、名称或规格" className="min-w-0 flex-1 bg-transparent" /></label>
      <label className="flex h-10 items-center gap-2"><span className="label">排序</span><select className="toolbar-select" value={sort} onChange={(event) => update('sort', event.target.value)}><option value="product_id">产品编码</option><option value="product_name">产品名称</option><option value="total_cost_desc">总成本从高到低</option><option value="total_cost_asc">总成本从低到高</option><option value="unit_cost_desc">单位成本从高到低</option><option value="unit_cost_asc">单位成本从低到高</option></select></label>
      <span className="ml-auto meta">{result?.total ?? 0} 个产品 · {period}</span>
    </section>
    <RequestState loading={loading && !result} error={error} empty={!loading && !error && result?.items.length === 0} onRetry={() => setReload((value) => value + 1)}>
      {result ? <section className="table-scroll min-h-[520px]"><table className="data-table"><thead><tr><th>产品</th><th>规格</th><th className="text-right">产量</th><th className="text-right">总成本</th><th className="text-right">单位成本</th><th className="text-right">总成本环比</th><th className="text-right">单位成本环比</th></tr></thead><tbody>{result.items.map((item) => <tr key={item.product_id}><td><button className="text-left hover:text-[var(--accent)] hover:underline" onClick={() => navigate(`/cost-data/${encodeURIComponent(item.product_id)}?period=${period}`)}><strong className="font-semibold">{item.product_name}</strong><span className="ml-2 meta">{item.product_id}</span></button></td><td>{item.spec || '—'}</td><td className="num text-right">{formatDecimal(item.output_qty)}</td><td className="num text-right">{formatPreciseMoney(item.total_cost, 0)}</td><td className="num text-right">{formatPreciseMoney(item.unit_cost)}</td><td className="num text-right">{item.comparison ? formatPercent(item.comparison.total_cost_delta_rate) : '—'}</td><td className="num text-right">{item.comparison ? formatPercent(item.comparison.unit_cost_delta_rate) : '—'}</td></tr>)}</tbody></table></section> : null}
    </RequestState>
    <footer className="flex min-h-14 items-center justify-between gap-3 border-t border-[var(--border)] px-4"><span className="text-sm text-[var(--fg-2)]">第 {page} / {pageCount} 页</span><div className="flex gap-1"><IconButton label="上一页" disabled={page <= 1 || loading} onClick={() => update('page', String(page - 1))}><ChevronLeft className="h-4 w-4" /></IconButton><IconButton label="下一页" disabled={page >= pageCount || loading} onClick={() => update('page', String(page + 1))}><ChevronRight className="h-4 w-4" /></IconButton></div></footer>
  </div>
}
