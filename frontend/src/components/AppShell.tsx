import { useEffect, useState } from 'react'
import { Bot, Database, FileBarChart2, LayoutDashboard, Menu, Moon, Server, Sun, X } from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import type { HealthStatus } from '../api/agent'
import { apiRequest } from '../api/http'
import { cn } from '../lib/utils'
import { IconButton } from './ui'

const nav = [
  { to: '/', label: '成本总览', icon: LayoutDashboard },
  { to: '/cost-data', label: '成本数据', icon: Database },
  { to: '/ai', label: 'AI 分析', icon: Bot },
  { to: '/reports', label: '报表中心', icon: FileBarChart2 },
]

export type AppOutletContext = { period: string }

function Brand() {
  return <div className="flex h-12 items-center gap-3 border-b border-[var(--border-soft)] px-4">
    <div className="grid h-6 w-6 grid-cols-3 gap-0.5" aria-hidden="true">{Array.from({ length: 9 }).map((_, index) => <span key={index} className={cn('bg-[var(--fg)]', index === 4 && 'bg-[var(--accent)]')} />)}</div>
    <div className="leading-none"><strong className="text-base font-semibold">CostGraph</strong><span className="ml-2 font-mono text-[10px] text-[var(--muted)]">成本中台</span></div>
  </div>
}

function Sidebar({ period, mobile = false, onClose }: { period: string; mobile?: boolean; onClose?: () => void }) {
  return <aside className={cn('fixed inset-y-0 left-0 z-40 w-60 border-r border-[var(--border-soft)] bg-[var(--bg)]', mobile ? 'shadow-[var(--elev-raised)]' : 'desktop-nav')}>
    <Brand />
    {mobile ? <IconButton label="关闭导航" onClick={onClose} className="absolute right-0 top-0"><X className="h-5 w-5" /></IconButton> : null}
    <nav className="py-4" aria-label="一级导航">{nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={`${to}?period=${period}`} end={to === '/'} onClick={onClose} className={({ isActive }) => cn('relative flex h-12 items-center gap-3 px-4 text-sm text-[var(--fg-2)] hover:bg-[var(--surface)] hover:text-[var(--fg)]', isActive && 'bg-[var(--surface)] text-[var(--fg)] before:absolute before:inset-y-0 before:left-0 before:w-1 before:bg-[var(--accent)]')}><Icon className="h-5 w-5" /><span>{label}</span></NavLink>)}</nav>
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
  const params = new URLSearchParams(location.search)
  const requestedPeriod = params.get('period') ?? sessionStorage.getItem('costgraph-period') ?? import.meta.env.VITE_DEFAULT_PERIOD ?? '2026-06'
  const period = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedPeriod) ? requestedPeriod : '2026-06'

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('costgraph-theme', theme)
  }, [theme])

  useEffect(() => setMobileOpen(false), [location.pathname])

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

  const runtimeReady = health?.runtime_ready === true
  return <div className="min-h-screen bg-[var(--bg)] text-[var(--fg)]">
    <Sidebar period={period} />
    {mobileOpen ? <><button className="fixed inset-0 z-30 bg-black/40" aria-label="关闭导航遮罩" onClick={() => setMobileOpen(false)} /><Sidebar period={period} mobile onClose={() => setMobileOpen(false)} /></> : null}
    <div className="workspace-main ml-60 min-h-screen">
      <header className="sticky top-0 z-30 flex h-12 items-center border-b border-[var(--border-soft)] bg-[var(--bg)]" aria-label="全局工具栏">
        <IconButton label="打开导航" className="min-[1056px]:hidden" onClick={() => setMobileOpen(true)}><Menu className="h-5 w-5" /></IconButton>
        <label className="flex h-full items-center gap-2 border-r border-[var(--border-soft)] px-3"><span className="label">期间</span><input aria-label="成本期间" type="month" value={period} onChange={(event) => changePeriod(event.target.value)} className="bg-transparent font-mono text-sm" /></label>
        <div className="ml-auto flex h-full items-center gap-2 border-l border-[var(--border-soft)] px-3 text-xs" aria-label={runtimeReady ? 'Runtime 就绪' : health ? 'Runtime 未就绪' : 'Runtime 离线'} title={runtimeReady ? 'Runtime 就绪' : health ? 'Runtime 未就绪' : 'Runtime 离线'}><Server className="h-4 w-4" /><span className={cn('h-2 w-2 rounded-full', runtimeReady ? 'bg-[var(--success)]' : 'bg-[var(--danger)]')} /><span className="mobile-hide">{runtimeReady ? 'Runtime 就绪' : health ? 'Runtime 未就绪' : 'Runtime 离线'}</span></div>
        <IconButton label={theme === 'light' ? '切换深色主题' : '切换浅色主题'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon className="h-5 w-5" /> : <Sun className="h-5 w-5" />}</IconButton>
      </header>
      <main><Outlet context={{ period } satisfies AppOutletContext} /></main>
    </div>
  </div>
}
