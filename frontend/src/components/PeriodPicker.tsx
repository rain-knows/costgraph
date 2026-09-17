import { useEffect, useId, useRef, useState } from 'react'
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '../lib/utils'

const monthLabels = Array.from({ length: 12 }, (_, index) => `${index + 1}月`)

type PeriodPickerProps = {
  period: string
  onChange: (period: string) => void
}

export function PeriodPicker({ period, onChange }: PeriodPickerProps) {
  const [year, month] = period.split('-').map(Number)
  const [open, setOpen] = useState(false)
  const [viewYear, setViewYear] = useState(year)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  useEffect(() => setViewYear(year), [year])

  useEffect(() => {
    if (!open) return

    function closeOnOutsideClick(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      setOpen(false)
      triggerRef.current?.focus()
    }

    document.addEventListener('pointerdown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  function selectMonth(nextMonth: number) {
    onChange(toPeriod(viewYear, nextMonth))
    setOpen(false)
    triggerRef.current?.focus()
  }

  function selectCurrentMonth() {
    const now = new Date()
    onChange(toPeriod(now.getFullYear(), now.getMonth() + 1))
    setOpen(false)
    triggerRef.current?.focus()
  }

  return <div ref={rootRef} className="relative flex h-12 shrink-0 items-center border-r border-[var(--border-soft)]" aria-label="成本期间选择">
    <span className="label ml-3 mobile-hide">期间</span>
    <button type="button" className="period-step grid h-12 w-9 place-items-center hover:bg-[var(--surface)]" aria-label="上一个月" onClick={() => onChange(shiftPeriod(period, -1))}>
      <ChevronLeft className="h-4 w-4" />
    </button>
    <button
      ref={triggerRef}
      type="button"
      className="flex h-12 min-w-[7.75rem] items-center justify-center gap-2 px-2 font-mono text-sm hover:bg-[var(--surface)]"
      aria-label={`选择成本期间，当前为 ${formatPeriodLabel(period)}`}
      aria-expanded={open}
      aria-controls={panelId}
      onClick={() => {
        if (!open) setViewYear(year)
        setOpen((value) => !value)
      }}
    >
      <CalendarDays className="h-4 w-4 text-[var(--fg-2)]" />
      <span>{formatPeriodLabel(period)}</span>
      <ChevronDown className={cn('h-3.5 w-3.5 text-[var(--muted)] transition-transform', open && 'rotate-180')} />
    </button>
    <button type="button" className="period-step grid h-12 w-9 place-items-center hover:bg-[var(--surface)]" aria-label="下一个月" onClick={() => onChange(shiftPeriod(period, 1))}>
      <ChevronRight className="h-4 w-4" />
    </button>

    {open ? <section id={panelId} role="dialog" aria-label="选择成本期间" className="period-panel absolute left-0 top-full z-50 w-72 border border-[var(--border)] bg-[var(--bg)] shadow-[var(--elev-raised)]">
      <header className="flex h-12 items-center border-b border-[var(--border-soft)]">
        <button type="button" disabled={viewYear <= 1} className="grid h-12 w-12 place-items-center hover:bg-[var(--surface)]" aria-label="上一年" onClick={() => setViewYear((value) => value - 1)}><ChevronLeft className="h-4 w-4" /></button>
        <strong className="num flex-1 text-center text-sm font-medium">{viewYear} 年</strong>
        <button type="button" disabled={viewYear >= 9999} className="grid h-12 w-12 place-items-center hover:bg-[var(--surface)]" aria-label="下一年" onClick={() => setViewYear((value) => value + 1)}><ChevronRight className="h-4 w-4" /></button>
      </header>
      <div className="grid grid-cols-4 gap-px bg-[var(--border-soft)]" role="group" aria-label={`${viewYear} 年月份`}>
        {monthLabels.map((label, index) => {
          const value = index + 1
          const selected = viewYear === year && value === month
          return <button
            key={label}
            type="button"
            aria-pressed={selected}
            className={cn('h-11 text-sm', selected ? 'bg-[var(--accent)] font-semibold text-[var(--accent-on)] hover:bg-[var(--accent-hover)]' : 'bg-[var(--bg)] hover:bg-[var(--surface)]')}
            onClick={() => selectMonth(value)}
          >{label}</button>
        })}
      </div>
      <button type="button" className="h-10 w-full border-t border-[var(--border-soft)] text-xs text-[var(--fg-2)] hover:bg-[var(--surface)] hover:text-[var(--fg)]" onClick={selectCurrentMonth}>回到本月</button>
    </section> : null}
  </div>
}

export function shiftPeriod(period: string, delta: number) {
  const [year, month] = period.split('-').map(Number)
  const totalMonths = year * 12 + month - 1 + delta
  const nextYear = Math.floor(totalMonths / 12)
  if (nextYear < 1 || nextYear > 9999) return period
  return toPeriod(nextYear, totalMonths % 12 + 1)
}

function toPeriod(year: number, month: number) {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`
}

function formatPeriodLabel(period: string) {
  const [year, month] = period.split('-')
  return `${year}年${month}月`
}
