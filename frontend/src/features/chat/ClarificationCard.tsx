import { LockKeyhole } from 'lucide-react'
import type { Clarification } from '../../api/agent'
import { Badge, Button } from '../../components/ui'

export function ClarificationCard({ clarification, disabled, onSelect }: { clarification: Clarification; disabled?: boolean; onSelect: (value: string, clarificationId: string) => void }) {
  return (
    <section className="border border-[var(--border)]" aria-label="需要补充的信息">
      <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] bg-[var(--surface)] px-4 py-3">
        <div><h3 className="font-semibold">需要补充信息</h3><p className="mt-1 text-xs text-[var(--fg-2)]">{clarification.question}</p></div>
        <Badge tone="warning"><LockKeyhole className="h-3.5 w-3.5" />读取已阻止</Badge>
      </div>
      {clarification.missing_slots.length ? <p className="px-4 pt-3 text-xs text-[var(--muted)]">缺少：{clarification.missing_slots.join('、')}</p> : null}
      {clarification.options.length ? <div className="flex flex-wrap gap-2 p-4">{clarification.options.map((option) => <Button key={`${option.label}-${option.value}`} disabled={disabled} size="sm" variant="ghost" onClick={() => onSelect(option.value, clarification.id)}>{option.label}</Button>)}</div> : <p className="p-4 text-xs text-[var(--fg-2)]">请在下方输入产品和期间，例如“产品A，2026-06”。</p>}
    </section>
  )
}
