import { useEffect, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { isForbidden, isNotFound, isUnauthorized } from '../api/http'
import { listIamPrincipals, setPrincipalRoles, setPrincipalScope, type IamPrincipal, type RoleId } from '../api/iam'
import { hasPermission } from '../auth/permissions'
import type { AppOutletContext } from '../components/AppShell'
import { Badge, Button, PageHeader, StatePanel } from '../components/ui'
import { formatDateTime } from '../lib/utils'

type LoadState = 'loading' | 'ready' | 'empty' | 'error' | 'unavailable' | 'unauthorized'

const PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/
const ROLES: Array<{ value: RoleId; label: string }> = [
  { value: 'viewer', label: '只读' },
  { value: 'data_operator', label: '数据操作' },
  { value: 'data_approver', label: '数据审批' },
  { value: 'tenant_admin', label: '租户管理员' },
]

const ROLE_LABELS = Object.fromEntries(ROLES.map((role) => [role.value, role.label])) as Record<RoleId, string>

function periodError(start: string, end: string): string {
  if (start && !PERIOD_PATTERN.test(start)) return '期间起点必须形如 2026-06。'
  if (end && !PERIOD_PATTERN.test(end)) return '期间终点必须形如 2026-06。'
  if (start && end && start > end) return '期间起点不能晚于终点，请先修正再保存。'
  return ''
}

export function IamPage() {
  const { period, me, meLoading, meError } = useOutletContext<AppOutletContext>()
  const [search, setSearch] = useSearchParams()
  const page = Math.max(1, Number(search.get('page') || 1))
  const [state, setState] = useState<LoadState>('loading')
  const [message, setMessage] = useState('')
  const [principals, setPrincipals] = useState<IamPrincipal[]>([])
  const [total, setTotal] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [roles, setRoles] = useState<RoleId[]>([])
  const [partIds, setPartIds] = useState('')
  const [periodStart, setPeriodStart] = useState('')
  const [periodEnd, setPeriodEnd] = useState('')
  const [formError, setFormError] = useState('')
  const [saved, setSaved] = useState('')
  const [pending, setPending] = useState(false)
  const [reload, setReload] = useState(0)
  const permitted = hasPermission(me?.permissions, 'iam.manage')
  const selected = principals.find((item) => item.principal_id === selectedId) ?? null

  useEffect(() => {
    if (meLoading) { setState('loading'); return }
    if (meError) { setState('unavailable'); setMessage('无法读取当前主体的权限摘要。'); return }
    if (!permitted) { setState('unauthorized'); return }
    const controller = new AbortController()
    setState('loading')
    setMessage('')
    listIamPrincipals({ page, pageSize: 20, signal: controller.signal }).then((list) => {
      setPrincipals(list.items)
      setTotal(list.total)
      setState(list.items.length ? 'ready' : 'empty')
    }).catch((failure: unknown) => {
      if (controller.signal.aborted) return
      if (isUnauthorized(failure) || isForbidden(failure)) { setState('unauthorized'); return }
      if (failure instanceof Error && 'status' in failure && (failure as { status: number }).status === 503) {
        setState('unavailable')
        setMessage(failure.message)
        return
      }
      setState('error')
      setMessage(failure instanceof Error ? failure.message : '读取主体权限失败')
    })
    return () => controller.abort()
  }, [meError, meLoading, page, permitted, reload])

  function select(principal: IamPrincipal) {
    setSelectedId(principal.principal_id)
    setRoles(principal.roles)
    setPartIds(principal.data_scope.allowed_part_ids.join(', '))
    setPeriodStart(principal.data_scope.allowed_period_start ?? '')
    setPeriodEnd(principal.data_scope.allowed_period_end ?? '')
    setFormError('')
    setSaved('')
  }

  function updatePage(value: number) {
    const next = new URLSearchParams(search)
    next.set('page', String(value))
    next.set('period', period)
    setSearch(next)
  }

  function reportFailure(failure: unknown) {
    if (isForbidden(failure)) setFormError('当前主体没有 iam.manage 权限。')
    else if (isNotFound(failure)) setFormError('该主体不存在或不属于当前租户。')
    else if (failure instanceof Error && 'status' in failure && (failure as { status: number }).status === 503) setFormError(`服务暂不可用：${failure.message}`)
    else setFormError(failure instanceof Error ? failure.message : '保存失败')
  }

  async function saveRoles() {
    if (!selected) return
    setFormError('')
    setSaved('')
    if (!roles.length) { setFormError('至少需要保留一个角色。'); return }
    setPending(true)
    try {
      await setPrincipalRoles(selected.principal_id, roles)
      setSaved('角色已保存')
      setReload((value) => value + 1)
    } catch (failure) { reportFailure(failure) } finally { setPending(false) }
  }

  async function saveScope() {
    if (!selected) return
    setFormError('')
    setSaved('')
    const invalid = periodError(periodStart, periodEnd)
    if (invalid) { setFormError(invalid); return }
    setPending(true)
    try {
      await setPrincipalScope(selected.principal_id, {
        allowed_part_ids: partIds.split(',').map((value) => value.trim()).filter(Boolean),
        allowed_period_start: periodStart || null,
        allowed_period_end: periodEnd || null,
      })
      setSaved('数据范围已保存')
      setReload((value) => value + 1)
    } catch (failure) { reportFailure(failure) } finally { setPending(false) }
  }

  return <div>
    <PageHeader title="身份与权限" actions={<Button variant="ghost" onClick={() => setReload((value) => value + 1)}>刷新</Button>} />
    {state === 'unauthorized' ? <StatePanel state="unauthorized" title="无权访问身份与权限" detail="当前主体没有 iam.manage 权限。" /> : null}
    {state === 'unavailable' ? <StatePanel state="unavailable" detail={message || undefined} onRetry={() => setReload((value) => value + 1)} /> : null}
    {state === 'error' ? <StatePanel state="error" detail={message} onRetry={() => setReload((value) => value + 1)} /> : null}
    {state === 'loading' ? <StatePanel state="loading" detail="正在读取当前租户的主体与数据范围。" /> : null}
    {state === 'empty' ? <StatePanel state="empty" title="尚无主体记录" detail="首次写入角色或数据范围后，主体会出现在这里。" /> : null}
    {state === 'ready' ? <div className="grid grid-cols-[minmax(0,1fr)_360px] mobile-stack">
      <section className="mobile-stack">
        <div className="panel-head"><div><h2 className="section-title">主体列表</h2><p className="meta mt-1">选中一行后可编辑角色与数据范围</p></div><Badge tone="info">{total} 个主体</Badge></div>
        <div className="table-scroll"><table className="data-table" aria-label="主体列表"><thead><tr><th>主体 ID</th><th>角色</th><th>零件范围</th><th>期间范围</th><th>更新人</th><th>更新时间</th></tr></thead>
          <tbody>{principals.map((item) => <tr key={item.principal_id} tabIndex={0} aria-selected={item.principal_id === selectedId} onClick={() => select(item)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(item) } }}>
            <td className="num">{item.principal_id}</td>
            <td><div className="flex flex-wrap gap-1">{item.roles.map((role) => <Badge key={role} tone="info">{ROLE_LABELS[role]}</Badge>)}</div></td>
            <td className="num">{item.data_scope.allowed_part_ids.length ? `${item.data_scope.allowed_part_ids.length} 个零件` : '全部零件'}</td>
            <td className="num">{scopeLabel(item)}</td>
            <td className="num">{item.updated_by}</td>
            <td className="num">{formatDateTime(item.updated_at)}</td>
          </tr>)}</tbody></table></div>
        <div className="flex min-h-14 items-center justify-end gap-3 border-t border-[var(--border-soft)] px-4">
          <Button size="sm" disabled={page <= 1} onClick={() => updatePage(page - 1)}>上一页</Button>
          <span className="meta">第 {page} 页</span>
          <Button size="sm" disabled={page * 20 >= total} onClick={() => updatePage(page + 1)}>下一页</Button>
        </div>
      </section>
      <section aria-label="主体编辑" className="px-4 py-4">
        {selected ? <>
          <h2 className="section-title">编辑 {selected.principal_id}</h2>
          <p className="meta mt-1">tenant_admin 角色会授予全部权限。</p>
          <fieldset className="mt-4"><legend className="label">角色</legend>
            <div className="mt-2 grid gap-2">{ROLES.map((role) => <label key={role.value} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={roles.includes(role.value)} onChange={(event) => setRoles((current) => event.target.checked ? [...current, role.value] : current.filter((value) => value !== role.value))} />
              <span>{role.label}<span className="meta ml-2">{role.value}</span></span>
            </label>)}</div>
          </fieldset>
          <Button className="mt-3" size="sm" variant="primary" disabled={pending || !roles.length} onClick={() => void saveRoles()}>保存角色</Button>

          <fieldset className="mt-6"><legend className="label">数据范围</legend>
            <label className="mt-2 grid gap-1"><span className="meta">零件 ID（逗号分隔，留空表示全部零件）</span>
              <input value={partIds} onChange={(event) => setPartIds(event.target.value)} placeholder="P-1, P-2" className="h-10 bg-[var(--surface)] px-3 text-sm" /></label>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <label className="grid gap-1"><span className="meta">期间起点</span><input type="month" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} className="h-10 bg-[var(--surface)] px-3 text-sm" /></label>
              <label className="grid gap-1"><span className="meta">期间终点</span><input type="month" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} className="h-10 bg-[var(--surface)] px-3 text-sm" /></label>
            </div>
          </fieldset>
          <Button className="mt-3" size="sm" variant="primary" disabled={pending} onClick={() => void saveScope()}>保存数据范围</Button>

          {formError ? <p className="mt-3 text-sm text-[var(--danger)]" role="alert">{formError}</p> : null}
          {saved ? <p className="mt-3 text-sm" role="status">{saved}</p> : null}
        </> : <p className="text-sm text-[var(--fg-2)]">在左侧选择一个主体后编辑角色与数据范围。</p>}
      </section>
    </div> : null}
  </div>
}

function scopeLabel(principal: IamPrincipal): string {
  const { allowed_part_ids: parts, allowed_period_start: start, allowed_period_end: end } = principal.data_scope
  if (!parts.length && !start && !end) return '未收窄（全部范围）'
  if (!start && !end) return '全部期间'
  return `${start ?? '不限'} – ${end ?? '不限'}`
}
