import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CostTree } from './CostTree'
import { finishedBatchDetail } from '../test/costFixtures'

describe('CostTree', () => {
  it('renders the root trace and marks a shared node as a reference', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<CostTree graph={finishedBatchDetail.trace} selectedEventId="event-root" onSelect={onSelect} />)
    expect(screen.getByText('成品外壳总成')).toBeInTheDocument()
    expect(screen.getAllByText('ABS 原料').length).toBeGreaterThan(1)
    expect(screen.getByText('引用')).toBeInTheDocument()
    await user.click(screen.getAllByRole('treeitem', { name: /ABS 原料/ })[0])
    expect(onSelect).toHaveBeenCalledWith('event-material')
  })

  it('supports keyboard expansion and selection', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<CostTree graph={finishedBatchDetail.trace} selectedEventId="event-root" onSelect={onSelect} />)
    const root = screen.getByRole('treeitem', { name: /成品外壳总成/ })
    root.focus()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith('event-root')
    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('treeitem', { name: /成品外壳总成/ })).toHaveAttribute('aria-expanded', 'false')
  })
})
