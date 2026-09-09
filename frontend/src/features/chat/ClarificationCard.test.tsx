import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ClarificationCard } from './ClarificationCard'

describe('ClarificationCard', () => {
  it('returns the selected value with its clarification id', async () => {
    const onSelect = vi.fn()
    render(<ClarificationCard clarification={{ id: 'clarify-1', question: '请选择期间', missing_slots: ['period'], options: [{ label: '2026 年 6 月', value: '2026-06' }] }} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: '2026 年 6 月' }))
    expect(onSelect).toHaveBeenCalledWith('2026-06', 'clarify-1')
  })

  it('does not submit a stale clarification', async () => {
    const onSelect = vi.fn()
    render(<ClarificationCard disabled clarification={{ id: 'old', question: '请选择产品', missing_slots: ['product'], options: [{ label: '产品A', value: '产品A' }] }} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: '产品A' }))
    expect(onSelect).not.toHaveBeenCalled()
  })
})
