import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { PeriodPicker, shiftPeriod } from './PeriodPicker'

describe('shiftPeriod', () => {
  it('crosses year boundaries', () => {
    expect(shiftPeriod('2026-01', -1)).toBe('2025-12')
    expect(shiftPeriod('2026-12', 1)).toBe('2027-01')
  })
})

describe('PeriodPicker', () => {
  it('supports adjacent month shortcuts and explicit month selection', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<PeriodPicker period="2026-06" onChange={onChange} />)

    await user.click(screen.getByRole('button', { name: '下一个月' }))
    expect(onChange).toHaveBeenLastCalledWith('2026-07')

    await user.click(screen.getByRole('button', { name: '选择成本期间，当前为 2026年06月' }))
    expect(screen.getByRole('dialog', { name: '选择成本期间' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '8月' }))
    expect(onChange).toHaveBeenLastCalledWith('2026-08')
    expect(screen.queryByRole('dialog', { name: '选择成本期间' })).not.toBeInTheDocument()
  })

  it('moves the visible year without changing the period until a month is selected', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<PeriodPicker period="2026-06" onChange={onChange} />)

    await user.click(screen.getByRole('button', { name: '选择成本期间，当前为 2026年06月' }))
    await user.click(screen.getByRole('button', { name: '上一年' }))
    expect(onChange).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '12月' }))
    expect(onChange).toHaveBeenCalledWith('2025-12')
  })

  it('returns to the selected year when reopened', async () => {
    const user = userEvent.setup()
    render(<PeriodPicker period="2026-06" onChange={vi.fn()} />)

    const trigger = screen.getByRole('button', { name: '选择成本期间，当前为 2026年06月' })
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: '上一年' }))
    await user.keyboard('{Escape}')
    await user.click(trigger)

    expect(screen.getByText('2026 年')).toBeVisible()
  })
})
