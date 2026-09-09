import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { RequestState } from './ui'

it('renders loading, empty, and permission error states with retry', async () => {
  const onRetry = vi.fn()
  const view = render(<RequestState loading error="" empty={false}>content</RequestState>)

  expect(screen.getByText('正在读取已发布数据')).toBeInTheDocument()

  view.rerender(<RequestState loading={false} error="" empty>content</RequestState>)
  expect(screen.getByText('当前条件下没有数据')).toBeInTheDocument()

  view.rerender(
    <RequestState loading={false} error="无权限查看当前数据范围" empty={false} onRetry={onRetry}>
      content
    </RequestState>,
  )
  expect(screen.getByRole('alert')).toHaveTextContent('无权限查看当前数据范围')
  await userEvent.click(screen.getByRole('button', { name: '重新读取' }))
  expect(onRetry).toHaveBeenCalledOnce()
})
