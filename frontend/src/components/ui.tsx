import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { AlertCircle, Database, LoaderCircle } from 'lucide-react'
import { cn } from '../lib/utils'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}

export function Button({ className, variant = 'secondary', size = 'md', ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-2 border text-sm font-normal transition-colors duration-fast',
        'focus-visible:relative disabled:opacity-50',
        size === 'md' ? 'h-12 px-4' : 'h-10 px-3',
        variant === 'primary' && 'border-transparent bg-[var(--accent)] text-[var(--accent-on)] hover:bg-[var(--accent-hover)] active:bg-[var(--accent-active)]',
        variant === 'secondary' && 'border-[var(--fg)] bg-[var(--fg)] text-[var(--bg)] hover:bg-[var(--fg-2)]',
        variant === 'ghost' && 'border-transparent bg-transparent text-[var(--fg)] hover:bg-[var(--surface)]',
        variant === 'danger' && 'border-transparent bg-[var(--danger)] text-[var(--accent-on)] hover:brightness-90',
        className,
      )}
      {...props}
    />
  )
}

export function IconButton({ label, className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return <button aria-label={label} title={label} className={cn('icon-button', className)} {...props} />
}

export function Badge({ tone = 'neutral', children, className }: { tone?: 'neutral' | 'info' | 'success' | 'warning' | 'danger'; children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex min-h-6 items-center gap-1 rounded-full px-2 font-mono text-xs',
        tone === 'neutral' && 'bg-[var(--surface)] text-[var(--fg-2)]',
        tone === 'info' && 'bg-[var(--surface-warm)] text-[var(--accent)]',
        tone === 'success' && 'bg-[color-mix(in_oklch,var(--success)_12%,transparent)] text-[var(--success)]',
        tone === 'warning' && 'bg-[color-mix(in_oklch,var(--warn)_18%,transparent)] text-[var(--fg)]',
        tone === 'danger' && 'bg-[color-mix(in_oklch,var(--danger)_12%,transparent)] text-[var(--danger)]',
        className,
      )}
    >{children}</span>
  )
}

export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (value: T) => void; options: Array<{ value: T; label: string }>; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex h-10 bg-[var(--surface)] p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn('min-w-16 px-3 text-sm transition-colors', value === option.value ? 'bg-[var(--fg)] text-[var(--bg)]' : 'text-[var(--fg-2)] hover:bg-[var(--border-soft)] hover:text-[var(--fg)]')}
        >{option.label}</button>
      ))}
    </div>
  )
}

export function PageHeader({ title, actions }: { title: string; actions?: ReactNode }) {
  return (
    <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-[var(--border-soft)] px-6 py-2 page-pad" data-od-id="page-header">
      <h1 className="page-title min-w-0 text-balance">{title}</h1>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

export type RequestStateKind = 'loading' | 'empty' | 'error' | 'unavailable' | 'unauthorized'

const STATE_PRESENTATION: Record<RequestStateKind, { title: string; detail: string }> = {
  loading: { title: '正在读取服务端数据', detail: '正在向服务端请求当前视图。' },
  empty: { title: '当前条件下没有数据', detail: '当前没有符合条件的记录。' },
  error: { title: '数据读取失败', detail: '服务端返回了错误，请重新读取。' },
  unavailable: { title: '服务暂不可用', detail: '依赖的服务当前不可用，请稍后重试。' },
  unauthorized: { title: '无权访问该页面', detail: '当前主体没有访问该页面所需的权限。' },
}

/** 五态统一呈现：loading / empty / error / unavailable / unauthorized。 */
export function StatePanel({ state, title, detail, onRetry, compact = false }: { state: RequestStateKind; title?: string; detail?: string; onRetry?: () => void; compact?: boolean }) {
  const presentation = STATE_PRESENTATION[state]
  const Icon = state === 'loading' ? LoaderCircle : state === 'empty' ? Database : AlertCircle
  const role = state === 'error' || state === 'unavailable' || state === 'unauthorized' ? 'alert' : 'status'
  return <section className={cn('grid place-items-center px-6', compact ? 'min-h-64' : 'min-h-[420px]')} data-state={state} role={role}>
    <div className="max-w-md text-center">
      <Icon className={cn('mx-auto mb-4 h-8 w-8 text-[var(--fg-2)]', state === 'loading' && 'animate-spin')} aria-hidden="true" />
      <h2 className="text-xl font-normal">{title ?? presentation.title}</h2>
      <p className="mt-2 text-sm text-[var(--fg-2)]">{detail ?? presentation.detail}</p>
      {onRetry && state !== 'loading' ? <Button className="mt-5" onClick={onRetry}>重新读取</Button> : null}
    </div>
  </section>
}

export function RequestState({ loading, error, empty, onRetry, children, unavailable = false, unauthorized = false, title, detail }: { loading: boolean; error: string; empty: boolean; onRetry?: () => void; children: ReactNode; unavailable?: boolean; unauthorized?: boolean; title?: string; detail?: string }) {
  if (!loading && !error && !empty && !unavailable && !unauthorized) return <>{children}</>
  const state: RequestStateKind = unauthorized ? 'unauthorized' : unavailable ? 'unavailable' : error ? 'error' : loading ? 'loading' : 'empty'
  return <StatePanel state={state} title={title} detail={detail ?? (error || undefined)} onRetry={onRetry} />
}
