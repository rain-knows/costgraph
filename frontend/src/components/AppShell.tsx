import { useEffect, useState } from 'react'
import { Bot, Database, FileBarChart2, LayoutDashboard, LogOut, Menu, Moon, ScrollText, Server, Sun, Upload, Users, X } from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import type { HealthStatus } from '../api/agent'
import { getMe, type MeSummary, type PermissionId } from '../api/me'
import { ApiError, apiRequest } from '../api/http'
import { clearAccessToken } from '../auth/tokenStore'
import { hasPermission, requiredPermissionFor } from '../auth/permissions'
import { cn } from '../lib/utils'
import { Breadcrumbs } from './Breadcrumbs'
import { PeriodPicker } from './PeriodPicker'
import { IconButton, StatePanel } from './ui'

const nav = [
  { to: '/', label: '成本总览', icon: LayoutDashboard, permission: 'cost_data.read' as PermissionId },
  { to: '/cost-data', label: '成本数据', icon: Database, permission: 'cost_data.read' as PermissionId },
  { to: '/ai', label: 'AI 分析', icon: Bot, permission: 'cost_data.read' as PermissionId },
  { to: '/reports', label: '报表中心', icon: FileBarChart2, permission: 'cost_data.read' as PermissionId },
  { to: '/data-admin', label: '数据治理', icon: Database, permission: 'data_management.read' as PermissionId },
  { to: '/data-admin/imports', label: '导入任务', icon: Upload, permission: 'data_management.read' as PermissionId },
  { to: '/iam', label: '身份与权限', icon: Users, permission: 'iam.manage' as PermissionId },
  { to: '/audit', label: '审计日志', icon: ScrollText, permission: 'audit.read' as PermissionId },
]

export type AppOutletContext = { period: string; me: MeSummary | null; meLoading: boolean; meError: boolean }

function Brand() {
  return <div className="flex h-12 items-center gap-3 border-b border-[var(--border-soft)] px-4">
    <div className="grid h-6 w-6 grid-cols-3 gap-0.5" aria-hidden="true">{Array.from({ length: 9 }).map((_, index) => <span key={index} className={cn('bg-[var(--fg)]', index === 4 && 'bg-[var(--accent)]')} />)}</div>
    <div className="leading-none"><strong className="text-base font-semibold">CostGraph</strong><span className="ml-2 font-mono text-[10px] text-[var(--muted)]">成本中台</span></div>
  </div>
}

/** 权限摘要就绪后才显示导航，避免加载期间暴露不可访问的入口。 */
function visibleNav(me: MeSummary | null, meLoading: boolean) {
  if (meLoading || !me) return []
  return nav.filter((item) => hasPermission(me.permissions, item.permission))
}

function Sidebar({ period, items, mobile = false, onClose }: { period: string; items: typeof nav; mobile?: boolean; onClose?: () => void }) {
  return <aside className={cn('fixed inset-y-0 left-0 z-40 w-60 border-r border-[var(--border-soft)] bg-[var(--bg)]', mobile ? 'shadow-[var(--elev-raised)]' : 'desktop-nav')}>
    <Brand />
    {mobile ? <IconButton label="关闭导航" onClick={onClose} className="absolute right-0 top-0"><X className="h-5 w-5" /></IconButton> : null}
    <nav className="py-4" aria-label="一级导航">{items.map(({ to, label, icon: Icon }) => <NavLink key={to} to={`${to}?period=${period}`} end={to === '/' || to === '/data-admin'} onClick={onClose} className={({ isActive }) => cn('relative flex h-12 items-center gap-3 px-4 text-sm text-[var(--fg-2)] hover:bg-[var(--surface)] hover:text-[var(--fg)]', isActive && 'bg-[var(--surface)] text-[var(--fg)] before:absolute before:inset-y-0 before:left-0 before:w-1 before:bg-[var(--accent)]')}><Icon className="h-5 w-5" /><span>{label}</span></NavLink>)}</nav>
    <div className="absolute inset-x-4 bottom-4 border-t border-[var(--border-soft)] pt-4">
      <p className="label">计算边界</p><p className="mt-1 text-xs leading-5 text-[var(--fg-2)]">金额、公式与数据范围由服务端验证</p>
    </div>
  </aside>
}

export function AppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const [theme, setTheme] = useState(() => localStorage.getItem('costgraph-theme') ?? 'light')
  const [mobileOpen, setMobileOpen] = useState(false)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [me, setMe] = useState<MeSummary | null>(null)
  const [meLoading, setMeLoading] = useState(true)
  const [meError, setMeError] = useState(false)
  const [meErrorStatus, setMeErrorStatus] = useState<number | null>(null)
  const [meReload, setMeReload] = useState(0)
  const params = new URLSearchParams(location.search)
  const requestedPeriod = params.get('period') ?? sessionStorage.getItem('costgraph-period') ?? import.meta.env.VITE_DEFAULT_PERIOD ?? '2026-06'
  const period = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedPeriod) ? requestedPeriod : '2026-06'

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('costgraph-theme', theme)
  }, [theme])

  useEffect(() => setMobileOpen(false), [location.pathname])

  useEffect(() => {
    const controller = new AbortController()
    setMeLoading(true)
    setMeError(false)
    getMe(controller.signal)
      .then((value) => { setMe(value); setMeError(false); setMeErrorStatus(null) })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setMe(null)
        setMeError(true)
        setMeErrorStatus(reason instanceof ApiError ? reason.status : 500)
      })
      .finally(() => { if (!controller.signal.aborted) setMeLoading(false) })
    return () => controller.abort()
  }, [meReload])

  useEffect(() => {
    let disposed = false
    const load = () => void apiRequest<HealthStatus>('/api/health').then((value) => { if (!disposed) setHealth(value) }).catch(() => { if (!disposed) setHealth(null) })
    load()
    const timer = window.setInterval(load, 30_000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [])

  function changePeriod(nextPeriod: string) {
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(nextPeriod)) return
    sessionStorage.setItem('costgraph-period', nextPeriod)
    const next = new URLSearchParams(location.search)
    next.set('period', nextPeriod)
    next.delete('page')
    navigate({ pathname: location.pathname, search: next.toString() }, { replace: true })
  }

  function signOut() {
    clearAccessToken()
    setMe(null)
    setMobileOpen(false)
  }

  const items = visibleNav(me, meLoading)
  const runtimeReady = health?.runtime_ready === true
  const requiredPermission = requiredPermissionFor(location.pathname)
  const outletContext = { period, me, meLoading, meError } satisfies AppOutletContext
  const routeState: { state: 'loading' | 'unavailable' | 'unauthorized' | 'error'; title: string; detail: string } | null = requiredPermission === undefined
    ? null
    : meLoading
      ? { state: 'loading', title: '正在读取当前主体权限', detail: '正在请求 /api/me 以确定可访问的页面与导航。' }
      : meError
        ? meErrorStatus === 401 || meErrorStatus === 403
          ? { state: 'unauthorized', title: meErrorStatus === 401 ? '登录状态已失效' : '无法确认当前主体权限', detail: meErrorStatus === 401 ? '请重新登录后继续。' : '身份服务拒绝读取当前主体信息。' }
          : meErrorStatus === 503
            ? { state: 'unavailable', title: '身份服务暂不可用', detail: '权限摘要服务暂时不可用，请稍后重试。' }
            : { state: 'error', title: '读取当前主体权限失败', detail: '权限摘要请求发生错误，请重新读取。' }
        : hasPermission(me?.permissions, requiredPermission)
          ? null
          : { state: 'unauthorized', title: '无权访问该页面', detail: `当前主体没有 ${requiredPermission} 权限。` }
  return <div className="min-h-screen bg-[var(--bg)] text-[var(--fg)]">
    <Sidebar period={period} items={items} />
    {mobileOpen ? <><button className="fixed inset-0 z-30 bg-black/40" aria-label="关闭导航遮罩" onClick={() => setMobileOpen(false)} /><Sidebar period={period} items={items} mobile onClose={() => setMobileOpen(false)} /></> : null}
    <div className="workspace-main ml-60 min-h-screen">
      <header className="app-toolbar sticky top-0 z-30 flex min-h-12 items-center border-b border-[var(--border-soft)] bg-[var(--bg)]" aria-label="全局工具栏">
        <IconButton label="打开导航" className="min-[1056px]:hidden" onClick={() => setMobileOpen(true)}><Menu className="h-5 w-5" /></IconButton>
        <PeriodPicker period={period} onChange={changePeriod} />
        <Breadcrumbs pathname={location.pathname} period={period} />
        <div className="ml-auto flex h-full items-center gap-2 border-l border-[var(--border-soft)] px-3 text-xs" aria-label={runtimeReady ? 'Runtime 就绪' : health ? 'Runtime 未就绪' : 'Runtime 离线'} title={runtimeReady ? 'Runtime 就绪' : health ? 'Runtime 未就绪' : 'Runtime 离线'}><Server className="h-4 w-4" /><span className={cn('h-2 w-2 rounded-full', runtimeReady ? 'bg-[var(--success)]' : 'bg-[var(--danger)]')} /><span className="mobile-hide">{runtimeReady ? 'Runtime 就绪' : health ? 'Runtime 未就绪' : 'Runtime 离线'}</span></div>
        <div className="flex h-full max-w-56 items-center gap-2 border-l border-[var(--border-soft)] px-3">
          <span className="truncate font-mono text-xs text-[var(--fg-2)]" title={me?.principal_id ?? '未读取主体'}>{me?.principal_id ?? '未读取主体'}</span>
          {me?.roles.length ? <span className="mobile-hide truncate font-mono text-[10px] text-[var(--muted)]" title={me.roles.join('、')}>{me.roles.join('·')}</span> : null}
          <IconButton label="退出登录" onClick={signOut}><LogOut className="h-4 w-4" /></IconButton>
        </div>
        <IconButton label={theme === 'light' ? '切换深色主题' : '切换浅色主题'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon className="h-5 w-5" /> : <Sun className="h-5 w-5" />}</IconButton>
      </header>
      <main>{routeState
        ? <StatePanel state={routeState.state} title={routeState.title} detail={routeState.detail} onRetry={routeState.state === 'unavailable' || routeState.state === 'error' ? () => { setMeError(false); setMeReload((value) => value + 1) } : undefined} />
        : <Outlet context={outletContext} />}</main>
    </div>
  </div>
}
